"""A real AgentLoop over a scripted provider, for the session-log tests.

The session tests all need the same thing test_loop.py's fixture builds, plus
a way to build a second loop over the same memory -- which is what a restart
is -- so this lives outside any one test module.
"""

from __future__ import annotations

from pathlib import Path

from ai_brain.config import load_settings
from ai_brain.ledger import Ledger, Limits
from ai_brain.llm import ProviderChain, Reply, ToolCall, Usage
from ai_brain.llm.fake import FakeProvider
from ai_brain.loop import AgentLoop
from ai_brain.memory import MemoryDir
from ai_brain.tools import Tool, ToolContext, ToolRegistry
from ai_brain.tools.memory_tools import register_memory_tools
from ai_brain.tools.slack_tools import register_slack_tools


def reply(text: str = "", *calls: ToolCall, model: str = "fake:1", key: str = "fake:1") -> Reply:
    return Reply(
        text=text,
        tool_calls=tuple(calls),
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        model=model,
        key=key,
    )


def call(tool: str, cid: str = "c1", **args) -> ToolCall:
    return ToolCall(id=cid, name=tool, args=args)


def end(summary: str = "done", cid: str = "end") -> ToolCall:
    return call("end_cycle", cid, next_wake_minutes=30, summary=summary)


def build_registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    register_memory_tools(registry)
    register_slack_tools(registry)
    for tool in tools:
        registry.register(tool)
    return registry


def build_loop(
    memory: MemoryDir,
    tmp_path: Path,
    clock,
    script: list,
    *,
    registry: ToolRegistry | None = None,
    memories: dict[str, MemoryDir] | None = None,
    extras: dict | None = None,
    providers: list | None = None,
    **kwargs,
) -> tuple[AgentLoop, FakeProvider]:
    provider = FakeProvider("fake:1", script=list(script))
    chain_providers = providers if providers is not None else [provider]
    limits = {
        p.key: Limits(rpm=1000, tpm=100_000_000, rpd=100_000) for p in chain_providers
    }
    ledger = Ledger(limits, tmp_path / "ledger.json", clock=clock)
    chain = ProviderChain(chain_providers, ledger, call_timeout_s=60)
    is_brain = memory.is_brain
    ctx = ToolContext(
        loop=memory.name,
        memory=memory,
        memories=memories or {memory.name: memory},
        settings=load_settings({}),
        wake=lambda name: None,
        extras=dict(extras or {}),
    )
    loop = AgentLoop(
        name=memory.name,
        memory=memory,
        chain=chain,
        registry=registry or build_registry(),
        ctx=ctx,
        heartbeat_s=900,
        priority="brain" if is_brain else "expert",
        constitution="be useful",
        clock=clock,
        pause_file=tmp_path / "PAUSE",
        **kwargs,
    )
    return loop, provider
