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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ai_brain.config import Settings
from ai_brain.llm import ToolCall, ToolSpec
from ai_brain.memory import MemoryDir

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


@dataclass
class Tool:
    spec: ToolSpec
    fn: ToolFn
    loops: frozenset[str] | None = None  # None = every loop


def ok(data: Any) -> str:
    payload = {"ok": True}
    if isinstance(data, dict):
        payload.update(data)
    else:
        payload["result"] = data
    return json.dumps(payload)


def err(msg: str) -> str:
    return json.dumps({"error": msg})


def wrap_external(source: str, text: str) -> str:
    """Fence text that came from outside this system.

    A metric name is ours; a Drive document, a web page and a friendly name a
    human typed into Home Assistant are not. Anything in the second group is
    data the model reads, never instructions it follows, so it is handed over
    inside an ``<external>`` element that says where it came from.
    """
    return f'<external source="{source}">{text}</external>'


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def specs_for(self, loop: str) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values() if t.loops is None or loop in t.loops]

    async def dispatch(self, ctx: ToolContext, call: ToolCall) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return err(f"unknown tool: {call.name}")

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
