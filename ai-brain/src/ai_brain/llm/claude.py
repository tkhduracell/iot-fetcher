"""Claude over the official ``anthropic`` SDK (Messages API).

Paid for out of the Max plan's monthly API credits rather than a free tier, so
this provider is the one that reports *dollars*: every ``Reply`` carries a
``cost_usd`` priced from the usage block, and the ledger's USD pool paces the
month with it. Tokens alone cannot do that -- a cache read costs a tenth of a
fresh input token here, so a token budget would make a well-cached cycle look
ten times as expensive as it is.

Three details of the API shape the request builder:

* Thinking cannot be switched off on the current models, and the blocks it
  produces are bound to the model and the conversation: an assistant turn must
  be echoed back exactly as it came, thinking signatures and all, or the next
  call is a 400. So every reply keeps its content blocks verbatim in
  ``provider_blocks``, and an assistant turn this model wrote is replayed from
  them. A turn another model wrote (gemini, a LAN qwen, or another Claude) has
  no such blocks and is replayed as plain text, the same rule gemini.py uses.
* Tool results are ``tool_result`` blocks in a ``user`` turn, all the results
  of one assistant turn in a single message -- and a plain user message after
  them (the loop's wrap-up nudge) joins that same turn as a trailing text
  block rather than opening a second, adjacent ``user`` turn.
* Prompt caching is a prefix match. Within one cycle the system prompt and the
  tool list never change and the conversation only grows, so one automatic
  top-level breakpoint (which moves forward with the conversation) is enough
  for every round to read the previous round's prefix from cache. Across
  cycles nothing survives anyway: heartbeats are hours apart, far beyond the
  5-minute TTL, and the system prompt carries the journal, which has changed.

Running out of credits is a 400 or 402 saying "credit balance is too low", not
a 429. It is classified as ``exhausted`` so the chain parks this key and falls
through to gemini/LAN instead of treating it as our own malformed payload.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, NamedTuple

import anthropic

from ai_brain.llm import (
    ErrorKind,
    Message,
    Provider,
    ProviderError,
    Reply,
    ToolCall,
    ToolSpec,
    Usage,
    as_replayed_result,
    as_transcript,
    is_foreign_turn,
)

log = logging.getLogger(__name__)

# Opus at high effort on a large prompt takes minutes, not the 60 s a free-tier
# flash call gets. The chain bounds each call by this; the SDK's own timeout is
# set to the same so a hung socket surfaces as a classified ``timeout``.
DEFAULT_CALL_TIMEOUT_S = 300

# Thinking spends from max_tokens on these models and cannot be turned off, so
# the loop's 8000 (sized for gemini) can leave a deep-thinking turn no room to
# say anything. Raised to this floor; still well under the size at which the
# SDK insists on streaming for a non-streaming call.
MIN_MAX_TOKENS = 16_000

class Price(NamedTuple):
    """$/MTok. Cache writes are 1.25x input (5-minute TTL, the only one this
    provider uses). A model priced by prompt size bills the whole request at
    ``long_multiplier`` once its prompt passes ``long_prompt_tokens``."""

    input: float
    output: float
    cache_read: float
    long_prompt_tokens: int | None = None
    long_multiplier: float = 1.0


# Matched by prefix so a dated or suffixed model id still prices.
PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0, 0.25),
    "claude-opus-5-5": Price(4.0, 20.0, 0.20),
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.20),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, long_prompt_tokens=100_000, long_multiplier=5.0),
}
CACHE_WRITE_MULTIPLIER = 1.25
# A model not in PRICES is charged as the most expensive one, so a typo'd or
# new model id over-counts rather than spending the month unseen.
_UNKNOWN_PRICE = PRICES["claude-fable-5-1"]

# THINK_BY_MODEL values as ``output_config.effort``. ``false`` cannot mean
# "off" here -- thinking is always on -- so it means the least of it; ``true``
# means the model's own default, i.e. send nothing.
_EFFORT_BY_THINK: dict[str, str | None] = {
    "false": "low",
    "true": None,
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
}

_LOG_BODY_CHARS = 500


def effort_for(think: str | None) -> str | None:
    """A THINK_BY_MODEL level as an effort value, or None for the default."""
    return _EFFORT_BY_THINK.get(think) if think else None


def price_usd(model: str, usage: Usage) -> float:
    """What one call cost, from its usage block.

    ``usage.prompt_tokens`` is the *total* prompt (fresh + cache read + cache
    write), which is what the loop's prompt budget wants; the fresh share is
    what is left after the two cache shares come off.
    """
    price = next(
        (p for name, p in PRICES.items() if model.startswith(name)),
        None,
    )
    if price is None:
        log.warning("no price for %s; charging it as %s", model, "claude-fable-5-1")
        price = _UNKNOWN_PRICE
    fresh = max(0, usage.prompt_tokens - usage.cached_tokens - usage.cache_write_tokens)
    cost = (
        fresh * price.input
        + usage.cache_write_tokens * price.input * CACHE_WRITE_MULTIPLIER
        + usage.cached_tokens * price.cache_read
        + usage.completion_tokens * price.output
    ) / 1_000_000
    if price.long_prompt_tokens is not None and usage.prompt_tokens > price.long_prompt_tokens:
        cost *= price.long_multiplier
    return cost


class AnthropicProvider(Provider):
    call_timeout_s = DEFAULT_CALL_TIMEOUT_S

    def __init__(
        self,
        model: str,
        api_key: str,
        client: Any | None = None,
        effort: str | None = None,
    ):
        self.model = model
        self.key = f"anthropic:{model}"
        self._api_key = api_key
        self._effort = effort
        self._client = client
        # The previous reply's prompt size: the conversation only grows, so it
        # is a floor on what a call that never answered was sending.
        self._last_prompt_tokens = 0

    def timeout_cost_usd(self, max_tokens: int) -> float:
        """A timed-out call priced as if nothing had been cached and the
        whole output budget had been spent -- the most it could have cost
        given the last prompt this provider saw."""
        return price_usd(
            self.model,
            Usage(
                prompt_tokens=self._last_prompt_tokens,
                completion_tokens=max(max_tokens, MIN_MAX_TOKENS),
            ),
        )

    def _sdk(self) -> Any:
        if self._client is None:
            # The chain owns retries (one more try on a blip, then the next
            # provider); SDK retries underneath would multiply them and spend
            # the call's timeout on a provider the chain wants to leave.
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key, max_retries=0, timeout=self.call_timeout_s
            )
        return self._client

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> Reply:
        kwargs = build_request(messages, tools, max_tokens, self.model, self._effort)
        log.debug(
            "%s request: %d messages, %d tools", self.key, len(kwargs["messages"]), len(tools)
        )
        try:
            response = await self._sdk().messages.create(**kwargs)
        except anthropic.APITimeoutError as err:
            cost = self.timeout_cost_usd(max_tokens)
            raise ProviderError(
                f"{self.key} timed out: {err}",
                kind="timeout",
                usage=Usage(prompt_tokens=0, completion_tokens=0, cost_usd=cost),
            ) from err
        except anthropic.APIConnectionError as err:
            raise ProviderError(f"{self.key} transport error: {err}", kind="server") from err
        except anthropic.APIStatusError as err:
            raise self._error(err) from err
        return self._reply(response)

    def _error(self, err: anthropic.APIStatusError) -> ProviderError:
        status = err.status_code
        message = str(err)
        kind = classify(status, getattr(err, "type", None), message)
        log.warning("%s HTTP %d (%s): %s", self.key, status, kind, message[:_LOG_BODY_CHARS])
        retry_after = _retry_after(err) if kind == "rate_limited" else None
        return ProviderError(
            f"{self.key} HTTP {status}: {message[:_LOG_BODY_CHARS]}",
            kind=kind,
            retry_after_s=retry_after,
        )

    def _reply(self, response: Any) -> Reply:
        u = response.usage
        cache_read = u.cache_read_input_tokens or 0
        cache_write = u.cache_creation_input_tokens or 0
        usage = Usage(
            prompt_tokens=u.input_tokens + cache_read + cache_write,
            completion_tokens=u.output_tokens,
            cached_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        usage = replace(usage, cost_usd=price_usd(self.model, usage))
        self._last_prompt_tokens = usage.prompt_tokens

        if response.stop_reason == "refusal":
            # A safety decline is an HTTP 200, not an error, and it is billed.
            # Another provider in the chain may well answer it, so it falls
            # through rather than ending the cycle -- with its cost attached
            # so the ledger still counts it.
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            raise ProviderError(
                f"{self.key} refused (category={category})", kind="refused", usage=usage
            )

        blocks = [block.to_dict() for block in response.content]
        if response.stop_reason == "max_tokens":
            # A tool call cut off mid-generation has no result to pair it
            # with, and echoing an unpaired tool_use is a 400 on the next
            # round. Drop it; the loop sees a turn with no calls and moves on.
            blocks = [b for b in blocks if b.get("type") != "tool_use"]

        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        thinking = "".join(
            b.get("thinking", "") for b in blocks if b.get("type") == "thinking"
        )
        calls = tuple(
            ToolCall(id=b["id"], name=b.get("name", ""), args=b.get("input") or {})
            for b in blocks
            if b.get("type") == "tool_use"
        )
        return Reply(
            text=text,
            tool_calls=calls,
            usage=usage,
            model=self.model,
            thinking=thinking,
            key=self.key,
            provider_blocks=tuple(blocks),
        )


def classify(status: int, error_type: str | None, message: str) -> ErrorKind:
    """An HTTP failure as the chain's error kind.

    Out-of-credit is checked first: it arrives as a 402 ``billing_error`` or as
    a 400 whose message says so, and as a 400 it would otherwise read as our
    own malformed payload and end the cycle instead of falling back.
    """
    lowered = message.lower()
    if status == 402 or error_type == "billing_error" or "credit balance" in lowered:
        return "exhausted"
    if status == 429:
        return "rate_limited"
    if status in (401, 403, 404):
        # A bad key, a key without access to this model, a model id that does
        # not exist: config mistakes that will not fix themselves this cycle.
        return "not_found"
    if status in (400, 413, 422):
        return "bad_request"
    return "server"


def _retry_after(err: anthropic.APIStatusError) -> float | None:
    header = err.response.headers.get("retry-after") if err.response is not None else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def build_request(
    messages: list[Message],
    tools: list[ToolSpec],
    max_tokens: int,
    model: str,
    effort: str | None = None,
) -> dict[str, Any]:
    """Our message list as ``messages.create`` keyword arguments."""
    system: list[str] = []
    turns: list[dict[str, Any]] = []
    # Set while walking an assistant turn whose tool calls came from another
    # model, so the results that follow it are replayed as text the same way.
    replaying = False

    for message in messages:
        if message.role == "system":
            if message.content:
                system.append(message.content)
        elif message.role == "tool":
            if replaying:
                _append(turns, "user", _text(as_replayed_result(message)))
                continue
            _append(
                turns,
                "user",
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content or "(empty result)",
                },
            )
        elif message.role == "assistant":
            replaying = is_foreign_turn(message, model)
            if message.model == model and message.provider_blocks:
                _append(turns, "assistant", *message.provider_blocks)
            elif replaying:
                log.debug("replaying %s turn as text for %s", message.model, model)
                _append(turns, "assistant", _text(as_transcript(message)))
            else:
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append(_text(message.content))
                blocks.extend(
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                    for c in message.tool_calls
                )
                if blocks:
                    _append(turns, "assistant", *blocks)
        else:
            if message.content:
                _append(turns, "user", _text(message.content))

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max(max_tokens, MIN_MAX_TOKENS),
        "messages": turns,
        # Summarised thinking costs the same as omitted thinking (it is spent
        # and billed either way) and gives the trace something to show.
        "thinking": {"type": "adaptive", "display": "summarized"},
        "cache_control": {"type": "ephemeral"},
    }
    if system:
        kwargs["system"] = "\n\n".join(system)
    if tools:
        kwargs["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]
    if effort:
        kwargs["output_config"] = {"effort": effort}
    return kwargs


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _append(turns: list[dict[str, Any]], role: str, *blocks: dict[str, Any]) -> None:
    """Add blocks to the conversation, merging into the last turn if same role.

    tool_result blocks must lead their user turn, which holds by construction:
    a tool result only ever follows an assistant turn, and the text merged in
    after it (the wrap-up nudge, a replayed result) comes later.
    """
    if turns and turns[-1]["role"] == role:
        turns[-1]["content"].extend(blocks)
    else:
        turns.append({"role": role, "content": list(blocks)})

