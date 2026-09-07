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


class OllamaProvider(Provider):
    def __init__(
        self,
        model: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 60,
    ):
        self.model = model
        self.key = f"ollama:{model}"
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client = client
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
        payload = build_request(self.model, messages, tools, max_tokens)
        body = json.dumps(payload)
        log.debug("%s request: %d bytes, %d messages", self.key, len(body), len(payload["messages"]))

        try:
            response = await self._http().post(
                self.url,
                content=body,
                headers={"content-type": "application/json"},
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
        text = response.text
        log.warning("%s HTTP %d: %s", self.key, status, text[:_LOG_BODY_CHARS])
        # Ollama returns 404 for an unpulled model and 400 for a malformed
        # request; everything else (5xx, and anything unexpected) is treated
        # as transient since this is a local/LAN service with no quota concept.
        kind: ErrorKind = "not_found" if status == 404 else "bad_request" if status == 400 else "server"
        return ProviderError(f"{self.key} HTTP {status}: {_error_message(response)}", kind=kind)

    def _reply(self, body: dict) -> Reply:
        message = body.get("message") or {}
        text = message.get("content") or ""
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
        )


def build_request(
    model: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
) -> dict:
    """Our message list as an Ollama ``/api/chat`` body."""
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
        "options": {"num_predict": max_tokens, "temperature": 0.7},
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
