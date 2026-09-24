"""Google Gemini over the REST ``generateContent`` endpoint.

Only the wire format is special here; everything else in the process speaks the
provider-agnostic types from :mod:`ai_brain.llm`. So this module is two
translations and nothing more: our ``Message``/``ToolSpec`` list into Gemini's
``contents``/``tools`` payload, and Gemini's candidates and HTTP statuses back
into a ``Reply`` or a classified ``ProviderError``.

Two details of Gemini's format drive the shape of the request builder:

* There is no ``tool`` role. A tool result is a ``user`` turn carrying a
  ``functionResponse`` part, and Gemini expects *all* the responses to one
  model turn in a single ``user`` content -- so consecutive tool messages are
  merged rather than emitted one content each. A plain user message that
  follows tool results (the loop's wrap-up nudge, chiefly) is the same
  ``user`` role as what precedes it, so it is folded into that content as an
  extra text part too, rather than opened as a second, adjacent ``user``
  turn -- see ``_append_user_text``.
* The system prompt is not a turn at all; it lives beside ``contents`` in
  ``systemInstruction``.
* Gemini 3.x thinking models sign their parts with a ``thoughtSignature``, and
  reject the turn on the next call unless the same string comes back on the
  same part. Signatures are therefore read off the response and echoed
  verbatim; they are opaque to us.

The API key travels only in the ``x-goog-api-key`` header, never in the URL or
the body, so neither logs nor error text can leak it.
"""

from __future__ import annotations

import json
import logging
import re
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

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
TEMPERATURE = 0.7

# ``-1`` is Gemini's "think as long as the question deserves". Sending nothing
# leaves the model on its own default, which on a flash model is the shallowest
# pass it can get away with -- fine for a chat turn, not for a cycle that has
# to read the house and decide what matters. ``0`` means send no
# ``thinkingConfig`` at all.
DEFAULT_THINKING_BUDGET = -1

# Not every model in the chain accepts the field: flash-lite and the older
# flashes 400 on it. A model that refuses it is not a failed cycle -- the field
# is dropped for that provider and the call is retried once, so the chain never
# falls through to a weaker model over a knob we added.
_THINKING_REJECTED = re.compile(r"thinking|thought", re.IGNORECASE)

# Gemini reports backoff as a protobuf duration string, e.g. "7s" or "12.5s".
_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")

# Anything not named here is treated as transient: a 5xx is plainly the far
# side's problem, and so in practice is a 401/403, which for this deployment
# means a key that has not propagated yet rather than a payload we can fix.
_STATUS_KINDS: dict[int, ErrorKind] = {
    400: "bad_request",
    404: "not_found",
    429: "rate_limited",
}

# Error bodies can be long; enough to identify the failure is enough to log.
_LOG_BODY_CHARS = 500


