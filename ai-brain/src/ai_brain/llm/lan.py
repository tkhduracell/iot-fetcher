"""The ``lan:`` provider: an Ollama host discovered on the local network.

It is an :class:`~ai_brain.llm.ollama.OllamaProvider` whose base URL is not
known until a scan finds one, so the address is resolved per call rather than
at construction. Three hooks in the chain make that work:

* ``available()`` is False while no host is known, so the chain steps straight
  past it instead of spending an attempt on a call that cannot be made.
* ``max_attempts`` is 3 rather than the chain's usual 2: a local box that
  answers in seconds is worth a couple of extra tries before the chain moves
  on down to the metered cloud models.
* ``call_timeout_s`` is far longer than a cloud API's -- a big local model on
  real hardware can legitimately take minutes to finish one turn, nothing like
  a metered provider's SLA -- while the *connect* itself stays tight: if the
  machine is not answering on the port at all, there is nothing to wait for.

A host that fails all three attempts is forgotten, so the next scan sweeps for
another one instead of re-dialling a machine that has gone to sleep.
"""

from __future__ import annotations

import logging

import httpx

from ai_brain.discovery import OllamaFinder
from ai_brain.llm import Message, Provider, ProviderError, Reply, ToolSpec
from ai_brain.llm.ollama import DEFAULT_NUM_CTX, OllamaProvider

log = logging.getLogger(__name__)

LAN_ATTEMPTS = 3

# A local model can be genuinely slow -- 900s is generous on purpose, since
# the alternative is falling back to a metered cloud key over a model that was
# still thinking. The connect phase gets its own, far tighter bound: if the
# host is not answering the port at all, no amount of patience helps.
LAN_CONNECT_TIMEOUT_S = 3.0
LAN_REQUEST_TIMEOUT_S = 900.0


class LanOllamaProvider(Provider):
    max_attempts = LAN_ATTEMPTS
    call_timeout_s = LAN_REQUEST_TIMEOUT_S

    def __init__(
        self,
        finder: OllamaFinder,
        client: httpx.AsyncClient | None = None,
        timeout_s: httpx.Timeout | None = None,
        num_ctx: int = DEFAULT_NUM_CTX,
    ) -> None:
        self.finder = finder
        self.model = finder.model
        self.key = f"lan:{finder.model}"
        self._client = client
        self._timeout_s = timeout_s or httpx.Timeout(
            LAN_REQUEST_TIMEOUT_S, connect=LAN_CONNECT_TIMEOUT_S
        )
        self._num_ctx = num_ctx
        self._attempts_left = LAN_ATTEMPTS

    def available(self) -> bool:
        return self.finder.current() is not None

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> Reply:
        host = self.finder.current()
        if host is None:
            raise ProviderError(f"{self.key}: no host on the network", kind="server")

        provider = OllamaProvider(
            self.model,
            host.base_url,
            client=self._client,
            timeout_s=self._timeout_s,
            num_ctx=self._num_ctx,
        )
        try:
            reply = await provider.complete(messages, tools, max_tokens)
        except ProviderError:
            self._attempts_left -= 1
            if self._attempts_left <= 0:
                # Out of tries against this machine: let the chain fall through
                # to the cloud, and make the next scan look for another host.
                self.finder.forget()
                self._attempts_left = LAN_ATTEMPTS
            raise
        finally:
            if self._client is None:
                await provider.aclose()
        self._attempts_left = LAN_ATTEMPTS
        # The chain reports which model answered; without this the trace would
        # name the model and never say which machine ran it.
        return Reply(
            text=reply.text,
            tool_calls=reply.tool_calls,
            usage=reply.usage,
            model=f"{self.model} @ {host.base_url}",
            thinking=reply.thinking,
        )
