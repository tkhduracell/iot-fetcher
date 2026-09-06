import logging

import pytest

from ai_brain.config import load_settings
from ai_brain.llm import ToolCall, ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

SPEC = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


@pytest.fixture
def ctx(brain_dir, expert_dir):
    return ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir, "energy": expert_dir},
        settings=load_settings({}),
        wake=lambda name: None,
    )


def echo_tool(name: str = "echo", loops=None) -> Tool:
    async def fn(ctx: ToolContext, args: dict) -> str:
        return ok({"echo": args["x"], "loop": ctx.loop})

    spec = ToolSpec(name=name, description="echo x back", parameters=SPEC)
    return Tool(spec=spec, fn=fn, loops=loops)


def test_ok_wraps_dicts_and_scalars():
    assert ok({"a": 1}) == '{"ok": true, "a": 1}'
    assert ok("hi") == '{"ok": true, "result": "hi"}'
    assert err("boom") == '{"error": "boom"}'


def test_specs_for_filters_by_loop():
    reg = ToolRegistry()
    reg.register(echo_tool("everywhere"))
    reg.register(echo_tool("brainy", loops=frozenset({"brain"})))
    assert [s.name for s in reg.specs_for("brain")] == ["everywhere", "brainy"]
    assert [s.name for s in reg.specs_for("energy")] == ["everywhere"]


async def test_dispatch_runs_the_tool(ctx):
    reg = ToolRegistry()
    reg.register(echo_tool())
    out = await reg.dispatch(ctx, ToolCall(id="1", name="echo", args={"x": "hi"}))
    assert out == '{"ok": true, "echo": "hi", "loop": "brain"}'


async def test_dispatch_unknown_tool(ctx):
    reg = ToolRegistry()
    out = await reg.dispatch(ctx, ToolCall(id="1", name="nope", args={}))
    assert out == '{"error": "unknown tool: nope"}'


async def test_dispatch_refuses_tool_not_allowlisted_for_loop(ctx, caplog):
    reg = ToolRegistry()
    reg.register(echo_tool("brainy", loops=frozenset({"brain"})))
    expert_ctx = ctx.__class__(**{**vars(ctx), "loop": "energy"})
    with caplog.at_level(logging.WARNING):
        out = await reg.dispatch(expert_ctx, ToolCall(id="1", name="brainy", args={"x": "hi"}))
    assert out == '{"error": "policy: tool brainy not allowed for loop energy"}'
    assert any("[policy]" in r.getMessage() for r in caplog.records)


async def test_dispatch_reports_missing_required_args(ctx):
    reg = ToolRegistry()
    reg.register(echo_tool())
    out = await reg.dispatch(ctx, ToolCall(id="1", name="echo", args={}))
    assert "missing required" in out and "x" in out


async def test_dispatch_turns_exceptions_into_errors(ctx, caplog):
    async def boom(ctx: ToolContext, args: dict) -> str:
        raise RuntimeError("kaboom")

    reg = ToolRegistry()
    empty = {"type": "object", "properties": {}}
    spec = ToolSpec(name="boom", description="raises", parameters=empty)
    reg.register(Tool(spec=spec, fn=boom, loops=None))
    with caplog.at_level(logging.WARNING):
        out = await reg.dispatch(ctx, ToolCall(id="1", name="boom", args={}))
    assert out == '{"error": "RuntimeError: kaboom"}'
    assert any(r.exc_info for r in caplog.records)