class GeminiProvider(Provider):
    def __init__(
        self,
        model: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 60,
        thinking_budget: int = DEFAULT_THINKING_BUDGET,
    ):
        self.model = model
        self.key = f"gemini:{model}"
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._thinking_budget = thinking_budget
        self._client = client
        # A client we were handed belongs to the caller; only one we made
        # ourselves is ours to close.
        self._owns_client = client is None

    @property
    def url(self) -> str:
        return f"{API_ROOT}/{self.model}:generateContent"

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
        response = await self._post(messages, tools, max_tokens)

        if response.status_code >= 400:
            if self._disable_thinking(response):
                response = await self._post(messages, tools, max_tokens)
            if response.status_code >= 400:
                raise self._error(response)
        return self._reply(response.json())

    async def _post(
        self, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> httpx.Response:
        payload = build_request(messages, tools, max_tokens, self._thinking_budget, self.model)
        body = json.dumps(payload)
        log.debug(
            "%s request: %d bytes, %d contents",
            self.key,
            len(body),
            len(payload["contents"]),
        )

        try:
            return await self._http().post(
                self.url,
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
            )
        except httpx.TimeoutException as err:
            raise ProviderError(f"{self.key} timed out: {err}", kind="timeout") from err
        except httpx.HTTPError as err:
            raise ProviderError(f"{self.key} transport error: {err}", kind="server") from err

    def _disable_thinking(self, response: httpx.Response) -> bool:
        """True when this 400 was our thinking budget, which is now switched off.

        Permanent for the life of the provider: the model will not start
        accepting the field mid-run, and retrying it every call would double
        every request this process makes.
        """
        if self._thinking_budget == 0 or response.status_code != 400:
            return False
        if not _THINKING_REJECTED.search(_error_message(response)):
            return False
        log.warning("%s rejected thinkingConfig; continuing without it", self.key)
        self._thinking_budget = 0
        return True

    def _error(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        kind = _STATUS_KINDS.get(status, "server")
        text = response.text
        log.warning("%s HTTP %d: %s", self.key, status, text[:_LOG_BODY_CHARS])
        retry_after = retry_after_seconds(response) if kind == "rate_limited" else None
        return ProviderError(
            f"{self.key} HTTP {status}: {_error_message(response)}",
            kind=kind,
            retry_after_s=retry_after,
        )

    def _reply(self, body: dict) -> Reply:
        candidates = body.get("candidates") or []
        if not candidates:
            feedback = body.get("promptFeedback")
            log.warning("%s returned no candidates: %s", self.key, str(body)[:_LOG_BODY_CHARS])
            raise ProviderError(
                f"{self.key} returned no candidates (promptFeedback={feedback})",
                kind="server",
            )

        parts = candidates[0].get("content", {}).get("parts") or []
        # With ``includeThoughts`` the reasoning comes back as ordinary text
        # parts flagged ``thought: true``. They have to be split off here:
        # folded into the answer they read as the model muttering to itself
        # halfway through its own reply.
        text = "".join(
            part["text"] for part in parts if "text" in part and not part.get("thought")
        )
        thinking = "".join(
            part["text"] for part in parts if "text" in part and part.get("thought")
        )
        calls = tuple(
            ToolCall(
                id=f"call_{n}",
                name=part["functionCall"].get("name", ""),
                args=part["functionCall"].get("args") or {},
                thought_signature=part.get("thoughtSignature") or "",
            )
            for n, part in enumerate(
                (p for p in parts if "functionCall" in p),
                start=1,
            )
        )
        # A thinking model may sign the text part instead of (or as well as)
        # the calls; that one belongs to the turn, so keep the first we see.
        # Prefer the answer's own signature, but take a thought part's rather
        # than none: dropping a signature the turn had is what earns a 400 on
        # the *next* call, and an answer-less thinking turn only signs there.
        signed_text = [
            part for part in parts if "text" in part and part.get("thoughtSignature")
        ]
        text_signature = next(
            (part["thoughtSignature"] for part in signed_text if not part.get("thought")),
            next((part["thoughtSignature"] for part in signed_text), ""),
        )

        usage = body.get("usageMetadata") or {}
        return Reply(
            text=text,
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=usage.get("promptTokenCount", 0),
                # Thinking tokens are billed and count against the same TPM
                # budget as the visible answer, but Gemini reports them
                # separately -- and on a thinking model they are usually the
                # larger half. Leaving them out makes the ledger think there is
                # far more budget left than there is.
                completion_tokens=(
                    usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
                ),
            ),
            model=self.model,
            thought_signature=text_signature,
            thinking=thinking,
            key=self.key,
        )


def build_request(
    messages: list[Message],
    tools: list[ToolSpec],
    max_tokens: int,
    thinking_budget: int = DEFAULT_THINKING_BUDGET,
    model: str = "",
) -> dict:
    """Our message list as a Gemini ``generateContent`` body."""
    system: list[str] = []
    contents: list[dict[str, Any]] = []
    # Set while walking a model turn whose tool calls came from some other
    # provider, so the results that follow it are replayed the same way.
    replaying = False

    for message in messages:
        if message.role == "system":
            if message.content:
                system.append(message.content)
        elif message.role == "tool":
            if replaying:
                _append_user_text(contents, f"Result of {message.name}: {message.content}")
                continue
            part = {
                "functionResponse": {
                    "name": message.name,
                    "response": _tool_response(message.content),
                }
            }
            # All responses to one model turn belong in a single user content.
            if contents and contents[-1].get("role") == "user" and _is_tool_content(contents[-1]):
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
        elif message.role == "assistant":
            replaying = _is_foreign_turn(message, model)
            if replaying:
                log.debug("replaying %s turn as text for %s", message.model, model)
                contents.append({"role": "model", "parts": [{"text": _as_transcript(message)}]})
                continue
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append(_signed({"text": message.content}, message.thought_signature))
            parts.extend(
                _signed(
                    {"functionCall": {"name": call.name, "args": call.args}},
                    call.thought_signature,
                )
                for call in message.tool_calls
            )
            if message.thought_signature and not message.content:
                # A thinking turn that signed itself but said nothing out loud.
                # Dropping that signature is a 400 on the next call, so it has
                # to ride on some part: the first unsigned functionCall if
                # there is one, otherwise an empty text part carrying it alone.
                _attach_orphan_signature(parts, message.thought_signature)
            contents.append({"role": "model", "parts": parts})
        else:
            # A plain user turn (the loop's wrap-up nudge, chiefly) right
            # after tool results is still the same ``user`` role Gemini
            # already sees on the preceding content -- two consecutive
            # ``user`` contents is not how Gemini's strict turn-taking wants
            # a back-and-forth, so this folds into the existing
            # functionResponse content as one more part rather than opening a
            # second, adjacent ``user`` turn. Ordinary text-follows-text still
            # goes through _append_user_text's own merge.
            if contents and contents[-1].get("role") == "user" and _is_tool_content(contents[-1]):
                contents[-1]["parts"].append({"text": message.content})
            else:
                _append_user_text(contents, message.content)

    payload: dict[str, Any] = {}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
    payload["contents"] = contents
    if tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    }
                    for tool in tools
                ]
            }
        ]
    generation: dict[str, Any] = {"maxOutputTokens": max_tokens, "temperature": TEMPERATURE}
    if thinking_budget != 0:
        # ``includeThoughts`` asks for a summary of reasoning the model does
        # either way: the thinking tokens are spent (and billed, and counted in
        # ``_reply``) whether or not it tells us about them, so this is free
        # apart from response bytes. Not every 3.x turn returns one.
        generation["thinkingConfig"] = {
            "thinkingBudget": thinking_budget,
            "includeThoughts": True,
        }
    payload["generationConfig"] = generation
    return payload


