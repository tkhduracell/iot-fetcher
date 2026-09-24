"""The tool registry every agent loop dispatches through.

A tool is a JSON-schema ``ToolSpec`` (what the model sees), an async function
(what actually runs) and an optional allowlist of loops permitted to call it.
``specs_for`` decides what a loop is even offered; ``dispatch`` enforces the
same allowlist again at call time, because the model can invent a call for a
tool it was never given.

``dispatch`` never raises. A tool result is a string that goes straight back
into the conversation, so an unknown name, a policy refusal, a missing
argument and a crash inside the tool all come back as ``{"error": ...}`` --
the model reads it and tries something else instead of killing the cycle.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ai_brain.config import Settings
from ai_brain.llm import ToolCall, ToolSpec
from ai_brain.memory import MemoryDir
from ai_brain.redact import redact

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    loop: str
    memory: MemoryDir
    memories: dict[str, MemoryDir]
    settings: Settings
    wake: Callable[[str], None]
    extras: dict[str, Any] = field(default_factory=dict)


ToolFn = Callable[[ToolContext, dict], Awaitable[str]]
# A predicate over settings: false means the tool cannot work right now (a
# missing API key, an unset URL) rather than that the loop may not call it.
# Takes ``Settings`` rather than the full ``ToolContext`` because availability
# is a fact about how the process is configured, not about who is asking --
# every loop that is even offered the tool sees the same answer.
AvailableFn = Callable[[Settings], bool]


@dataclass
class Tool:
    spec: ToolSpec
    fn: ToolFn
    loops: frozenset[str] | None = None  # None = every loop
    # None = always available. A tool whose function would just return an
    # error string for a missing config (see e.g. web_search's "disabled:
    # BRAVE_API_KEY unset") should not be offered to the model at all --
    # spending a whole round to discover a tool cannot work is a round the
    # model does not get back, and a weak fallback model is the one least
    # able to shrug that off and try something else.
    available: AvailableFn | None = None


def ok(data: Any) -> str:
    payload = {"ok": True}
    if isinstance(data, dict):
        payload.update(data)
    else:
        payload["result"] = data
    return json.dumps(payload)


def err(msg: str) -> str:
    return json.dumps({"error": msg})


_SOURCE_RE = re.compile(r"[a-z0-9_.-]+")
_ZWSP = "\u200b"
# Case-insensitive: HTML tag names are, so ``</EXTERNAL>`` closes the fence too.
_FENCE_RE = re.compile(r"</?external", re.IGNORECASE)


def wrap_external(source: str, text: str) -> str:
    """Fence text that came from outside this system.

    A metric name is ours; a Drive document, a web page and a friendly name a
    human typed into Home Assistant are not. Anything in the second group is
    data the model reads, never instructions it follows, so it is handed over
    inside an ``<external>`` element that says where it came from.

    The fence only means anything if the fenced text cannot close it, so any
    ``<external`` or ``</external`` inside ``text`` -- in any case, since HTML
    tag names are case-insensitive -- gets a zero-width space wedged after the
    ``<``. That breaks the tag while leaving the text
    readable, so a page saying "put </external> here" still reads correctly and
    still cannot escape. ``source`` is ours rather than a stranger's, but it is
    validated too, since it lands in an attribute value.

    Every caller that hands the model log or page text from outside this
    system routes through here, which makes this the one place to catch a
    secret before it reaches the model at all: a container's stdout or HA's
    error log can contain an API key or bearer token quoted in a request URL
    or header, and once the model has read it, it can end up copied verbatim
    into a memory fact. Masking happens before fencing so a secret cannot
    hide the fence's own sentinel handling from itself.
    """
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError(f"wrap_external: invalid source {source!r}")
    masked = redact(text)
    safe = _FENCE_RE.sub(lambda m: m.group(0)[0] + _ZWSP + m.group(0)[1:], masked)
    return f'<external source="{source}">{safe}</external>'


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def specs_for(self, loop: str, settings: Settings | None = None) -> list[ToolSpec]:
        """What ``loop`` is offered: on its allowlist and, if it declares one,
        passing its own ``available`` check against ``settings``.

        ``settings`` is optional only so existing call sites and tests that do
        not care about availability (every tool without a predicate) keep
        working unchanged; a tool that *does* declare ``available`` and is
        asked for without settings is treated as available, since there is
        nothing to check it against.
        """
        return [
            t.spec
            for t in self._tools.values()
            if (t.loops is None or loop in t.loops)
            and (t.available is None or settings is None or t.available(settings))
        ]

    async def dispatch(self, ctx: ToolContext, call: ToolCall) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return err(f"unknown tool: {call.name}")

        # No ``available`` re-check here, unlike ``loops`` below: ``available``
        # only controls what a loop is *offered* in specs_for, as a courtesy so
        # a round is not wasted discovering a tool cannot work. A call that
        # arrives anyway (a stale tool list, a model that remembers a tool from
        # an earlier round before config changed) still reaches the real
        # function, whose own check already returns the same clear error
        # (e.g. web_search's "disabled: BRAVE_API_KEY unset") -- so this must
        # stay a soft filter, not a second hard refusal path to keep in sync
        # with the tool's own error message.
        if tool.loops is not None and ctx.loop not in tool.loops:
            log.warning("[policy] %s called %s, not on its allowlist", ctx.loop, call.name)
            return err(f"policy: tool {call.name} not allowed for loop {ctx.loop}")

        args = call.args if isinstance(call.args, dict) else {}
        missing = [name for name in tool.spec.parameters.get("required", []) if name not in args]
        if missing:
            return err(f"missing required argument(s) for {call.name}: {', '.join(missing)}")

        try:
            return await tool.fn(ctx, args)
        except Exception as exc:  # a tool crash must not end the cycle
            log.warning("[tool] %s failed", call.name, exc_info=True)
            return err(f"{type(exc).__name__}: {exc}")
