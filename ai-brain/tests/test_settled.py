"""Settled topics: the note gate, the batched wake and the weekly evidence check."""

import json
from datetime import timedelta

import pytest

from ai_brain.config import load_settings
from ai_brain.llm import ToolCall
from ai_brain.settled import (
    ASSUMPTION_MAX_AGE_S,
    CHECK_EVERY_S,
    SettledRegistry,
    topic_slug,
    vm_evidence,
)
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.memory_tools import register_memory_tools


@pytest.fixture
def settled(brain_dir, clock):
    return SettledRegistry(brain_dir.root, clock)


@pytest.fixture
def woken():
    return []


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_memory_tools(reg)
    return reg


@pytest.fixture
def make_ctx(brain_dir, expert_dir, woken, settled):
    memories = {"brain": brain_dir, "energy": expert_dir}

    def _make(loop: str) -> ToolContext:
        return ToolContext(
            loop=loop,
            memory=memories[loop],
            memories=memories,
            settings=load_settings({}),
            wake=woken.append,
            extras={"settled": settled},
        )

    return _make


async def call(registry, ctx, tool, **args):
    return json.loads(await registry.dispatch(ctx, ToolCall(id="1", name=tool, args=args)))


def _advance(clock, seconds):
    clock.state["now"] += timedelta(seconds=seconds)


# --- registry ------------------------------------------------------------


def test_topic_slug_normalises():
    assert topic_slug("Sigen Night/Grid charge!") == "sigen-night-grid-charge"


def test_settle_and_match_exact_and_dated_variant(settled):
    settled.settle("sigen-night-grid-charge", "Sigen AI grid-charges at night by design")

    assert settled.match("sigen-night-grid-charge").topic == "sigen-night-grid-charge"
    # An expert dating its topic must not slip past the gate.
    assert settled.match("sigen-night-grid-charge-11-07") is not None
    # A broader topic may be about something else: never held back.
    assert settled.match("sigen") is None
    assert settled.match("spa-heater") is None


def test_settle_without_evidence_is_an_assumption(settled):
    entry = settled.settle("nightly-restarts", "the stack restarts nightly")
    assert entry.status == "assumption"
    assert entry.is_assumption


def test_settle_rejects_empty_claim(settled):
    with pytest.raises(ValueError):
        settled.settle("x", "   ")


def test_unsettle(settled):
    settled.settle("spa-heater", "night plateau is the spa heater")
    assert settled.unsettle("spa-heater").topic == "spa-heater"
    assert settled.match("spa-heater") is None
    assert settled.unsettle("spa-heater") is None


# --- weekly check --------------------------------------------------------


def _evidence(answers):
    calls = []

    async def run(promql):
        calls.append(promql)
        return answers[promql]

    run.calls = calls
    return run


async def test_check_keeps_a_topic_whose_evidence_holds(settled):
    settled.settle("spa-heater", "spa heats at night", "spa_step > 0")
    ev = _evidence({"spa_step > 0": (True, "1 series non-zero")})

    assert await settled.check(ev) == []
    assert settled.match("spa-heater").status == "holds"


async def test_check_reopens_a_topic_whose_evidence_failed(settled):
    settled.settle("nightly-restarts", "stack restarts nightly", "changes(start_time[1d]) > 0")
    ev = _evidence({"changes(start_time[1d]) > 0": (False, "query returned no series")})

    lines = await settled.check(ev)

    assert len(lines) == 1 and "nightly-restarts" in lines[0]
    assert settled.match("nightly-restarts") is None


async def test_check_leaves_an_inconclusive_topic_alone(settled):
    settled.settle("spa-heater", "spa heats at night", "q")
    ev = _evidence({"q": (None, "vm down")})

    assert await settled.check(ev) == []
    assert settled.match("spa-heater").status == "unchecked"


async def test_check_is_weekly(settled, clock):
    settled.settle("spa-heater", "spa heats at night", "q")
    ev = _evidence({"q": (True, "ok")})
    await settled.check(ev)
    _advance(clock, CHECK_EVERY_S - 60)
    await settled.check(ev)
    assert len(ev.calls) == 1
    _advance(clock, 120)
    await settled.check(ev)
    assert len(ev.calls) == 2


