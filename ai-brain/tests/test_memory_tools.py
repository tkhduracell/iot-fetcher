import json

import pytest

from ai_brain.config import load_settings
from ai_brain.llm import ToolCall
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.memory_tools import register_memory_tools


@pytest.fixture
def woken():
    return []


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_memory_tools(reg)
    return reg


@pytest.fixture
def make_ctx(brain_dir, expert_dir, woken):
    memories = {"brain": brain_dir, "energy": expert_dir}

    def _make(loop: str) -> ToolContext:
        return ToolContext(
            loop=loop,
            memory=memories[loop],
            memories=memories,
            settings=load_settings({}),
            wake=woken.append,
        )

    return _make


async def call(registry, ctx, tool, **args):
    return json.loads(await registry.dispatch(ctx, ToolCall(id="1", name=tool, args=args)))


def test_expert_sees_fewer_tools_than_brain(registry):
    brain = {s.name for s in registry.specs_for("brain")}
    expert = {s.name for s in registry.specs_for("energy")}
    assert {"rewrite_goals", "rewrite_identity"} <= brain
    assert brain - expert == {"rewrite_goals", "rewrite_identity"}
    shared = {"append_journal", "write_fact", "read_fact", "list_facts", "send_note", "end_cycle"}
    assert shared <= expert


async def test_every_spec_declares_object_parameters(registry):
    for spec in registry.specs_for("brain"):
        assert spec.parameters["type"] == "object"
        assert isinstance(spec.parameters["properties"], dict)
        assert spec.description.strip()


async def test_journal_and_facts_roundtrip(registry, make_ctx, brain_dir):
    ctx = make_ctx("brain")
    assert (await call(registry, ctx, "append_journal", line="woke up"))["ok"] is True
    assert "woke up" in brain_dir.journal_text(days=1)

    assert (await call(registry, ctx, "write_fact", name="pool", body="28C"))["ok"] is True
    assert (await call(registry, ctx, "read_fact", name="pool"))["result"] == "28C"
    assert (await call(registry, ctx, "list_facts"))["result"] == ["pool"]


async def test_read_fact_missing_is_not_an_error(registry, make_ctx):
    out = await call(registry, make_ctx("brain"), "read_fact", name="nope")
    assert out == {"ok": True, "result": None}


async def test_unsafe_fact_name_is_an_error(registry, make_ctx):
    out = await call(registry, make_ctx("brain"), "write_fact", name="../escape", body="x")
    assert "ValueError" in out["error"]


async def test_send_note_writes_to_target_inbox_and_wakes_it(registry, make_ctx, expert_dir, woken):
    out = await call(registry, make_ctx("brain"), "send_note", to="energy", body="check the spa")
    assert out["ok"] is True
    notes = expert_dir.unread_notes()
    assert len(notes) == 1
    assert notes[0].sender == "brain"
    assert "check the spa" in notes[0].body
    assert woken == ["energy"]


async def test_send_note_to_unknown_loop_errors(registry, make_ctx, woken):
    out = await call(registry, make_ctx("brain"), "send_note", to="mars", body="hi")
    assert "unknown loop" in out["error"]
    assert woken == []


async def test_send_note_to_self_errors(registry, make_ctx, brain_dir, woken):
    out = await call(registry, make_ctx("brain"), "send_note", to="brain", body="hi")
    assert "error" in out
    assert brain_dir.unread_notes() == []
    assert woken == []


async def test_end_cycle_populates_extras(registry, make_ctx):
    ctx = make_ctx("energy")
    out = await call(registry, ctx, "end_cycle", next_wake_minutes=45, summary="all quiet")
    assert out["ok"] is True
    assert ctx.extras["end_cycle"] == (45, "all quiet")


async def test_rewrite_goals_from_expert_is_a_policy_error(registry, make_ctx, brain_dir):
    out = await call(registry, make_ctx("energy"), "rewrite_goals", body="mine now")
    assert out["error"] == "policy: tool rewrite_goals not allowed for loop energy"
    assert brain_dir.goals_text() == ""


async def test_rewrite_goals_and_identity_from_brain(registry, make_ctx, brain_dir):
    ctx = make_ctx("brain")
    assert (await call(registry, ctx, "rewrite_goals", body="# Goals\n- rest"))["ok"] is True
    assert brain_dir.goals_text() == "# Goals\n- rest"
    assert (await call(registry, ctx, "rewrite_identity", body="I am the brain"))["ok"] is True
    assert brain_dir.persona_text() == "I am the brain"
