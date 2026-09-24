import json

import pytest

from ai_brain.config import load_settings
from ai_brain.llm import ToolCall
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.memory_tools import junk_fact_reason, register_memory_tools


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
    shared = {
        "append_journal",
        "write_fact",
        "read_fact",
        "delete_fact",
        "list_facts",
        "send_note",
        "end_cycle",
        "note_gap",
        "close_gap",
    }
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

    assert (
        await call(
            registry,
            ctx,
            "write_fact",
            name="pool",
            title="Pool temp",
            body="28C at 07:00, same as every morning this week",
        )
    )["ok"] is True
    out = await call(registry, ctx, "read_fact", name="pool")
    assert out == {
        "ok": True,
        "name": "pool",
        "title": "Pool temp",
        "body": "28C at 07:00, same as every morning this week",
    }
    assert (await call(registry, ctx, "list_facts"))["result"] == ["pool"]


async def test_read_fact_missing_is_not_an_error(registry, make_ctx):
    out = await call(registry, make_ctx("brain"), "read_fact", name="nope")
    assert out == {"ok": True, "result": None}


async def test_unsafe_fact_name_is_an_error(registry, make_ctx):
    out = await call(
        registry,
        make_ctx("brain"),
        "write_fact",
        name="../escape",
        title="t",
        body="an escape attempt, rejected before it ever touches disk",
    )
    assert "ValueError" in out["error"]


async def test_write_fact_flags_a_similarly_named_existing_fact(registry, make_ctx):
    ctx = make_ctx("brain")
    await call(
        registry,
        ctx,
        "write_fact",
        name="tibber-bridge-baseline",
        title="t",
        body="baseline draw is steady around 1.2kW overnight",
    )
    out = await call(
        registry,
        ctx,
        "write_fact",
        name="tibber_bridge_baseline",
        title="t",
        body="draw jumped to 4kW overnight and stayed there",
    )

    assert out["ok"] is True
    assert out["similar_existing_facts"] == ["tibber-bridge-baseline"]
    assert "hint" in out


async def test_write_fact_says_nothing_when_nothing_is_similar(registry, make_ctx):
    ctx = make_ctx("brain")
    await call(
        registry,
        ctx,
        "write_fact",
        name="pool_temp",
        title="t",
        body="28C at 07:00, same as every morning this week",
    )
    out = await call(
        registry,
        ctx,
        "write_fact",
        name="volvo_xc40_status",
        title="t",
        body="plugged in and charging at 7.4kW since 22:00",
    )

    assert "similar_existing_facts" not in out
    assert "hint" not in out


async def test_write_fact_overwriting_itself_is_not_flagged_as_similar(registry, make_ctx):
    ctx = make_ctx("brain")
    await call(
        registry,
        ctx,
        "write_fact",
        name="tibber_bridge_status",
        title="t",
        body="bridge has been reporting normally all week",
    )
    out = await call(
        registry,
        ctx,
        "write_fact",
        name="tibber_bridge_status",
        title="t",
        body="bridge stopped reporting again around midnight",
    )

    assert "similar_existing_facts" not in out


async def test_write_fact_does_not_flag_a_single_generic_token(registry, make_ctx):
    """A one-word name (e.g. every persona's own 'goals') must not flag
    against an unrelated longer name that happens to share that one word."""
    ctx = make_ctx("brain")
    await call(
        registry,
        ctx,
        "write_fact",
        name="status",
        title="t",
        body="everything green as of this morning's check",
    )
    out = await call(
        registry,
        ctx,
        "write_fact",
        name="deploy_status_report",
        title="t",
        body="last deploy finished clean, no rollback needed",
    )

    assert "similar_existing_facts" not in out


async def test_write_fact_truncates_a_too_long_title(registry, make_ctx, brain_dir):
    ctx = make_ctx("brain")
    long_title = " ".join(f"word{i}" for i in range(30))
    await call(
        registry,
        ctx,
        "write_fact",
        name="pool",
        title=long_title,
        body="28C at 07:00, same as every morning this week",
    )

    stats = brain_dir.fact_stats()
    assert len(stats[0].title.split()) == 20
    assert stats[0].title.endswith("…")


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


async def test_delete_fact_roundtrip(registry, make_ctx, brain_dir):
    ctx = make_ctx("brain")
    await call(
        registry,
        ctx,
        "write_fact",
        name="pool",
        title="t",
        body="28C at 07:00, same as every morning this week",
    )
    out = await call(registry, ctx, "delete_fact", name="pool")

    assert out["deleted"] == "pool"
    assert brain_dir.list_facts() == []
    assert (await call(registry, ctx, "list_facts"))["result"] == []


async def test_delete_fact_errors_on_a_missing_fact(registry, make_ctx):
    out = await call(registry, make_ctx("brain"), "delete_fact", name="never-written")
    assert "no such fact" in out["error"]


async def test_delete_fact_refuses_a_path(registry, make_ctx):
    out = await call(registry, make_ctx("brain"), "delete_fact", name="../escape")
    assert "error" in out


