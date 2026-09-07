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
  merged rather than emitted one content each.
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
    ):
        self.model = model
        self.key = f"gemini:{model}"
        self._api_key = api_key
        self._timeout_s = timeout_s
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
        payload = build_request(messages, tools, max_tokens)
        body = json.dumps(payload)
        log.debug(
            "%s request: %d bytes, %d contents",
            self.key,
            len(body),
            len(payload["contents"]),
        )

        try:
            response = await self._http().post(
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

        if response.status_code >= 400:
            raise self._error(response)
        return self._reply(response.json())

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
        text = "".join(part["text"] for part in parts if "text" in part)
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
        text_signature = next(
            (
                part["thoughtSignature"]
                for part in parts
                if "text" in part and part.get("thoughtSignature")
            ),
            "",
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
        )


def build_request(messages: list[Message], tools: list[ToolSpec], max_tokens: int) -> dict:
    """Our message list as a Gemini ``generateContent`` body."""
    system: list[str] = []
    contents: list[dict[str, Any]] = []

    for message in messages:
        if message.role == "system":
            if message.content:
                system.append(message.content)
        elif message.role == "tool":
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
            contents.append({"role": "user", "parts": [{"text": message.content}]})

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
    payload["generationConfig"] = {"maxOutputTokens": max_tokens, "temperature": TEMPERATURE}
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
