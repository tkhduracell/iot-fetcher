"""Provider-agnostic LLM types and the fallback chain.

Every agent talks to a ``ProviderChain`` rather than to a model API. The chain
walks its providers in order, asking the shared ledger first so a key that is
out of quota is never even dialled, and translating each provider's failure
into the ledger bookkeeping that keeps the rest of the process honest.

Failures are not equal. A missing model (``not_found``) is a config mistake
that will not fix itself, so the key is disabled for a day. A ``rate_limited``
key is fine but busy, so it is backed off. ``server``/``timeout`` are usually
transient, so the same provider gets one more try before we move on. A
``bad_request`` is our own bug -- falling through would just repeat it against
every key in the chain, so it is re-raised immediately.

The per-call timeout lives here rather than around ``complete``. A budget on
the whole chain lets a first provider that hangs eat the fallback's time --
exactly the wrong outcome on a free tier where the first model is the one that
503s. Bounding one call instead means every provider gets its own timeout, and
a timed-out call is *recorded* in the ledger before it is retried: the request
reached Google and may well have been billed, so counting it conservatively is
the only way ``ai_brain_ledger_remaining`` stays honest.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ai_brain.ledger import Ledger, Limits, Priority

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from ai_brain.config import Settings

log = logging.getLogger(__name__)

ErrorKind = Literal["not_found", "rate_limited", "server", "timeout", "bad_request"]

# server/timeout are worth one more shot at the same provider before we burn a
# fallback key on what is probably a blip.
RETRYABLE: frozenset[str] = frozenset({"server", "timeout"})

# Seconds one provider call may take. Also what a timed-out call is charged:
# we never saw a usage block, so we assume the request was as expensive as the
# loop's own max_tokens rather than free.
DEFAULT_CALL_TIMEOUT_S = 60
TIMEOUT_CHARGE_TOKENS = 4000

# Every provider prefix ``from_settings`` knows how to build. Exported so the
# supervisor can reject a typo'd LLM_CHAIN at startup rather than at the first
# cycle, and so the two lists cannot drift apart.
PROVIDER_PREFIXES: frozenset[str] = frozenset({"gemini", "fake"})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict
    # Gemini 3.x thinking models sign the part they hand back; the same string
    # must ride along when we echo that model turn, or the next call is a 400.
    thought_signature: str = ""


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    name: str = ""
    thought_signature: str = ""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class Reply:
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    model: str
    thought_signature: str = ""


class ProviderError(Exception):
    """A provider call failed in a way the chain knows how to classify."""

    def __init__(self, message: str, *, kind: ErrorKind, retry_after_s: float | None = None):
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.retry_after_s: float | None = retry_after_s


class ChainExhausted(Exception):
    """No provider in the chain could serve the request."""

    def __init__(self, retry_at: float | None = None):
        super().__init__(f"all providers exhausted; retry_at={retry_at}")
        self.retry_at = retry_at


class Provider(ABC):
    key: str

    @abstractmethod
    async def complete(
        self, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> Reply: ...


def limits_from_settings(settings: Settings) -> dict[str, Limits]:
    """One budget per chain key, all taken from the same env-configured tier."""
    limits = Limits(rpm=settings.rpm, tpm=settings.tpm, rpd=settings.rpd)
    return {key: limits for key in settings.llm_chain}


class ProviderChain:
    def __init__(
        self,
        providers: list[Provider],
        ledger: Ledger,
        call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
    ):
        self.providers = list(providers)
        self.ledger = ledger
        self.call_timeout_s = call_timeout_s

    @staticmethod
    def from_settings(
        settings: Settings,
        ledger: Ledger,
        call_timeout_s: float | None = None,
    ) -> ProviderChain:
        providers: list[Provider] = []
        for entry in settings.llm_chain:
            name, _, model = entry.partition(":")
            if name == "gemini":
                # Imported lazily: the chain is usable (and testable) without
                # the concrete provider module or its API client.
                from ai_brain.llm.gemini import GeminiProvider

                providers.append(GeminiProvider(model, settings.gemini_api_key))
            elif name == "fake":
                from ai_brain.llm.fake import FakeProvider

                providers.append(FakeProvider(entry, script=[]))
            else:
                raise ValueError(f"provider '{name}' not implemented")
        if call_timeout_s is None:
            call_timeout_s = getattr(settings, "call_timeout_s", DEFAULT_CALL_TIMEOUT_S)
        return ProviderChain(providers, ledger, call_timeout_s=call_timeout_s)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        priority: Priority,
    ) -> Reply:
        earliest: float | None = None

        def remember(retry_at: float | None) -> None:
            nonlocal earliest
            if retry_at is not None and (earliest is None or retry_at < earliest):
                earliest = retry_at

        for provider in self.providers:
            key = provider.key
            decision = self.ledger.can_spend(key, priority)
            if not decision.allowed:
                log.warning("skipping %s: %s", key, decision.reason)
                remember(decision.retry_at)
                continue

            reply = await self._try(provider, messages, tools, max_tokens, priority, remember)
            if reply is not None:
                log.info("%s answered (%d tokens)", key, reply.usage.completion_tokens)
                return reply

        if earliest is None:
            earliest = self.ledger.next_available_at(priority)
        raise ChainExhausted(retry_at=earliest)

    async def _try(
        self,
        provider: Provider,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        priority: Priority,
        remember: Callable[[float | None], None],
    ) -> Reply | None:
        """One provider's turn: up to two attempts, then None to fall through."""
        key = provider.key
        for attempt in (1, 2):
            if attempt == 2:
                # The first attempt spent budget, and on a small free tier that
                # can be the last of it. Retrying anyway sends a request the
                # ledger has already said no to -- and against a provider that
                # is hanging, that is a second full call_timeout_s of the
                # cycle's clock spent on a call that cannot be allowed.
                decision = self.ledger.can_spend(key, priority)
                if not decision.allowed:
                    log.warning("not retrying %s: %s", key, decision.reason)
                    remember(decision.retry_at)
                    return None
            try:
                reply = await asyncio.wait_for(
                    provider.complete(messages, tools, max_tokens),
                    timeout=self.call_timeout_s,
                )
            except TimeoutError:
                # The request was sent and may already have been billed, so
                # charge it before deciding what to do next -- an unrecorded
                # spend makes the remaining-budget metric overstate the truth.
                self.ledger.record(key, TIMEOUT_CHARGE_TOKENS, 0)
                err = ProviderError(
                    f"call exceeded {self.call_timeout_s}s", kind="timeout"
                )
                log.warning("%s failed (%s, attempt %d): %s", key, err.kind, attempt, err)
                if attempt == 1:
                    continue
                return None
            except ProviderError as err:
                if err.kind == "bad_request":
                    # Our payload is wrong; every other key would reject it too.
                    raise
                log.warning("%s failed (%s, attempt %d): %s", key, err.kind, attempt, err)
                if err.kind == "not_found":
                    self.ledger.record_not_found(key)
                    remember(self.ledger.can_spend(key, priority).retry_at)
                    return None
                if err.kind == "rate_limited":
                    self.ledger.record_429(key, err.retry_after_s)
                    remember(self.ledger.can_spend(key, priority).retry_at)
                    return None
                if err.kind in RETRYABLE and attempt == 1:
                    continue
                return None
            else:
                self.ledger.record(key, reply.usage.prompt_tokens, reply.usage.completion_tokens)
                self.ledger.record_ok(key)
                return reply
        return None
