"""The one place backend tools talk to the network.

Every backend tool is a read against a service that can be slow, down or
missing entirely, and none of that may end an agent's cycle. ``request``
therefore returns either a response or an error string -- never an exception --
and each caller turns that string into ``err(...)``.

The client comes from ``ctx.extras["http"]`` when the process runs one, so
connections are pooled across a cycle. Tests usually omit it, so a private
client is opened for the single call instead.

A 3xx is an error like any other non-2xx, except to a caller following
redirects by hand: ``web_fetch`` has to see the ``Location`` header to validate
the next hop before requesting it, so it passes ``allow_redirect_response``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from ai_brain.tools import ToolContext

TIMEOUT_S = 20


@asynccontextmanager
async def client_for(ctx: ToolContext) -> AsyncIterator[httpx.AsyncClient]:
    shared = ctx.extras.get("http")
    if isinstance(shared, httpx.AsyncClient):
        yield shared
        return
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as own:
        yield own


async def request(
    ctx: ToolContext,
    method: str,
    url: str,
    *,
    label: str,
    **kwargs: Any,
) -> tuple[httpx.Response | None, str | None]:
    """Return ``(response, None)`` on 2xx, else ``(None, message)``."""
    kwargs.setdefault("timeout", TIMEOUT_S)
    try:
        async with client_for(ctx) as client:
            response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        return None, f"{label}: {type(exc).__name__}: {exc}"
    if response.is_success:
        return response, None
    return None, f"{label}: backend returned HTTP {response.status_code}"


async def stream(
    ctx: ToolContext,
    method: str,
    url: str,
    *,
    label: str,
    max_bytes: int,
    allow_redirect_response: bool = False,
    **kwargs: Any,
) -> tuple[httpx.Response | None, bytes, bool, str | None]:
    """Like ``request``, but stop reading the body after ``max_bytes``.

    Returns ``(response, body, truncated, error)``. The body is read chunk by
    chunk and abandoned the moment the cap is reached, so a caller can be
    pointed at a huge file without the process ever holding it -- which
    ``response.read()`` cannot promise. Headers are available before any of the
    body arrives, so a caller may also refuse on Content-Type and read nothing.
    """
    kwargs.setdefault("timeout", TIMEOUT_S)
    body = bytearray()
    truncated = False
    try:
        async with (
            client_for(ctx) as client,
            client.stream(method, url, **kwargs) as response,
        ):
            usable = response.is_success or (
                allow_redirect_response
                and response.is_redirect
                and "location" in response.headers
            )
            if not usable:
                return None, b"", False, f"{label}: backend returned HTTP {response.status_code}"
            if response.is_redirect:
                # The caller only wants the Location header; reading a
                # redirect's body would be pointless bytes.
                return response, b"", False, None
            # ``truncated`` means "there was more than we kept", so it may only
            # be set once bytes *past* the cap have actually arrived. A body
            # that is exactly ``max_bytes`` long is complete, and saying
            # otherwise sends the model chasing a page it already has in full.
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    truncated = True
                    del body[max_bytes:]
                    break
    except httpx.HTTPError as exc:
        return None, b"", False, f"{label}: {type(exc).__name__}: {exc}"
    return response, bytes(body), truncated, None


def decode_json(response: httpx.Response, label: str) -> tuple[Any, str | None]:
    try:
        return response.json(), None
    except ValueError:
        return None, f"{label}: backend returned a non-JSON body"