def retry_after_seconds(response: httpx.Response) -> float | None:
    """Backoff hint from a 429: the header if present, else Google's RetryInfo."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            # The HTTP-date form is legal but Gemini does not use it; the
            # ledger's own backoff is a fine fallback.
            log.warning("unparsable Retry-After: %s", header[:_LOG_BODY_CHARS])

    for detail in _error_field(response, "details") or []:
        if isinstance(detail, dict):
            match = _RETRY_DELAY.match(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _attach_orphan_signature(parts: list[dict[str, Any]], signature: str) -> None:
    """Give a turn-level signature somewhere to live when there is no text."""
    for part in parts:
        if "functionCall" in part and not part.get("thoughtSignature"):
            part["thoughtSignature"] = signature
            return
    parts.append({"text": "", "thoughtSignature": signature})


def _is_foreign_turn(message: Message, model: str) -> bool:
    """True when this tool-calling turn came from a different model.

    The chain changes model mid-cycle whenever one runs out: the LAN host goes
    away, a key is parked on 429s. The conversation so far then belongs to
    ollama, which has no notion of a thought signature, and Gemini 3.x rejects
    the whole request for it -- "Function call is missing a thought_signature
    in functionCall parts" -- as a 400 that ``bad_request`` quite rightly
    refuses to retry. The cycle dies at whatever round the switch happened on,
    with every round before it wasted.

    Signatures are the model's own, so the same applies between two Gemini
    models. A turn with no model recorded is left alone: that is a turn nobody
    attributed, and the old behaviour is the safe one for it.
    """
    return bool(message.tool_calls) and bool(message.model) and message.model != model


def _as_transcript(message: Message) -> str:
    """One unsigned model turn as plain text Gemini will accept.

    The alternative to replaying it is dropping it, and the tool calls are the
    entire content of a cycle: an agent handed its own history minus what it
    did would repeat every call it had already made.
    """
    lines = [message.content] if message.content else []
    lines += [
        f"I called {call.name}({json.dumps(call.args, ensure_ascii=False, sort_keys=True)})"
        for call in message.tool_calls
    ]
    return "\n".join(lines)


def _append_user_text(contents: list[dict[str, Any]], text: str) -> None:
    """Add a user text part, merging into the previous user turn if it is text.

    Deliberately does *not* merge into a preceding tool-result content (one
    that is all ``functionResponse`` parts): this is also how a replayed
    foreign turn's tool results are appended (the ``tool``/``replaying``
    branch in ``build_request``), and folding that synthetic text into a
    *real* functionResponse content would blur two things that ought to stay
    visually distinct in the payload. A plain user message that needs to
    merge into a real tool-result content instead -- the wrap-up nudge,
    chiefly -- is handled at its own call site in ``build_request``, which
    merges into a tool content on purpose.
    """
    if contents and contents[-1].get("role") == "user" and not _is_tool_content(contents[-1]):
        contents[-1]["parts"].append({"text": text})
        return
    contents.append({"role": "user", "parts": [{"text": text}]})


def _signed(part: dict[str, Any], signature: str) -> dict[str, Any]:
    """A part with its thought signature attached, if it has one.

    Gemini 3.x rejects an echoed model turn whose ``functionCall`` parts lost
    their signature, but an empty ``thoughtSignature`` is just as invalid --
    so the key is emitted only when there is something to say.
    """
    if signature:
        part["thoughtSignature"] = signature
    return part


def _tool_response(content: str) -> dict:
    """A tool result as a JSON object, since Gemini will not take a bare scalar."""
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        return {"result": content}
    return parsed if isinstance(parsed, dict) else {"result": content}


def _is_tool_content(content: dict) -> bool:
    return all("functionResponse" in part for part in content["parts"])


def _error_field(response: httpx.Response, field: str) -> Any:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error.get(field) if isinstance(error, dict) else None


def _error_message(response: httpx.Response) -> str:
    return _error_field(response, "message") or response.text[:_LOG_BODY_CHARS]
