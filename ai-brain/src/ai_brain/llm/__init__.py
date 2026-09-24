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
    from ai_brain.discovery import OllamaFinder

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

# Skip reasons that mean the chain is working, not failing: the expert floor
# holding cloud quota back for the brain, and the per-minute windows. An
# expert falling through to the LAN on every round is the design, so these
# log at debug. See ``ProviderChain._log_skip``.
_BY_DESIGN_SKIPS = frozenset({"priority", "rpm", "tpm"})

# Every provider prefix ``from_settings`` knows how to build. Exported so the
# supervisor can reject a typo'd LLM_CHAIN at startup rather than at the first
# cycle, and so the two lists cannot drift apart.
PROVIDER_PREFIXES: frozenset[str] = frozenset({"lan", "gemini", "ollama", "fake"})


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
    # Which model produced an assistant turn. The chain can change model
    # mid-conversation -- a LAN host goes away, a key runs out -- and a
    # provider handed another model's turn cannot always echo it back
    # verbatim. Empty means "not from a provider", e.g. a turn a test built.
    model: str = ""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    # Prompt tokens served from the provider's cache. No provider fills it yet.
    cached_tokens: int = 0


@dataclass(frozen=True)
class Reply:
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    model: str
    thought_signature: str = ""
    # What the model reasoned before answering, when it says so out loud.
    # Gemini returns a *summary* of its thinking (only when asked with
    # ``includeThoughts``, and not on every turn); Ollama's thinking models
    # return the real thing in ``message.thinking``. Unlike
    # ``thought_signature`` this is never echoed back to a provider -- it is
    # for the reader, not the next request.
    thinking: str = ""
    # The chain key that answered (``provider.key``, e.g.
    # ``gemini:gemini-3.8-flash`` or ``lan:qwen3-coder:30b``) -- the same
    # shape as an LLM_CHAIN entry, unlike ``model`` which a provider is free
    # to decorate (the lan: provider appends `` @ host``). This is what
    # CYCLE_MAX_ROUNDS_BY_MODEL matches against. Empty means "not from a real
    # provider", the same convention ``model`` on a hand-built Message uses.
    key: str = ""


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
    # How many times the chain may call this provider before falling through.
    # Two is the default "one retry on a blip"; the lan provider raises its own.
    max_attempts: int = 2
    # None means "use the chain's own call_timeout_s". A local model on real
    # hardware can legitimately take minutes to answer a big prompt -- nothing
    # like a cloud API's SLA -- so the lan: provider overrides this rather than
    # making every provider in the chain wait as long as the slowest one.
    call_timeout_s: float | None = None

    @abstractmethod
    async def complete(
        self, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> Reply: ...

    def available(self) -> bool:
        """False when the provider has nothing to dial at all.

        Distinct from being out of quota, which is the ledger's answer and is
        worth logging: a provider whose host has not been found yet is not a
        problem, it is simply not there this minute.
        """
        return True


# A machine in the house is not a metered API. It still needs a bucket -- the
# ledger refuses a key it has never heard of -- so it gets one nobody can
# exhaust, and the free-tier arithmetic stays about the free tier.
UNMETERED = Limits(rpm=10_000, tpm=100_000_000, rpd=1_000_000)

# gemini-3.8-flash's free-tier RPD is nowhere near the RPM/TPM/RPD every other
# metered key gets from settings -- Google's own docs no longer publish the
# number, so this is measured, not guessed: on 2026-09-22 it answered 12
# calls, then every call for the rest of the day was a 429. One below that
# observed ceiling, so the ledger's own rpd check blocks the key before a
# cycle ever has to spend a real request discovering it is gone (see
# EXHAUSTED_AFTER_429 in ledger.py -- without this, three separate loops each
# burn one real 429 finding this out the hard way, every single day). A key
# not listed here keeps the env-configured tier, unchanged.
KEY_RPD_OVERRIDES: dict[str, int] = {
    "gemini:gemini-3.8-flash": 11,
}


def limits_from_settings(settings: Settings) -> dict[str, Limits]:
    """One budget per chain key: the env-configured tier, except for local
    hosts (unmetered) and any key in KEY_RPD_OVERRIDES (a lower, measured rpd)."""
    limits = Limits(rpm=settings.rpm, tpm=settings.tpm, rpd=settings.rpd)
    out: dict[str, Limits] = {}
    for key in settings.llm_chain:
        if key.startswith("lan:"):
            out[key] = UNMETERED
        elif key in KEY_RPD_OVERRIDES:
            out[key] = Limits(rpm=limits.rpm, tpm=limits.tpm, rpd=KEY_RPD_OVERRIDES[key])
        else:
            out[key] = limits
    return out


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
        # Set by ``from_settings`` for each ``lan:`` entry in the chain -- one
        # finder per LAN model, since a chain may try more than one local
        # model before falling back to the metered cloud (e.g. a bigger model
        # on the desktop first, a smaller one second). The supervisor sweeps
        # all of them on its own schedule; nothing else in the chain touches
        # this list.
        self.lan_finders: list[OllamaFinder] = []
        # The last skip reason logged per key, so a condition that holds all
        # day says so once instead of once per round. See ``_log_skip``.
        self._skipped: dict[str, str] = {}

    @staticmethod
    def from_settings(
        settings: Settings,
        ledger: Ledger,
        call_timeout_s: float | None = None,
    ) -> ProviderChain:
        providers: list[Provider] = []
        lan_finders: list[OllamaFinder] = []
        for entry in settings.llm_chain:
            name, _, model = entry.partition(":")
            if name == "lan":
                # A model on some machine in the house. Which machine is not
                # known until a scan finds one, so the provider carries a
                # finder rather than an address. Each lan: entry gets its own
                # finder even when several share a model name, since two
                # entries might resolve to different machines.
                from ai_brain.discovery import OllamaFinder, subnets_for
                from ai_brain.llm.lan import LanOllamaProvider

                lan_finder = OllamaFinder(
                    model, subnets_for(settings.lan_subnets, settings.ha_url)
                )
                lan_finders.append(lan_finder)
                providers.append(
                    LanOllamaProvider(lan_finder, num_ctx=settings.lan_ollama_num_ctx)
                )
            elif name == "gemini":
                # Imported lazily: the chain is usable (and testable) without
                # the concrete provider module or its API client.
                from ai_brain.llm.gemini import GeminiProvider

                providers.append(
                    GeminiProvider(
                        model,
                        settings.gemini_api_key,
                        thinking_budget=settings.thinking_budget,
                    )
                )
            elif name == "ollama":
                # Imported lazily to match the gemini branch above.
                from ai_brain.llm.ollama import OllamaProvider

                providers.append(
                    OllamaProvider(
                        model, settings.ollama_url, num_ctx=settings.ollama_num_ctx
                    )
                )
            elif name == "fake":
                from ai_brain.llm.fake import FakeProvider

                providers.append(FakeProvider(entry, script=[]))
            else:
                raise ValueError(f"provider '{name}' not implemented")
        if call_timeout_s is None:
            call_timeout_s = getattr(settings, "call_timeout_s", DEFAULT_CALL_TIMEOUT_S)
        chain = ProviderChain(providers, ledger, call_timeout_s=call_timeout_s)
        chain.lan_finders = lan_finders
        return chain

    def _log_skip(self, key: str, reason: str) -> None:
        """Say a key was skipped, at a level that matches what it means.

        Two different things used to share one WARNING. ``priority`` (the
        expert floor holding cloud quota back for the brain) and ``rpm``/
        ``tpm`` are the chain doing exactly its job: an expert is *supposed*
        to fall through to the LAN, on every round of every cycle, all day.
        Warning about it buried the reasons that are actually worth reading.

        The rest -- ``exhausted``, ``rpd``, ``disabled``, ``cooldown`` -- do
        deserve a warning, but only when they change: a key exhausted at noon
        is still exhausted at midnight, and it does not need to say so once
        per round for twelve hours. Repeats drop to debug until the reason
        changes or the key recovers.
        """
        if reason in _BY_DESIGN_SKIPS:
            log.debug("skipping %s: %s", key, reason)
            return
        if self._skipped.get(key) == reason:
            log.debug("still skipping %s: %s", key, reason)
            return
        self._skipped[key] = reason
        log.warning("skipping %s: %s", key, reason)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        priority: Priority,
        agent: str = "",
    ) -> Reply:
        earliest: float | None = None
        # Purely for the log line below: one chain is shared across every
        # AgentLoop (brain and each expert), so nothing here otherwise says
        # whose cycle this call belongs to. Optional and unused elsewhere --
        # a caller that has no loop to name (a test, a script) just gets an
        # unlabelled line, exactly like before this parameter existed.
        who = f"{agent}: " if agent else ""

        def remember(retry_at: float | None) -> None:
            nonlocal earliest
            if retry_at is not None and (earliest is None or retry_at < earliest):
                earliest = retry_at

        for provider in self.providers:
            key = provider.key
            if not provider.available():
                log.debug("skipping %s: nothing to dial", key)
                continue
            decision = self.ledger.can_spend(key, priority)
            if not decision.allowed:
                self._log_skip(key, decision.reason)
                remember(decision.retry_at)
                continue
            # Spendable again: forget what it was last refused for, so the
            # next refusal is heard even if it is the same reason as before.
            self._skipped.pop(key, None)

            reply = await self._try(provider, messages, tools, max_tokens, priority, remember)
            if reply is not None:
                log.info(
                    "%s%s answered (in=%d out=%d tokens)",
                    who,
                    key,
                    reply.usage.prompt_tokens,
                    reply.usage.completion_tokens,
                )
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
        """One provider's turn: up to ``max_attempts`` of them, then None."""
        key = provider.key
        attempts = max(1, provider.max_attempts)
        timeout_s = provider.call_timeout_s or self.call_timeout_s
        for attempt in range(1, attempts + 1):
            if attempt > 1:
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
                    timeout=timeout_s,
                )
            except TimeoutError:
                # The request was sent and may already have been billed, so
                # charge it before deciding what to do next -- an unrecorded
                # spend makes the remaining-budget metric overstate the truth.
                self.ledger.record(key, TIMEOUT_CHARGE_TOKENS, 0)
                err = ProviderError(f"call exceeded {timeout_s}s", kind="timeout")
                log.warning("%s failed (%s, attempt %d): %s", key, err.kind, attempt, err)
                if attempt < attempts:
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
                if err.kind in RETRYABLE and attempt < attempts:
                    continue
                return None
            else:
                self.ledger.record(key, reply.usage.prompt_tokens, reply.usage.completion_tokens)
                self.ledger.record_ok(key)
                return reply
        return None
