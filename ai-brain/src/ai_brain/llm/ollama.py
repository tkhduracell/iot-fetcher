"""Local Ollama over its native ``/api/chat`` endpoint.

Same shape as :mod:`ai_brain.llm.gemini`: two translations and nothing more,
our ``Message``/``ToolSpec`` list into Ollama's ``messages``/``tools`` payload,
and Ollama's response and HTTP statuses back into a ``Reply`` or a classified
``ProviderError``.

Ollama has no API key (it is a local/LAN service) and no thought signatures --
those are Gemini 3.x-specific and simply never populated here. Tool calls and
results use plain ``role: "tool"`` messages, unlike Gemini's ``functionResponse``
parts, so no merging of consecutive tool turns is needed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ai_brain.llm import (
    ErrorKind,
    Message,
    Provider,
    ProviderError,
    Reply,
    ToolCall,
    ToolSpec,
    Usage,
)

log = logging.getLogger(__name__)

# Error bodies can be long; enough to identify the failure is enough to log.
_LOG_BODY_CHARS = 500

# This box's own hardware, not a cloud SLA -- the chain's default 60s (sized
# for the metered providers ahead of it) is too tight for a local model under
# load and cuts it off mid-answer rather than letting it finish. Same
# reasoning as the lan: provider's much longer timeout, just a smaller number:
# this is the rpi5 itself running a 3B model, not a desktop GPU running
# something bigger.
CALL_TIMEOUT_S = 600.0

# The native /api/chat endpoint honours options.num_ctx per request; the
# OpenAI-compatible /v1/chat/completions endpoint does not, which is why this
# module talks to /api/chat rather than that one. Ollama's own server default
# is 4096 unless overridden (here, or via OLLAMA_CONTEXT_LENGTH on the
# server), which is far smaller than a cycle's persona+memory+tool-result
# prompt -- the server silently truncates from the *front* of the prompt to
# fit, dropping exactly the persona/system context a truncated prompt most
# needs. 8192 is a working default for a Raspberry Pi 5 (8GB): enough for a
# small model's context without swapping, still short of the 16GB board's
# headroom. See ai-brain/README.md's Configuration section for the tradeoff.
DEFAULT_NUM_CTX = 8192

# Rough tokens-per-character used only to decide whether to warn/trim before
# sending -- Ollama does the real tokenization server-side. English averages
# ~4 chars/token; this errs low (over-counts tokens) so the warning fires
# before the server's own truncation would, not after.
_CHARS_PER_TOKEN = 4

# Chat-template overhead (role markers, per-message framing, tool schemas)
# that _estimate_tokens does not see since it only sums message content.
# Fixed and conservative rather than computed from the tool list -- this is a
# trim decision, not a token bill.
_TEMPLATE_OVERHEAD_TOKENS = 512


class OllamaProvider(Provider):
    call_timeout_s = CALL_TIMEOUT_S

    def __init__(
        self,
        model: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float | httpx.Timeout = CALL_TIMEOUT_S,
        num_ctx: int = DEFAULT_NUM_CTX,
    ):
        self.model = model
        self.key = f"ollama:{model}"
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client = client
        self._num_ctx = num_ctx
        # A client we were handed belongs to the caller; only one we made
        # ourselves is ours to close.
        self._owns_client = client is None

    @property
    def url(self) -> str:
        return f"{self._base_url}/api/chat"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> Reply:
        messages = _fit_to_context(messages, self._num_ctx, max_tokens, self.key)
        payload = build_request(
            self.model, messages, tools, max_tokens, num_ctx=self._num_ctx
        )
        body = json.dumps(payload)
        log.debug(
            "%s request: %d bytes, %d messages",
            self.key,
            len(body),
            len(payload["messages"]),
        )

        try:
            response = await self._http().post(
                self.url,
                content=body,
                headers={"content-type": "application/json"},
            )
        except httpx.TimeoutException as err:
            raise ProviderError(f"{self.key} timed out: {err}", kind="timeout") from err
        except httpx.HTTPError as err:
            raise ProviderError(
                f"{self.key} transport error: {err}", kind="server"
            ) from err

        if response.status_code >= 400:
            raise self._error(response)
        return self._reply(response.json())

    def _error(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        text = response.text
        log.warning("%s HTTP %d: %s", self.key, status, text[:_LOG_BODY_CHARS])
        # Ollama returns 404 for an unpulled model and 400 for a malformed
        # request; everything else (5xx, and anything unexpected) is treated
        # as transient since this is a local/LAN service with no quota concept.
        kind: ErrorKind = (
            "not_found"
            if status == 404
            else "bad_request"
            if status == 400
            else "server"
        )
        return ProviderError(
            f"{self.key} HTTP {status}: {_error_message(response)}", kind=kind
        )

    def _reply(self, body: dict) -> Reply:
        message = body.get("message") or {}
        text = message.get("content") or ""
        # Thinking models (qwen3 and friends) keep their reasoning out of
        # ``content`` and in its own field, so this costs nothing to read: the
        # tokens were generated on our own machine either way. Absent on a
        # model that does not think, which is why it defaults to "".
        thinking = message.get("thinking") or ""
        calls = tuple(
            ToolCall(
                id=f"call_{n}",
                name=call.get("function", {}).get("name", ""),
                args=call.get("function", {}).get("arguments") or {},
            )
            for n, call in enumerate(message.get("tool_calls") or [], start=1)
        )
        return Reply(
            text=text,
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=body.get("prompt_eval_count", 0),
                completion_tokens=body.get("eval_count", 0),
            ),
            model=self.model,
            thinking=thinking,
        )


def _estimate_tokens(messages: list[Message]) -> int:
    """A cheap upper-bound token estimate, good enough to decide whether to warn/trim.

    Ollama does the real tokenization server-side; this only needs to be in
    the right ballpark, and erring high (undercounting chars/token) is the
    safe direction -- it means we trim a little early rather than a little
    late.
    """
    chars = sum(len(m.content) for m in messages)
    return chars // _CHARS_PER_TOKEN


def _fit_to_context(
    messages: list[Message], num_ctx: int, max_tokens: int, key: str
) -> list[Message]:
    """Warn, and drop oldest tool-result turns, when the prompt won't fit.

    Ollama's own behaviour on overflow is to silently truncate the prompt
    from the *front* -- which is exactly backwards for this chain's messages,
    since the front holds the persona/system turn every reply depends on.
    Trimming from the middle (oldest tool results first, working forward)
    keeps that system turn and the most recent turns, which matter far more
    to answer quality than a mid-conversation tool result.

    Budget is ``num_ctx`` minus room for the reply (``max_tokens``) and a
    fixed slack for chat-template overhead (role markers, tool schemas),
    since the estimate only counts message content.
    """
    # Clamped rather than let go negative: a num_ctx so small that reserving
    # max_tokens + overhead alone would exhaust it is already a
    # misconfiguration, but the prompt still needs *some* budget to trim
    # against instead of skipping the warning because the math went negative.
    budget = max(num_ctx - max_tokens - _TEMPLATE_OVERHEAD_TOKENS, 1)
    estimated = _estimate_tokens(messages)
    if estimated <= budget:
        return messages

    log.warning(
        "%s prompt ~%d tokens exceeds num_ctx=%d budget (~%d after reserving "
        "%d for the reply); trimming oldest tool results rather than letting "
        "the server truncate from the front",
        key,
        estimated,
        num_ctx,
        budget,
        max_tokens,
    )

    trimmed = list(messages)
    # Drop oldest-first, but never the first message (system/persona) or the
    # most recent one (the user's actual turn) -- everything in between is
    # fair game, tool results before other roles since they are usually the
    # bulkiest and least needed verbatim once the model has moved on.
    droppable = [
        i
        for i in range(1, len(trimmed) - 1)
        if trimmed[i].role == "tool"
    ]
    for i in droppable:
        if _estimate_tokens(trimmed) <= budget:
            break
        trimmed[i] = Message(
            role="tool",
            content="[trimmed: prompt exceeded num_ctx]",
            tool_call_id=trimmed[i].tool_call_id,
            name=trimmed[i].name,
        )
    return trimmed


def build_request(
    model: str,
    messages: list[Message],
    tools: list[ToolSpec],
    max_tokens: int,
    num_ctx: int = DEFAULT_NUM_CTX,
) -> dict:
    """Our message list as an Ollama ``/api/chat`` body.

    ``options.num_ctx`` is only honoured by this native endpoint -- the
    OpenAI-compatible ``/v1/chat/completions`` one ignores per-request
    options entirely, which is why this module is native rather than
    OpenAI-compatible.
    """
    out_messages: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            out_messages.append({"role": "tool", "content": message.content})
        elif message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": call.args}}
                    for call in message.tool_calls
                ]
            out_messages.append(entry)
        else:
            out_messages.append({"role": message.role, "content": message.content})

    payload: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.7,
            "num_ctx": num_ctx,
        },
    }
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
    return payload


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:_LOG_BODY_CHARS]
    if isinstance(body, dict) and "error" in body:
        return str(body["error"])
    return response.text[:_LOG_BODY_CHARS]