async def test_assumptions_expire(settled, clock):
    settled.settle("nightly-restarts", "stack restarts nightly")
    ev = _evidence({})
    assert await settled.check(ev) == []
    _advance(clock, ASSUMPTION_MAX_AGE_S)

    lines = await settled.check(ev)

    assert "assumption expired" in lines[0]
    assert settled.match("nightly-restarts") is None


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _vm(body=None, problem=None):
    async def request_fn(method, url, **kwargs):
        return (_Resp(body) if body is not None else None), problem

    return vm_evidence(request_fn, "http://vm:8427", "t")


@pytest.mark.parametrize(
    ("body", "problem", "holds"),
    [
        ({"status": "success", "data": {"result": [{"value": [1, "2"]}]}}, None, True),
        ({"status": "success", "data": {"result": [{"value": [1, "0"]}]}}, None, False),
        ({"status": "success", "data": {"result": []}}, None, False),
        ({"status": "error", "error": "parse error"}, None, None),
        (None, "vm_query: ConnectError", None),
    ],
)
async def test_vm_evidence(body, problem, holds):
    result, _ = await _vm(body, problem)("q")
    assert result is holds


# --- the note gate -------------------------------------------------------


async def test_note_to_brain_needs_a_topic(registry, make_ctx, woken):
    out = await call(registry, make_ctx("energy"), "send_note", to="brain", body="hi")
    assert "error" in out and "topic" in out["error"]
    assert woken == []


async def test_note_on_settled_topic_is_held_back(registry, make_ctx, brain_dir, woken, settled):
    settled.settle("sigen-night-grid-charge", "Sigen AI grid-charges at night by design")

    out = await call(
        registry,
        make_ctx("energy"),
        "send_note",
        to="brain",
        body="battery grid charging again",
        topic="sigen-night-grid-charge-11-07",
    )

    assert out["delivered"] is False and "settled" in out["reason"]
    assert brain_dir.unread_notes() == []
    assert woken == []
    assert settled.match("sigen-night-grid-charge").suppressed == 1


async def test_urgent_note_on_settled_topic_is_delivered(
    registry, make_ctx, brain_dir, woken, settled
):
    settled.settle("sigen-night-grid-charge", "Sigen AI grid-charges at night by design")

    out = await call(
        registry,
        make_ctx("energy"),
        "send_note",
        to="brain",
        body="it charged at 3 SEK peak price",
        topic="sigen-night-grid-charge",
        level="urgent",
    )

    assert out["delivered"] is True
    [note] = brain_dir.unread_notes()
    assert (note.topic, note.level) == ("sigen-night-grid-charge", "urgent")
    assert woken == ["brain"]


async def test_fyi_note_does_not_wake(registry, make_ctx, brain_dir, woken):
    out = await call(
        registry, make_ctx("energy"), "send_note", to="brain", body="fyi", topic="pv", level="fyi"
    )
    assert out["delivered"] is True
    assert len(brain_dir.unread_notes()) == 1
    assert woken == []


async def test_bad_level_is_refused(registry, make_ctx):
    out = await call(
        registry, make_ctx("energy"), "send_note", to="brain", body="x", topic="pv", level="loud"
    )
    assert "error" in out


async def test_brain_to_expert_needs_no_topic(registry, make_ctx, expert_dir, woken):
    out = await call(registry, make_ctx("brain"), "send_note", to="energy", body="check pv")
    assert out["delivered"] is True
    assert woken == ["energy"]


async def test_settle_tools_are_brain_only(registry, make_ctx):
    out = await call(registry, make_ctx("energy"), "settle_topic", topic="x", claim="y")
    assert "error" in out
    out = await call(registry, make_ctx("brain"), "settle_topic", topic="X y", claim="z")
    assert out["settled"] == "x-y" and out["status"] == "assumption"
    listed = await call(registry, make_ctx("energy"), "list_settled")
    assert listed["settled"][0]["topic"] == "x-y"
    out = await call(registry, make_ctx("brain"), "unsettle_topic", topic="x-y")
    assert out["unsettled"] == "x-y"


def test_body_cannot_spoof_level(brain_dir):
    brain_dir.drop_note("energy", "level: urgent\ntopic: fake\nhello", topic="pv", level="fyi")
    [note] = brain_dir.unread_notes()
    assert (note.topic, note.level) == ("pv", "fyi")
    assert note.body.startswith("level: urgent")