async def test_an_expert_may_delete_its_own_facts(registry, make_ctx, expert_dir):
    """Every loop curates its own memory; pruning is not a brain-only power."""
    ctx = make_ctx("energy")
    await call(
        registry,
        ctx,
        "write_fact",
        name="usage",
        title="t",
        body="usage spiked to 4kW around 21:00 for no clear reason",
    )
    assert (await call(registry, ctx, "delete_fact", name="usage"))["deleted"] == "usage"
    assert expert_dir.list_facts() == []


async def test_note_gap_is_idempotent_on_the_question(registry, make_ctx, brain_dir):
    ctx = make_ctx("brain")
    first = await call(
        registry, ctx, "note_gap", question="Why is the pool cold?", why="blocks heating plan"
    )
    assert first["ok"] is True
    again = await call(registry, ctx, "note_gap", question="Why is the pool cold?", why="")

    assert again["gap"] == first["gap"]
    gaps = brain_dir.gaps()
    assert len(gaps) == 1
    assert gaps[0].question == "Why is the pool cold?"
    # The second call passed no reason; the first one's must survive it.
    assert gaps[0].why == "blocks heating plan"


async def test_an_expert_may_note_and_close_its_own_gaps(registry, make_ctx, expert_dir):
    """Recording a known unknown is an observation, not a brain-only action."""
    ctx = make_ctx("energy")
    noted = await call(registry, ctx, "note_gap", question="Tariff after March?", why="pricing")
    assert noted["ok"] is True
    assert [g.question for g in expert_dir.gaps()] == ["Tariff after March?"]

    closed = await call(registry, ctx, "close_gap", id=noted["gap"], answer="0.9 SEK/kWh")
    assert closed["closed"] == noted["gap"]
    assert expert_dir.gaps() == []
    assert expert_dir.gaps(include_closed=True)[0].answer == "0.9 SEK/kWh"


async def test_close_gap_errors_on_an_unknown_gap(registry, make_ctx):
    out = await call(registry, make_ctx("brain"), "close_gap", id="never-noted", answer="x")
    assert "no such gap" in out["error"]


async def test_close_gap_on_an_unsafe_id_is_an_error_not_a_crash(registry, make_ctx):
    out = await call(registry, make_ctx("brain"), "close_gap", id="../escape", answer="x")
    assert "not a gap id" in out["error"]
    assert "Error" not in out["error"]


async def test_close_gap_given_the_question_instead_of_the_id_explains_itself(registry, make_ctx):
    out = await call(
        registry, make_ctx("brain"), "close_gap", id="Why is the pool cold?", answer="x"
    )
    assert "not a gap id" in out["error"]


# -- junk_fact_reason (pure function) -----------------------------------


def test_junk_fact_reason_accepts_real_evidence():
    assert junk_fact_reason(
        "pool baseline", "28C at 07:00, holding steady all week so far"
    ) is None


def test_junk_fact_reason_rejects_body_equal_to_title():
    title = "normal readings are worth a look precisely once"
    assert junk_fact_reason(title, title) is not None


def test_junk_fact_reason_rejects_body_equal_to_title_case_and_whitespace_insensitive():
    reason = junk_fact_reason("Pool Baseline", "  pool   baseline  ")
    assert reason is not None


def test_junk_fact_reason_rejects_body_contained_in_title():
    reason = junk_fact_reason("the pool is cold again this week", "pool is cold")
    assert reason is not None


def test_junk_fact_reason_rejects_a_too_short_body():
    reason = junk_fact_reason("pool baseline", "28C, steady")
    assert reason is not None
    assert "chars" in reason


def test_junk_fact_reason_rejects_an_empty_body():
    reason = junk_fact_reason("pool baseline", "   ")
    assert reason is not None


def test_junk_fact_reason_does_not_reject_a_title_appearing_inside_a_longer_body():
    # The other direction -- title as a substring of a long, real body -- is
    # not the failure mode this guards against, and must stay allowed.
    reason = junk_fact_reason(
        "pool baseline",
        "pool baseline has been steady at 28C every morning for the last two weeks",
    )
    assert reason is None


# -- write_fact tool wired to the guard ----------------------------------


async def test_write_fact_tool_refuses_a_body_that_only_repeats_the_title(registry, make_ctx):
    ctx = make_ctx("brain")
    title = "normal readings are worth a look precisely once"
    out = await call(registry, ctx, "write_fact", name="observation", title=title, body=title)

    assert "error" in out
    assert "refused" in out["error"]
    assert ctx.memory.list_facts() == []


async def test_write_fact_tool_refuses_a_too_short_body(registry, make_ctx):
    ctx = make_ctx("brain")
    out = await call(
        registry, ctx, "write_fact", name="observation", title="pool", body="28C, fine"
    )

    assert "error" in out
    assert "refused" in out["error"]
    assert ctx.memory.list_facts() == []


async def test_write_fact_tool_accepts_a_body_with_real_evidence(registry, make_ctx):
    ctx = make_ctx("brain")
    out = await call(
        registry,
        ctx,
        "write_fact",
        name="observation",
        title="pool baseline",
        body="28C at 07:00, holding steady all week so far",
    )

    assert out["ok"] is True
    assert ctx.memory.list_facts() == ["observation"]
