import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_brain.approvals import Approvals
from ai_brain.config import load_settings
from ai_brain.events import EventBus
from ai_brain.ledger import Ledger, Limits
from ai_brain.llm import (
    ChainExhausted,
    Message,
    ProviderChain,
    ProviderError,
    Reply,
    ToolCall,
    ToolSpec,
    Usage,
)
from ai_brain.llm.fake import FakeProvider
from ai_brain.loop import (
    BRAIN_ANGLES,
    CYCLE_INSTRUCTIONS,
    EXPERT_ANGLES,
    MAX_TOOL_RESULT_CHARS,
    STATE_FILENAME,
    AgentLoop,
    CycleResult,
    _angle_for,
)
from ai_brain.tools import Tool, ToolContext, ToolRegistry
from ai_brain.tools.introspect import register_introspect
from ai_brain.tools.memory_tools import register_memory_tools
from ai_brain.tools.slack_tools import register_slack_tools

HEARTBEAT = 900


def reply(
    text: str = "",
    *calls: ToolCall,
    model: str = "fake:1",
    key: str = "",
    prompt_tokens: int = 10,
) -> Reply:
    return Reply(
        text=text,
        tool_calls=tuple(calls),
        usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=5),
        model=model,
        key=key,
    )


def call(name: str, cid: str = "c1", **args) -> ToolCall:
    return ToolCall(id=cid, name=name, args=args)


def _static(text: str):
    """A tool function that always returns the same string."""

    async def _fn(_ctx, _args) -> str:
        return text

    return _fn


def _last_journal_line(memory) -> str:
    """The most recent journal entry, with its ``HH:MM  `` stamp stripped."""
    lines = [ln for ln in memory.journal_text(1).splitlines() if ln and not ln.startswith("## ")]
    return lines[-1].split("  ", 1)[1]


class FakeSlackOut:
    def __init__(self) -> None:
        self.posts: list[str] = []
        self.unanswered: list[str] = []

    async def post(self, topic: str, text: str) -> str:
        self.posts.append(topic)
        return "1.0"

    async def mark_unanswered(self, topic: str) -> None:
        self.unanswered.append(topic)


@pytest.fixture
def wall():
    """A monotonic-ish wall clock the loop reads; tests can advance it."""
    state = {"t": 1_000_000.0}

    def _now() -> float:
        return state["t"]

    _now.state = state
    return _now


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_memory_tools(reg)
    register_slack_tools(reg)
    return reg


@pytest.fixture
def make_loop(brain_dir, expert_dir, registry, wall, tmp_path: Path):
    memories = {"brain": brain_dir, "energy": expert_dir}

    def _make(
        script,
        *,
        pause_file: Path | None = None,
        max_rounds: int = 8,
        raises: BaseException | None = None,
        **kwargs,
    ):
        provider = FakeProvider("fake:1", script=list(script))
        ledger = Ledger(
            {"fake:1": Limits(rpm=100, tpm=1_000_000, rpd=1000)},
            tmp_path / "ledger.json",
            clock=wall,
        )
        # The chain's own call_timeout_s, not the loop's, is what actually
        # bounds one provider call -- AgentLoop.chain_timeout_s is derived
        # from the chain's providers, so a test overriding call_timeout_s to
        # force a fast timeout has to set it on the chain too.
        chain = ProviderChain([provider], ledger, call_timeout_s=kwargs.get("call_timeout_s", 60))
        if raises is not None:

            async def _boom(*_args, **_kwargs):
                raise raises

            chain.complete = _boom  # type: ignore[method-assign]
        ctx = ToolContext(
            loop="brain",
            memory=brain_dir,
            memories=memories,
            settings=load_settings({}),
            wake=lambda name: None,
        )
        loop = AgentLoop(
            name="brain",
            memory=brain_dir,
            chain=chain,
            registry=registry,
            ctx=ctx,
            heartbeat_s=HEARTBEAT,
            priority="brain",
            constitution="be useful",
            clock=wall,
            pause_file=pause_file or (tmp_path / "PAUSE"),
            max_rounds=max_rounds,
            **kwargs,
        )
        return loop, provider

    return _make


# -- happy path --------------------------------------------------------


async def test_two_tool_rounds_then_end_cycle(make_loop, brain_dir):
    loop, provider = make_loop(
        [
            reply("looking", call("list_facts", "a")),
            reply("noting", call("append_journal", "b", line="checked the pool")),
            reply("done", call("end_cycle", "c", next_wake_minutes=30, summary="all quiet")),
        ]
    )

    result = await loop.run_cycle()

    assert isinstance(result, CycleResult)
    assert result.status == "ok"
    assert result.rounds == 3
    assert result.model == "fake:1"
    assert result.next_wake_s == 30 * 60
    assert len(provider.calls) == 3
    journal = brain_dir.journal_text(1)
    assert "[ok] model=fake:1 rounds=3/8 all quiet" in journal
    assert "checked the pool" in journal
    assert loop.last_cycle is result
    assert loop.cycle_counts["ok"] == 1
    # Three replies at 10 prompt / 5 completion each.
    assert loop.token_counts == {"prompt": 30, "completion": 15}
    assert (loop.trace.prompt_tokens, loop.trace.completion_tokens) == (30, 15)


async def test_token_counts_accumulate_across_cycles(make_loop):
    loop, _ = make_loop(
        [
            reply("done", call("end_cycle", "a", next_wake_minutes=30, summary="one")),
            reply("done", call("end_cycle", "b", next_wake_minutes=30, summary="two")),
        ]
    )

    await loop.run_cycle()
    await loop.run_cycle()

    assert loop.token_counts == {"prompt": 20, "completion": 10}
    # The trace is per cycle; the counter is per process.
    assert (loop.trace.prompt_tokens, loop.trace.completion_tokens) == (10, 5)


# -- events -------------------------------------------------------------


async def test_a_round_with_no_tool_calls_publishes_started_then_complete(make_loop):
    bus = EventBus()
    loop, _ = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))],
        events=bus,
    )
    with bus.subscribe() as queue:
        await loop.run_cycle()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    kinds = [e["type"] for e in events]
    # end_cycle has no tool calls of its own, so this one round is complete
    # the instant the reply lands -- no separate tool-dispatch event follows.
    assert kinds == ["round_started", "round_complete", "cycle_ended"]
    assert events[0]["loop"] == "brain"
    assert events[1]["round"]["text"] == "done"
    assert events[1]["round"]["model"] == "fake:1"  # no key: falls back to model
    assert events[2]["status"] == "ok"


async def test_round_records_the_chain_key_that_answered(make_loop):
    end = call("end_cycle", "c", next_wake_minutes=10, summary="s")
    loop, _ = make_loop([reply("done", end, key="lan:qwen3-coder:30b")])
    await loop.run_cycle()
    assert loop.trace.rounds[0].model == "lan:qwen3-coder:30b"


async def test_a_round_with_tool_calls_publishes_complete_after_dispatch(make_loop):
    bus = EventBus()
    loop, _ = make_loop(
        [
            reply("looking", call("list_facts", "a")),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ],
        events=bus,
    )
    with bus.subscribe() as queue:
        await loop.run_cycle()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    kinds = [e["type"] for e in events]
    assert kinds == [
        "round_started",
        "round_complete",
        "round_started",
        "round_complete",
        "cycle_ended",
    ]
    first_complete = events[1]
    assert first_complete["round"]["tool_calls"] == [{"name": "list_facts", "args": {}}]
    assert first_complete["round"]["tool_results"] == [
        {
            "name": "list_facts",
            "result_preview": '{"ok": true, "result": []}',
            "stats": {"chars": 26, "ok": True},
        }
    ]


async def test_a_loop_with_no_bus_runs_normally(make_loop):
    """``events=None`` is the default every existing test already relies on;
    this just says the cycle behaves identically either way."""
    loop, _ = make_loop([reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))])
    assert loop.events is None

    result = await loop.run_cycle()

    assert result.status == "ok"


async def test_a_cycle_that_raises_still_publishes_cycle_ended(make_loop):
    """A round_started with nothing after it must resolve somehow -- this is
    what a live feed's pending "thinking" entry for the loop clears on."""
    bus = EventBus()
    loop, _ = make_loop([], raises=ChainExhausted(retry_at=None), events=bus)
    with bus.subscribe() as queue:
        result = await loop.run_cycle()

    assert result.status == "no_budget"
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    # The call was attempted (round_started) but never answered, so no
    # round_complete follows it -- cycle_ended is what tells a live feed to
    # stop waiting on that round.
    assert [e["type"] for e in events] == ["round_started", "cycle_ended"]
    assert events[-1] == {"type": "cycle_ended", "loop": "brain", "status": "no_budget"}


async def test_the_chain_call_is_logged_against_this_loops_name(make_loop, brain_dir, caplog):
    loop, _ = make_loop([reply("all quiet", call("end_cycle", next_wake_minutes=30))])

    with caplog.at_level("INFO", logger="ai_brain.llm"):
        await loop.run_cycle()

    assert "brain: fake:1 answered (in=10 out=5 tokens)" in caplog.text


async def test_system_and_user_messages_frame_the_cycle(make_loop, brain_dir):
    brain_dir.write_fact("pool", "Pool status", "the pool is a hole with water in it")
    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))]
    )

    await loop.run_cycle()

    messages, tools = provider.calls[0]
    assert messages[0].role == "system"
    assert "be useful" in messages[0].content
    assert "pool" in messages[0].content
    assert "end_cycle" in messages[0].content
    assert messages[1].role == "user"
    assert "Begin your think cycle" in messages[1].content
    assert {t.name for t in tools} == {
        s.name for s in loop.registry.specs_for("brain", loop.ctx.settings)
    }


async def test_tool_results_are_fed_back_as_tool_messages(make_loop):
    loop, provider = make_loop(
        [
            reply("hm", call("list_facts", "a")),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    await loop.run_cycle()

    # calls[i] aliases the one growing conversation, so index from the front:
    # system, user, then assistant/tool for each round.
    convo, _ = provider.calls[-1]
    assert [m.role for m in convo] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assistant, tool_msg = convo[2], convo[3]
    assert assistant.content == "hm"
    assert assistant.tool_calls[0].name == "list_facts"
    assert tool_msg.tool_call_id == "a"
    assert tool_msg.name == "list_facts"
    assert '"ok": true' in tool_msg.content


async def test_thought_signatures_are_carried_into_the_assistant_turn(make_loop):
    """Gemini 3.x rejects an echoed model turn whose signatures were dropped."""
    signed = Reply(
        text="hm",
        tool_calls=(ToolCall(id="a", name="list_facts", args={}, thought_signature="sig-A"),),
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        model="fake:1",
        thought_signature="sig-T",
    )
    loop, provider = make_loop(
        [
            signed,
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    await loop.run_cycle()

    convo, _ = provider.calls[-1]
    assistant = convo[2]
    assert assistant.thought_signature == "sig-T"
    assert assistant.tool_calls[0].thought_signature == "sig-A"


async def test_a_huge_tool_result_is_truncated_before_the_model_sees_it(make_loop, registry):
    """A tool that forgets to bound itself must not swamp the conversation."""
    registry.register(
        Tool(
            spec=ToolSpec(name="firehose", description="returns too much", parameters={}),
            fn=_static("x" * 40_000),
        )
    )
    loop, provider = make_loop(
        [
            reply("hm", call("firehose", "a")),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    await loop.run_cycle()

    convo, _ = provider.calls[-1]
    tool_msg = convo[3]
    assert tool_msg.name == "firehose"
    assert len(tool_msg.content) <= MAX_TOOL_RESULT_CHARS + 100
    dropped = 40_000 - MAX_TOOL_RESULT_CHARS
    assert tool_msg.content.endswith(f"[truncated by loop: {dropped} more chars]")
    assert tool_msg.content.startswith("x" * 100)


async def test_a_small_tool_result_is_left_alone(make_loop, registry):
    registry.register(
        Tool(
            spec=ToolSpec(name="trickle", description="returns a little", parameters={}),
            fn=_static("y" * 100),
        )
    )
    loop, provider = make_loop(
        [
            reply("hm", call("trickle", "a")),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    await loop.run_cycle()

    convo, _ = provider.calls[-1]
    assert convo[3].content == "y" * 100


async def test_compaction_hint_added_when_memory_is_large(make_loop, brain_dir):
    for i in range(45):
        brain_dir.write_fact(f"fact{i}", "x", "x")
    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))]
    )

    await loop.run_cycle()

    messages, _ = provider.calls[0]
    assert "memory is large" in messages[0].content


async def test_notes_are_marked_done_after_the_cycle(make_loop, brain_dir):
    brain_dir.drop_note("energy", "prices are silly high")
    loop, _ = make_loop([reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))])

    await loop.run_cycle()

    assert brain_dir.unread_notes() == []
    assert [p.name for p in brain_dir.done_dir.glob("*.md")]


async def test_reply_without_tool_calls_ends_the_cycle(make_loop):
    loop, _provider = make_loop([reply("nothing to do")])

    result = await loop.run_cycle()

    assert result.status == "ok"
    assert result.rounds == 1
    assert result.next_wake_s == HEARTBEAT


# -- wake clamping -----------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (1, HEARTBEAT // 2),  # below the floor
        (30, 1800),  # inside the band
        (60 * 24, 12 * 3600),  # above the ceiling
    ],
)
async def test_end_cycle_wake_is_clamped(make_loop, minutes, expected):
    loop, _ = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=minutes, summary="s"))]
    )

    result = await loop.run_cycle()

    assert result.next_wake_s == expected


# -- bounds and failures ----------------------------------------------


async def test_max_rounds_stops_a_model_that_never_ends(make_loop, brain_dir):
    """Running out of rounds is its own outcome, not a normal cycle."""
    script = [reply(f"round {i}", call("list_facts", f"c{i}")) for i in range(20)]
    loop, provider = make_loop(script, max_rounds=8)

    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    assert result.rounds == 8
    assert len(provider.calls) == 8
    assert result.next_wake_s == HEARTBEAT
    assert "[max_rounds]" in brain_dir.journal_text(1)
    assert loop.cycle_counts == {"max_rounds": 1}


# -- per-model max rounds -----------------------------------------------


async def test_a_strong_model_is_allowed_past_the_global_max_rounds(make_loop, brain_dir):
    """A cycle answered throughout by a model matching CYCLE_MAX_ROUNDS_BY_MODEL
    keeps going well past the global max_rounds (8 here)."""
    script = [
        reply(f"round {i}", call("list_facts", f"c{i}"), key="gemini:gemini-3.8-flash")
        for i in range(11)
    ]
    script.append(
        reply(
            "done",
            call("end_cycle", "end", next_wake_minutes=30, summary="deep dive"),
            key="gemini:gemini-3.8-flash",
        )
    )
    loop, provider = make_loop(
        script,
        max_rounds=8,
        max_rounds_by_model=[("gemini:*3.8*", 20)],
    )

    result = await loop.run_cycle()

    assert result.status == "ok"
    assert result.rounds == 12
    assert result.cap == 20
    assert "rounds=12/20" in brain_dir.journal_text(1)


async def test_a_mid_cycle_fallback_to_a_weak_model_is_cut_at_its_own_limit(
    make_loop, brain_dir
):
    """A cycle that opens on a strong model (limit 20) and falls back to a weak
    one partway through is cut at the weak model's limit (the global default,
    8) as soon as that model answers -- even though the strong model had
    already carried the cycle past 8 rounds while its own, higher limit was in
    force. The drop itself is rescued one round: since the nudge has not yet
    been sent, round 11 (the first weak one) grants just enough extra room for
    it rather than ending the cycle right there with the model never told it
    was about to be cut off -- see the wrap-up-nudge cap-drop tests below. That
    rescued floor (13) is then sticky for the rest of the cycle: it is not
    recomputed back down to the weak model's own cap (8) on later rounds, or
    the model would have been promised "2 rounds left" and then gotten fewer."""
    strong = [
        reply(f"strong {i}", call("list_facts", f"s{i}"), key="gemini:gemini-3.8-flash")
        for i in range(10)
    ]
    weak = [reply(f"weak {i}", call("list_facts", f"w{i}"), key="ollama:llama3.2:3b") for i in range(10)]
    loop, provider = make_loop(
        strong + weak,
        max_rounds=8,
        max_rounds_by_model=[("gemini:*3.8*", 20)],
    )

    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    # 10 rounds on the strong model (cap 20, never hit) are already in the
    # bank when round 11 -- the first weak one -- lands and drops the model's
    # own cap to 8. That is within WRAP_UP_ROUNDS_BEFORE_CAP (2) of round 11,
    # so the cap-drop rescue fixes floor_cap to 11+2=13, sends the nudge, and
    # that floor is sticky: every later round (still weak, own cap 8) uses
    # max(new_cap, floor_cap) = 13, not the weak model's own, lower cap. The
    # loop keeps going until rounds(13) < effective_cap(13) is false.
    assert result.rounds == 13
    assert len(provider.calls) == 13
    assert result.cap == 13
    assert "rounds=13/13" in brain_dir.journal_text(1)


async def test_the_hard_ceiling_clamps_a_too_generous_per_model_cap(make_loop, brain_dir):
    """CYCLE_MAX_ROUNDS_HARD_CEILING (64) wins even over a matched, larger cap --
    belt and braces against a typo'd env value that validation let through."""
    script = [
        reply(f"round {i}", call("list_facts", f"c{i}"), key="gemini:gemini-3.8-flash")
        for i in range(65)
    ]
    loop, provider = make_loop(
        script,
        max_rounds=8,
        max_rounds_by_model=[("gemini:*3.8*", 1000)],
    )

    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    assert result.rounds == 64
    assert result.cap == 64


async def test_wrap_up_nudge_is_sent_once_two_rounds_before_the_cap(make_loop, brain_dir):
    """The nudge is appended to the conversation exactly once, the round the
    cycle first comes within WRAP_UP_ROUNDS_BEFORE_CAP of its effective cap --
    not once per round after that, even though the full conversation (and so
    the nudge, once added) is resent to the model on every later round."""
    script = [reply(f"round {i}", call("list_facts", f"c{i}")) for i in range(5)]
    script.append(reply("done", call("end_cycle", "end", next_wake_minutes=30, summary="s")))
    loop, provider = make_loop(script, max_rounds=6)

    result = await loop.run_cycle()

    assert result.rounds == 6
    # FakeProvider records (messages, tools) by reference, and the loop keeps
    # appending to that same list object -- so every recorded call ends up
    # showing the *final* conversation, not a snapshot from when it was sent.
    # The one list that is safe to inspect is therefore the last call's, and
    # the nudge must appear in it exactly once however many rounds resent it.
    final_messages = provider.calls[-1][0]
    nudge_indices = [
        i
        for i, msg in enumerate(final_messages)
        if msg.role == "user" and "You have 2 rounds left" in msg.content
    ]
    assert len(nudge_indices) == 1
    # It must have landed after round 4's tool results (cap 6 - 2) and before
    # round 6's assistant turn (the end_cycle call that ends the cycle):
    # messages run system, opening-user, then one assistant+tool(s) pair per
    # round, so the 4th assistant turn is at index 2 + (4-1)*2 = 8 and the
    # 6th at 2 + (6-1)*2 = 12.
    assistant_indices = [i for i, msg in enumerate(final_messages) if msg.role == "assistant"]
    assert assistant_indices[3] < nudge_indices[0] < assistant_indices[5]


async def test_wrap_up_nudge_not_sent_when_the_cycle_ends_well_before_the_cap(
    make_loop, brain_dir
):
    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))],
        max_rounds=8,
    )

    await loop.run_cycle()

    final_messages = provider.calls[-1][0]
    nudges = [
        msg
        for msg in final_messages
        if msg.role == "user" and "You have 2 rounds left" in msg.content
    ]
    assert nudges == []


async def test_a_mid_cycle_cap_drop_still_gets_a_wrap_up_nudge(make_loop, brain_dir):
    """The bug in the cap-drop test above's old expectation: a strong model
    (cap 32-ish, here unbounded by max_rounds_by_model matching everything)
    answers rounds 1-20, then a fallback to a weak 16-cap model lands on round
    21 -- already past the weak cap. Without the rescue the loop would simply
    stop there, the model never having seen the nudge at all. With it, round
    21 is granted just enough room (21+2=23) for the nudge to land and for
    the model to get one more round to act on it."""
    strong = [
        reply(f"strong {i}", call("list_facts", f"s{i}"), key="gemini:strong")
        for i in range(20)
    ]
    # Round 21: the weak model's first answer, already past its own cap (16).
    # It still calls a tool (so the round is not treated as a bare, toolless
    # answer) rather than end_cycle, so the cycle carries on to see the nudge.
    weak = [reply("weak 0", call("list_facts", "w0"), key="weak")]
    weak.append(reply("done", call("end_cycle", "e", next_wake_minutes=10, summary="wrapped up"), key="weak"))
    loop, provider = make_loop(
        strong + weak,
        max_rounds=16,
        max_rounds_by_model=[("gemini:strong", 32)],
    )

    result = await loop.run_cycle()

    # Round 21 (the drop) is rescued to floor_cap=23, which is what lets round
    # 22 happen at all -- without the rescue rounds(21) < effective_cap(16) is
    # already false and the loop stops right there. Round 22 calls end_cycle
    # cleanly having seen the nudge, so the cycle ends "ok", not "max_rounds".
    # floor_cap is sticky, so round 22's effective_cap is max(new_cap=16,
    # floor_cap=23) = 23, not the weak model's own, lower cap -- the model was
    # promised 2 rounds and the cap must not shrink back under it before it
    # gets to use them.
    assert result.status == "ok"
    assert result.rounds == 22
    assert result.cap == 23

    final_messages = provider.calls[-1][0]
    nudges = [msg for msg in final_messages if msg.role == "user" and "rounds left" in msg.content]
    assert len(nudges) == 1
    # Two rounds left at the moment the rescued cap (23) was set and round 21
    # had already been played -- 23 - 21 = 2.
    assert "You have 2 rounds left" in nudges[0].content


async def test_the_cap_drop_rescue_floor_is_sticky_across_further_drops(make_loop, brain_dir):
    """Regression: the rescued floor must not be recomputed on a later round.

    Round 11 drops the cap to a weak model's own limit (8), lands within the
    wrap-up window, and is rescued to floor_cap = 11+2 = 13, with the nudge
    promising "2 rounds left". If effective_cap were naively recomputed as
    max(new_cap, rounds+2) on every later round instead of freezing floor_cap
    at the moment it was first set, round 12 (still the weak model, still cap
    8, rounds now 12) would recompute effective_cap = max(8, 12+2) = 14 --
    coincidentally still growing here, so this alone would not catch the bug.
    The real failure mode is the loop exiting *too early*: with the old,
    non-sticky code, round 12's cap was max(new_cap, rounds+2) computed fresh
    ignoring the floor already promised, which in the shape below collapses
    back toward the weak model's own low cap once nudge_sent gate stops
    re-widening it, cutting the model off after round 12 instead of round 13.
    The provider-call count pins the model actually getting to use both of
    its promised rounds (12 and 13), not being cut off after just one."""
    strong = [
        reply(f"strong {i}", call("list_facts", f"s{i}"), key="gemini:gemini-3.8-flash")
        for i in range(10)
    ]
    # The weak model keeps calling tools (never end_cycle) for far more
    # rounds than the rescue could possibly grant, so however many rounds the
    # loop actually plays is determined purely by the cap logic, not by the
    # script running out.
    weak = [
        reply(f"weak {i}", call("list_facts", f"w{i}"), key="ollama:llama3.2:3b") for i in range(20)
    ]
    loop, provider = make_loop(
        strong + weak,
        max_rounds=8,
        max_rounds_by_model=[("gemini:*3.8*", 20)],
    )

    result = await loop.run_cycle()

    # Round 11 (first weak) rescues floor_cap to 11+2=13 and sends the nudge
    # that round. The model is promised exactly WRAP_UP_ROUNDS_BEFORE_CAP (2)
    # more rounds after that -- rounds 12 and 13 -- and the sticky floor must
    # deliver exactly that, not fewer.
    assert result.status == "max_rounds"
    assert result.rounds == 13
    assert len(provider.calls) == 13
    assert result.cap == 13

    final_messages = provider.calls[-1][0]
    nudge_indices = [
        i
        for i, msg in enumerate(final_messages)
        if msg.role == "user" and "You have 2 rounds left" in msg.content
    ]
    assert len(nudge_indices) == 1
    assistant_indices = [i for i, msg in enumerate(final_messages) if msg.role == "assistant"]
    # The nudge landed after round 11's tool results and the model got two
    # more assistant turns (rounds 12 and 13) after it.
    assert nudge_indices[0] < assistant_indices[-2] < assistant_indices[-1]


async def test_prompt_budget_floor_is_sticky_across_a_later_cap_bump(make_loop, brain_dir):
    """The budget-stop counterpart of the cap-drop regression above: once the
    token budget has fixed floor_cap, the model must get to use exactly the
    WRAP_UP_ROUNDS_BEFORE_CAP (2) rounds the nudge promised it, not be cut off
    after only one because a later round's per-model cap bump reset the
    budget's own math."""
    over_budget = reply("big", call("list_facts", "a"), prompt_tokens=20_000, key="weak")
    # The strong model keeps calling tools well past where the rescue could
    # possibly grant more room, so the round count actually played is
    # governed purely by the sticky floor, not by the script running out.
    strong_after = [
        reply(f"s{i}", call("list_facts", f"s{i}"), prompt_tokens=10, key="gemini:strong")
        for i in range(10)
    ]
    loop, provider = make_loop(
        [over_budget, *strong_after],
        max_rounds=8,
        max_prompt_tokens=10_000,
        max_rounds_by_model=[("gemini:strong", 32)],
    )

    result = await loop.run_cycle()

    # Round 1 crosses the budget (20000 > 10000): floor_cap = 1+2 = 3, nudge
    # sent that round promising 2 rounds left. Rounds 2 and 3 answer on the
    # "strong" model, whose own CYCLE_MAX_ROUNDS_BY_MODEL entry (32) must not
    # be allowed to either grow the cap past 3 or -- the actual regression --
    # cause the loop to stop before round 3 is even played.
    assert result.status == "max_rounds"
    assert result.rounds == 3
    assert len(provider.calls) == 3
    assert result.cap == 3

    final_messages = provider.calls[-1][0]
    nudges = [msg for msg in final_messages if msg.role == "user" and "rounds left" in msg.content]
    assert len(nudges) == 1
    assert "You have 2 rounds left" in nudges[0].content


async def test_the_cap_drop_rescue_never_exceeds_the_hard_ceiling(make_loop, brain_dir):
    """A drop that lands within WRAP_UP_ROUNDS_BEFORE_CAP of the hard ceiling
    itself must not be rescued past it -- the ceiling is belt-and-braces over
    every other rule, the rescue included."""
    from ai_brain.config import CYCLE_MAX_ROUNDS_HARD_CEILING

    script = [
        reply(f"round {i}", call("list_facts", f"c{i}"), key="gemini:strong")
        for i in range(CYCLE_MAX_ROUNDS_HARD_CEILING - 1)
    ]
    # The last round before the loop would stop anyway answers on a model with
    # no match, dropping the cap to the plain default (8) -- deep within the
    # rescue's window this close to the ceiling.
    script.append(reply(f"round {CYCLE_MAX_ROUNDS_HARD_CEILING - 1}", call("list_facts", "last")))
    loop, provider = make_loop(
        script,
        max_rounds=8,
        max_rounds_by_model=[("gemini:strong", CYCLE_MAX_ROUNDS_HARD_CEILING)],
    )

    result = await loop.run_cycle()

    assert result.cap <= CYCLE_MAX_ROUNDS_HARD_CEILING
    assert result.rounds <= CYCLE_MAX_ROUNDS_HARD_CEILING


def test_wrap_up_nudge_text_reports_the_real_remaining_count():
    from ai_brain.loop import _wrap_up_nudge

    assert _wrap_up_nudge(2) == (
        "You have 2 rounds left: write what you found (append_journal / "
        "write_fact) and call end_cycle now."
    )
    assert _wrap_up_nudge(1) == (
        "You have 1 round left: write what you found (append_journal / "
        "write_fact) and call end_cycle now."
    )
    assert _wrap_up_nudge(5) == (
        "You have 5 rounds left: write what you found (append_journal / "
        "write_fact) and call end_cycle now."
    )


# -- per-cycle prompt-token budget ---------------------------------------


async def test_prompt_budget_sends_the_nudge_then_stops_within_the_wrap_up_window(
    make_loop, brain_dir
):
    """Crossing CYCLE_MAX_PROMPT_TOKENS mid-cycle behaves like a cap drop: the
    nudge is sent (if not already), at most WRAP_UP_ROUNDS_BEFORE_CAP more
    rounds are allowed, then the cycle stops with status max_rounds and a
    summary naming the token budget rather than the round cap."""
    # 5 rounds of 3000 prompt tokens each: the 4th round (12000) crosses a
    # budget of 10000, at which point the nudge fires and at most 2 more
    # rounds are allowed (rounds 4 and 5) -- round 5 is scripted to keep
    # calling tools rather than end_cycle, so the cycle is cut off there.
    script = [
        reply(f"round {i}", call("list_facts", f"c{i}"), prompt_tokens=3000) for i in range(6)
    ]
    loop, provider = make_loop(script, max_rounds=20, max_prompt_tokens=10_000)

    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    # Budget crossed on round 4 (running total 12000); rescued to
    # rounds(4)+WRAP_UP_ROUNDS_BEFORE_CAP(2)=6, so round 6 is the last one
    # played.
    assert result.rounds == 6
    assert result.cap == 6
    journal = brain_dir.journal_text(1)
    assert "CYCLE_MAX_PROMPT_TOKENS" in journal
    assert "rounds=6/6" in journal

    final_messages = provider.calls[-1][0]
    nudges = [msg for msg in final_messages if msg.role == "user" and "rounds left" in msg.content]
    assert len(nudges) == 1


async def test_prompt_budget_of_zero_disables_the_check(make_loop, brain_dir):
    """0 is the documented off switch: even a running total that would
    obviously have crossed any real budget never triggers the nudge or an
    early stop."""
    script = [
        reply(f"round {i}", call("list_facts", f"c{i}"), prompt_tokens=100_000) for i in range(3)
    ]
    script.append(reply("done", call("end_cycle", "e", next_wake_minutes=10, summary="s")))
    loop, provider = make_loop(script, max_rounds=8, max_prompt_tokens=0)

    result = await loop.run_cycle()

    assert result.status == "ok"
    final_messages = provider.calls[-1][0]
    nudges = [msg for msg in final_messages if msg.role == "user" and "rounds left" in msg.content]
    assert nudges == []


async def test_prompt_budget_does_not_fire_when_never_crossed(make_loop, brain_dir):
    script = [reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"), prompt_tokens=10)]
    loop, provider = make_loop(script, max_rounds=8, max_prompt_tokens=10_000)

    result = await loop.run_cycle()

    assert result.status == "ok"


async def test_prompt_budget_stop_is_sticky_even_if_a_later_model_would_raise_the_cap(
    make_loop, brain_dir
):
    """Once the token budget has stopped a cycle, a per-model cap bump on a
    later round (a fallback to a model matching a generous
    CYCLE_MAX_ROUNDS_BY_MODEL entry) must not undo the stop -- the budget
    reason stays in force for the rest of the cycle."""
    over_budget = reply("big", call("list_facts", "a"), prompt_tokens=20_000, key="weak")
    strong_after = [
        reply(f"s{i}", call("list_facts", f"s{i}"), prompt_tokens=10, key="gemini:strong")
        for i in range(5)
    ]
    loop, provider = make_loop(
        [over_budget, *strong_after],
        max_rounds=8,
        max_prompt_tokens=10_000,
        max_rounds_by_model=[("gemini:strong", 32)],
    )

    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    # Budget crossed on round 1 (20000 > 10000): rescued to 1+2=3, and even
    # though round 2 onward answers on the "strong" model (cap 32 if the
    # budget stop were not sticky), the cap never grows back past 3.
    assert result.rounds == 3
    assert result.cap == 3
    assert "CYCLE_MAX_PROMPT_TOKENS" in brain_dir.journal_text(1)


async def test_chain_exhausted_backs_off_until_retry_at(make_loop, wall, brain_dir):
    loop, _ = make_loop([], raises=ChainExhausted(retry_at=wall() + 300))

    result = await loop.run_cycle()

    assert result.status == "no_budget"
    assert result.next_wake_s == 300
    assert result.rounds == 0
    assert "[no_budget]" in brain_dir.journal_text(1)


async def test_chain_exhausted_never_waits_less_than_a_minute(make_loop, wall):
    loop, _ = make_loop([], raises=ChainExhausted(retry_at=wall() + 2))

    result = await loop.run_cycle()

    assert result.next_wake_s == 60


async def test_chain_exhausted_without_retry_at_uses_heartbeat(make_loop):
    loop, _ = make_loop([], raises=ChainExhausted(retry_at=None))

    result = await loop.run_cycle()

    assert result.status == "no_budget"
    assert result.next_wake_s == HEARTBEAT


async def test_bad_request_is_an_error_the_loop_survives(make_loop, brain_dir):
    loop, _ = make_loop([ProviderError("schema is wrong", kind="bad_request")])

    result = await loop.run_cycle()

    assert result.status == "error"
    assert result.next_wake_s == HEARTBEAT
    assert "[error]" in brain_dir.journal_text(1)
    assert loop.cycle_counts["error"] == 1


async def test_a_slow_provider_times_out(make_loop, brain_dir, monkeypatch):
    loop, provider = make_loop([reply("never arrives")], call_timeout_s=0.01)

    async def hang(*args, **kwargs):
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    monkeypatch.setattr(provider, "complete", hang)

    result = await loop.run_cycle()

    assert result.status == "timeout"
    assert result.next_wake_s == HEARTBEAT
    assert "[timeout]" in brain_dir.journal_text(1)


def test_chain_timeout_s_is_the_exact_worst_case_of_every_provider(make_loop):
    """Not a flat multiple: each provider's own timeout x its own attempts."""
    loop, provider = make_loop([])
    # The single fake provider from make_loop: default max_attempts (2), no
    # call_timeout_s override, so it falls back to the chain's own (60s here,
    # since the chain was built with call_timeout_s=60 by the fixture).
    assert provider.max_attempts == 2
    assert loop.chain_timeout_s == 60 * 2


def test_chain_timeout_s_accounts_for_a_slower_providers_own_override(tmp_path, wall, registry):
    """A lan:-style provider's longer timeout must not be invisible to the loop."""
    from ai_brain.config import load_settings
    from ai_brain.ledger import Ledger, Limits
    from ai_brain.llm.fake import FakeProvider
    from ai_brain.tools import ToolContext

    class SlowProvider(FakeProvider):
        max_attempts = 3
        call_timeout_s = 900.0

    fast = FakeProvider("fast:1", script=[])
    slow = SlowProvider("slow:1", script=[])
    ledger = Ledger(
        {"fast:1": Limits(rpm=100, tpm=1_000_000, rpd=1000), "slow:1": Limits(rpm=100, tpm=1, rpd=1)},
        tmp_path / "ledger.json",
        clock=wall,
    )
    chain = ProviderChain([fast, slow], ledger, call_timeout_s=60)
    ctx = ToolContext(
        loop="brain", memory=None, memories={}, settings=load_settings({}), wake=lambda name: None
    )
    loop = AgentLoop(
        name="brain",
        memory=None,
        chain=chain,
        registry=registry,
        ctx=ctx,
        heartbeat_s=HEARTBEAT,
        priority="brain",
        constitution="be useful",
        clock=wall,
        pause_file=tmp_path / "PAUSE",
    )

    # fast: 60s x 2 attempts (the chain's default) + slow: 900s x 3 attempts
    # (its own override) -- neither the chain's flat default nor a fixed
    # multiplier of it would produce this.
    assert loop.chain_timeout_s == 60 * 2 + 900 * 3


async def test_pause_file_skips_the_provider_entirely(make_loop, brain_dir, tmp_path):
    pause = tmp_path / "PAUSE"
    pause.write_text("stop", encoding="utf-8")
    loop, provider = make_loop([reply("should not run")], pause_file=pause)

    result = await loop.run_cycle()

    assert result.status == "paused"
    assert result.rounds == 0
    assert result.next_wake_s == HEARTBEAT
    assert provider.calls == []
    assert "[paused]" in brain_dir.journal_text(1)


async def test_a_note_survives_a_budgetless_cycle(make_loop, brain_dir):
    """A cycle that never reached the model must not archive the note unseen."""
    brain_dir.drop_note("filip", "when is the pool warm enough?")
    loop, _ = make_loop([], raises=ChainExhausted(retry_at=None))

    result = await loop.run_cycle()

    assert result.status == "no_budget"
    assert result.rounds == 0
    # The model never saw it, so it stays in the inbox for the next cycle.
    assert [n.body for n in brain_dir.unread_notes()] == ["when is the pool warm enough?"]
    assert list(brain_dir.done_dir.glob("*.md")) == []
    assert "(1 notes left unread)" in _last_journal_line(brain_dir)


async def test_a_note_survives_a_paused_cycle(make_loop, brain_dir, tmp_path):
    """PAUSE means no provider call at all, so the inbox is untouched."""
    pause = tmp_path / "PAUSE"
    pause.write_text("stop", encoding="utf-8")
    brain_dir.drop_note("filip", "are you awake?")
    loop, _ = make_loop([reply("should not run")], pause_file=pause)

    result = await loop.run_cycle()

    assert result.status == "paused"
    assert [n.body for n in brain_dir.unread_notes()] == ["are you awake?"]
    assert list(brain_dir.done_dir.glob("*.md")) == []
    assert "(1 notes left unread)" in _last_journal_line(brain_dir)


async def test_a_note_is_archived_after_a_successful_cycle(make_loop, brain_dir):
    brain_dir.drop_note("energy", "important")
    loop, _ = make_loop([reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))])

    result = await loop.run_cycle()

    assert result.status == "ok"
    assert brain_dir.unread_notes() == []
    assert [p.name for p in brain_dir.done_dir.glob("*.md")]
    assert "left unread" not in _last_journal_line(brain_dir)


async def test_a_note_is_archived_after_a_timed_out_cycle(make_loop, brain_dir, monkeypatch):
    brain_dir.drop_note("energy", "important")
    loop, provider = make_loop([reply("never arrives")], call_timeout_s=0.01)

    async def hang(*args, **kwargs):
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    monkeypatch.setattr(provider, "complete", hang)

    await loop.run_cycle()

    assert brain_dir.unread_notes() == []
    assert [p.name for p in brain_dir.done_dir.glob("*.md")]
    assert _last_journal_line(brain_dir).startswith("[timeout]")


async def test_a_note_is_archived_after_an_errored_cycle(make_loop, brain_dir):
    """The model saw the inbox, so a note that broke the cycle must not loop."""
    brain_dir.drop_note("energy", "important")
    loop, _ = make_loop([ProviderError("schema is wrong", kind="bad_request")])

    await loop.run_cycle()

    assert brain_dir.unread_notes() == []
    assert [p.name for p in brain_dir.done_dir.glob("*.md")]
    assert _last_journal_line(brain_dir).startswith("[error]")


# -- answering Filip ---------------------------------------------------


def test_cycle_instructions_tell_the_model_to_answer_filip_on_slack():
    """The journal is not a reply: Filip only ever reads Slack."""
    assert "slack_post" in CYCLE_INSTRUCTIONS
    assert "cannot see your journal" in CYCLE_INSTRUCTIONS


async def test_a_note_from_filip_reaches_the_model_with_the_slack_guidance(make_loop, brain_dir):
    brain_dir.drop_note("filip", "topic: pool -- how warm is it?")
    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))]
    )

    await loop.run_cycle()

    system = provider.calls[0][0][0].content
    assert "from: filip" in system
    assert "how warm is it?" in system
    assert "slack_post" in system


def test_cycle_instructions_say_a_statement_still_gets_a_reply():
    """"18 is the floor" is a correction, not a question -- it still needs an answer."""
    assert "Every note from him gets a slack_post reply" in CYCLE_INSTRUCTIONS


async def test_end_cycle_is_refused_once_while_filip_waits(make_loop, brain_dir):
    brain_dir.drop_note("filip", "topic: bedroom-heater\n18 is the lower bar.")
    loop, provider = make_loop(
        [
            reply("", call("end_cycle", "c1", next_wake_minutes=10, summary="quiet")),
            reply("", call("slack_post", "c2", topic="bedroom-heater", text="Noted.")),
            reply("", call("end_cycle", "c3", next_wake_minutes=10, summary="answered")),
        ]
    )
    slack = FakeSlackOut()
    loop.ctx.extras["slack_out"] = slack

    result = await loop.run_cycle()

    assert result.status == "ok"
    tool_results = [m.content for m in provider.calls[-1][0] if m.role == "tool"]
    assert "bedroom-heater" in tool_results[0] and "slack_post" in tool_results[0]
    assert len(provider.calls) == 3
    assert slack.posts == ["bedroom-heater"]
    assert slack.unanswered == []
    assert "answered" in _last_journal_line(brain_dir)


async def test_a_second_end_cycle_is_honoured_and_the_note_is_marked_unanswered(
    make_loop, brain_dir
):
    brain_dir.drop_note("filip", "topic: bedroom-heater\n18 is the lower bar.")
    loop, _ = make_loop(
        [
            reply("", call("end_cycle", "c1", next_wake_minutes=10, summary="quiet")),
            reply("", call("end_cycle", "c2", next_wake_minutes=10, summary="still quiet")),
        ]
    )
    slack = FakeSlackOut()
    loop.ctx.extras["slack_out"] = slack

    result = await loop.run_cycle()

    assert result.status == "ok"
    assert slack.unanswered == ["bedroom-heater"]
    assert brain_dir.unread_notes() == []


async def test_a_bare_dm_is_answered_by_any_topic(make_loop, brain_dir):
    brain_dir.drop_note("filip", "how warm is the pool?")
    loop, provider = make_loop(
        [
            reply("", call("slack_post", "c1", topic="pool-temp", text="21 C")),
            reply("", call("end_cycle", "c2", next_wake_minutes=10, summary="s")),
        ]
    )
    slack = FakeSlackOut()
    loop.ctx.extras["slack_out"] = slack

    await loop.run_cycle()

    assert len(provider.calls) == 2
    assert slack.unanswered == []


async def test_notes_from_experts_are_not_owed_a_reply(make_loop, brain_dir):
    brain_dir.drop_note("energy", "prices spike at 18")
    loop, provider = make_loop(
        [reply("", call("end_cycle", "c1", next_wake_minutes=10, summary="s"))]
    )
    slack = FakeSlackOut()
    loop.ctx.extras["slack_out"] = slack

    await loop.run_cycle()

    assert len(provider.calls) == 1
    assert slack.unanswered == []


async def test_a_note_that_arrives_mid_cycle_is_not_marked_by_that_cycle(make_loop, brain_dir):
    async def drop(_ctx, _args):
        brain_dir.drop_note("filip", "topic: pool\nlate arrival")
        return "ok"

    loop, _ = make_loop(
        [
            reply("", call("poke", "c1")),
            reply("", call("end_cycle", "c2", next_wake_minutes=10, summary="s")),
        ]
    )
    loop.registry.register(
        Tool(spec=ToolSpec(name="poke", description="", parameters={}), fn=drop, loops=None)
    )
    slack = FakeSlackOut()
    loop.ctx.extras["slack_out"] = slack

    await loop.run_cycle()

    assert slack.unanswered == []
    assert [n.body for n in brain_dir.unread_notes()] == ["topic: pool\nlate arrival"]


def test_slack_post_says_it_is_the_only_way_filip_hears_from_you(registry):
    spec = next(s for s in registry.specs_for("brain") if s.name == "slack_post")
    assert "only way" in spec.description


# -- run_forever -------------------------------------------------------


async def test_wake_event_interrupts_the_sleep(make_loop):
    script = [
        reply("a", call("end_cycle", "c", next_wake_minutes=600, summary="first")),
        reply("b", call("end_cycle", "c", next_wake_minutes=600, summary="second")),
    ]
    loop, provider = make_loop(script)

    task = asyncio.create_task(loop.run_forever())
    for _ in range(200):
        await asyncio.sleep(0)
        if loop.cycle_counts.get("ok"):
            break
    assert loop.cycle_counts["ok"] == 1

    loop.wake.set()
    for _ in range(200):
        await asyncio.sleep(0)
        if loop.cycle_counts.get("ok", 0) >= 2:
            break

    task.cancel()
    assert loop.cycle_counts["ok"] == 2
    assert len(provider.calls) == 2
    assert not loop.wake.is_set()


async def test_run_forever_survives_a_crashing_cycle(make_loop, monkeypatch):
    loop, _ = make_loop([])
    calls = {"n": 0}

    async def boom() -> CycleResult:
        calls["n"] += 1
        raise RuntimeError("cycle blew up")

    monkeypatch.setattr(loop, "run_cycle", boom)
    monkeypatch.setattr(loop, "heartbeat_s", 0)

    task = asyncio.create_task(loop.run_forever())
    for _ in range(500):
        await asyncio.sleep(0)
        if calls["n"] >= 3:
            break
    task.cancel()

    assert calls["n"] >= 3


async def test_last_cycle_at_records_the_clock(make_loop, wall):
    loop, _ = make_loop([reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))])

    await loop.run_cycle()

    assert loop.last_cycle_at == wall()


async def test_messages_are_rebuilt_each_cycle(make_loop):
    loop, provider = make_loop(
        [
            reply("a", call("end_cycle", "c", next_wake_minutes=10, summary="first")),
            reply("b", call("end_cycle", "c", next_wake_minutes=10, summary="second")),
        ]
    )

    await loop.run_cycle()
    await loop.run_cycle()

    # FakeProvider records the same mutable list it was handed, so length says
    # nothing; what matters is that the second cycle opened its own system
    # message, freshly rebuilt from a journal that now mentions the first.
    second, _ = provider.calls[1]
    assert isinstance(second[0], Message)
    assert second[0].role == "system"
    assert second[1].role == "user"
    assert "first" in second[0].content  # the previous cycle shows up via the journal
    assert "second" not in second[0].content


async def test_a_cancelled_cycle_still_closes_its_books(make_loop, brain_dir):
    """Shutdown cancels the task mid-call; the books close, the note waits."""
    brain_dir.drop_note("energy", "mid-flight")
    loop, provider = make_loop([reply("never arrives")])

    async def hang(*args, **kwargs):
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    provider.complete = hang

    task = asyncio.create_task(loop.run_cycle())
    for _ in range(50):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "[cancelled]" in brain_dir.journal_text(1)
    # Cut short mid-thought: the note may never have been acted on, so the
    # next run must see it again rather than find it archived.
    assert [n.body for n in brain_dir.unread_notes()] == ["mid-flight"]


async def test_cancellation_does_not_lose_the_journal_when_slack_is_wired(make_loop, brain_dir):
    """_finish runs inside a cancelled task with slack wired; books close regardless."""
    loop, provider = make_loop([reply("never arrives")])
    loop.ctx.extras["slack_out"] = FakeSlackOut()

    async def hang(*args, **kwargs):
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    provider.complete = hang

    task = asyncio.create_task(loop.run_cycle())
    for _ in range(50):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "[cancelled]" in brain_dir.journal_text(1)
    assert loop.cycle_counts["cancelled"] == 1


# -- a slow provider must not cost the cycle ---------------------------------


async def test_cycle_stays_ok_when_the_second_provider_answers(
    brain_dir, expert_dir, registry, wall, tmp_path
):
    """A first provider that hangs is the fallback's cue, not a failed cycle.

    The loop's own timeout is a multiple of the per-call one, so the chain has
    room to give up on the slow provider and let the next one answer.
    """

    class Slow(FakeProvider):
        async def complete(self, messages, tools, max_tokens):
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    slow = Slow("fake:slow", script=[])
    fast = FakeProvider(
        "fake:fast",
        [
            Reply(
                text="done",
                tool_calls=(
                    ToolCall(
                        id="c",
                        name="end_cycle",
                        args={"next_wake_minutes": 30, "summary": "all quiet"},
                    ),
                ),
                usage=Usage(prompt_tokens=10, completion_tokens=5),
                model="fake:fast",
            )
        ],
    )
    ledger = Ledger(
        {
            "fake:slow": Limits(rpm=100, tpm=1_000_000, rpd=1000),
            "fake:fast": Limits(rpm=100, tpm=1_000_000, rpd=1000),
        },
        tmp_path / "ledger.json",
        clock=wall,
    )
    chain = ProviderChain([slow, fast], ledger, call_timeout_s=0.01)
    ctx = ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir, "energy": expert_dir},
        settings=load_settings({}),
        wake=lambda name: None,
    )
    loop = AgentLoop(
        name="brain",
        memory=brain_dir,
        chain=chain,
        registry=registry,
        ctx=ctx,
        heartbeat_s=HEARTBEAT,
        priority="brain",
        constitution="be useful",
        clock=wall,
        pause_file=tmp_path / "PAUSE",
        call_timeout_s=0.01,
    )

    result = await loop.run_cycle()

    assert result.status == "ok"
    assert result.model == "fake:fast"
    # The abandoned calls were still charged to the slow key.
    assert ledger.snapshot()["buckets"]["fake:slow"]["requests_day"] == 2


async def test_finish_prunes_the_journal(make_loop, brain_dir, wall):
    """Pruning runs in the finally, before the next cycle reads needs_compaction."""
    from datetime import UTC, datetime, timedelta

    now = datetime.fromtimestamp(wall(), UTC)
    stale = (now - timedelta(days=40)).strftime("%Y-%m-%d")
    (brain_dir.journal_dir / f"{stale}.md").write_text("old\n", encoding="utf-8")

    loop, _ = make_loop([reply("done", call("end_cycle", "c", next_wake_minutes=30, summary="s"))])
    await loop.run_cycle()

    assert not (brain_dir.journal_dir / f"{stale}.md").exists()


# -- mid-cycle pause, inbox snapshot, wake race -----------------------


async def test_pause_created_mid_cycle_stops_before_the_next_round(
    make_loop, registry, brain_dir, tmp_path
):
    """A tool drops PAUSE during round 1; round 2 must not call the provider."""
    pause = tmp_path / "PAUSE"

    async def _pauses(_ctx, _args) -> str:
        pause.write_text("stop", encoding="utf-8")
        return "paused"

    registry.register(
        Tool(
            spec=ToolSpec(name="drop_pause", description="d", parameters={"type": "object"}),
            fn=_pauses,
        )
    )
    script = [reply("one", call("drop_pause", "c1")), reply("two", call("list_facts", "c2"))]
    loop, provider = make_loop(script, pause_file=pause)

    result = await loop.run_cycle()

    assert result.status == "paused"
    assert result.rounds == 1
    assert len(provider.calls) == 1
    assert _last_journal_line(brain_dir) == "[paused] model=fake:1 rounds=1/8 paused mid-cycle"


async def test_a_note_arriving_mid_context_is_not_rendered_or_consumed(make_loop, brain_dir):
    """read_context renders the inbox the loop already read, not a fresh one.

    Otherwise a note dropped between unread_notes() and read_context() is shown
    to the model and then left unread -- shown again on the next cycle, having
    already been answered.
    """
    brain_dir.drop_note("energy", "first note")
    original = brain_dir.unread_notes

    def read_then_race():
        notes = original()
        brain_dir.drop_note("energy", "raced in late")
        brain_dir.unread_notes = original
        return notes

    brain_dir.unread_notes = read_then_race
    loop, provider = make_loop([reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))])

    await loop.run_cycle()

    system = provider.calls[0][0][0].content
    assert "first note" in system
    assert "raced in late" not in system
    assert [n.body for n in brain_dir.unread_notes()] == ["raced in late"]


async def test_a_wake_rung_as_the_sleep_ends_survives_to_the_next_sleep(make_loop):
    """The bell is cleared before the wait, so a ring in the gap is not lost.

    The old order cleared *after* the wait: a wake that landed between wait_for
    returning and the clear was swallowed, and since the note was already in
    the inbox nothing would ring again -- the brain slept a whole heartbeat on
    a message it had been told about.
    """
    loop, _ = make_loop([])

    # A sleep that ends on its timeout, with a ring arriving in the same tick.
    await asyncio.wait_for(loop._sleep(0), timeout=1)
    loop.wake.set()
    assert loop.wake.is_set()  # the timed-out sleep consumed nothing

    # The next sleep must see that ring rather than wait out its timeout.
    await asyncio.wait_for(loop._sleep(30), timeout=1)
    assert not loop.wake.is_set()


async def test_a_wake_during_a_sleep_still_interrupts_it(make_loop):
    loop, _ = make_loop([])
    sleeping = asyncio.create_task(loop._sleep(30))
    await asyncio.sleep(0)
    loop.wake.set()
    await asyncio.wait_for(sleeping, timeout=1)


# -- live trace --------------------------------------------------------


async def test_no_trace_before_the_first_cycle(make_loop):
    loop, _ = make_loop([])
    assert loop.trace is None


async def test_trace_records_rounds_calls_and_results(make_loop):
    loop, _ = make_loop(
        [
            reply("looking", call("list_facts", "a")),
            reply("noting", call("append_journal", "b", line="checked the pool")),
            reply("done", call("end_cycle", "c", next_wake_minutes=30, summary="all quiet")),
        ]
    )

    await loop.run_cycle()

    trace = loop.trace
    assert trace is not None
    assert trace.status == "ok"
    assert trace.model == "fake:1"
    assert trace.summary == "all quiet"
    assert trace.finished_at is not None
    assert trace.in_progress is False
    assert [r.text for r in trace.rounds] == ["looking", "noting", "done"]
    assert [c["name"] for r in trace.rounds for c in r.tool_calls] == [
        "list_facts",
        "append_journal",
        "end_cycle",
    ]
    assert trace.rounds[1].tool_calls[0]["args"] == {"line": "checked the pool"}
    assert [r.tool_results[0]["name"] for r in trace.rounds] == [
        "list_facts",
        "append_journal",
        "end_cycle",
    ]
    assert '"ok": true' in trace.rounds[0].tool_results[0]["result_preview"]


async def test_a_paused_cycle_still_leaves_a_closed_trace(make_loop, tmp_path):
    pause = tmp_path / "PAUSE"
    pause.write_text("stop", encoding="utf-8")
    loop, _ = make_loop([reply("should not run")], pause_file=pause)

    await loop.run_cycle()

    trace = loop.trace
    assert trace is not None
    assert trace.status == "paused"
    assert trace.rounds == []
    assert trace.in_progress is False


async def test_an_errored_cycle_closes_its_trace(make_loop):
    loop, _ = make_loop([ProviderError("schema is wrong", kind="bad_request")])

    await loop.run_cycle()

    assert loop.trace is not None
    assert loop.trace.status == "error"
    assert loop.trace.in_progress is False


async def test_the_trace_is_replaced_each_cycle(make_loop):
    loop, _ = make_loop(
        [
            reply("one", call("end_cycle", "c", next_wake_minutes=10, summary="first")),
            reply("two", call("end_cycle", "c", next_wake_minutes=10, summary="second")),
        ]
    )

    await loop.run_cycle()
    first = loop.trace
    await loop.run_cycle()

    assert loop.trace is not first
    assert loop.trace.summary == "second"
    assert len(loop.trace.rounds) == 1


async def test_trace_text_and_results_are_truncated(make_loop, registry):
    registry.register(
        Tool(
            spec=ToolSpec(name="huge", description="d", parameters={"type": "object"}),
            fn=_static("y" * 4_000),
        )
    )
    loop, _ = make_loop(
        [
            reply("x" * 5_000, call("huge", "a", note="z" * 2_000)),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    await loop.run_cycle()

    round_one = loop.trace.rounds[0]
    assert round_one.text.startswith("x" * 2000)
    assert round_one.text.endswith("…[+3000]")
    assert round_one.tool_calls[0]["args"]["note"].endswith("…[+1500]")
    assert len(round_one.tool_results[0]["result_preview"]) <= 500 + len("…[+3500]")
    assert "…[+" in round_one.tool_results[0]["result_preview"]


def test_tool_result_stats_computed_from_the_full_untruncated_result():
    from ai_brain.loop import _tool_result_stats

    body = "line one\nline two\nline three"
    result = json.dumps({"ok": True, "body": body})
    stats = _tool_result_stats(result)
    assert stats == {"chars": len(result), "ok": True, "lines": 3}


def test_tool_result_stats_counts_series_and_hits():
    from ai_brain.loop import _tool_result_stats

    series_result = json.dumps({"ok": True, "series": [{"a": 1}, {"a": 2}, {"a": 3}]})
    assert _tool_result_stats(series_result) == {
        "chars": len(series_result),
        "ok": True,
        "series": 3,
    }

    hits_result = json.dumps({"ok": True, "hits": [{"h": 1}]})
    assert _tool_result_stats(hits_result) == {"chars": len(hits_result), "ok": True, "hits": 1}


def test_tool_result_stats_ok_is_false_on_an_error_envelope():
    from ai_brain.loop import _tool_result_stats

    result = json.dumps({"error": "policy: tool x not allowed"})
    assert _tool_result_stats(result) == {"chars": len(result), "ok": False}


def test_tool_result_stats_skips_the_shape_keys_on_a_parse_failure():
    """A tool result that fails to parse as JSON (plain text, or a truncation
    marker appended past MAX_TOOL_RESULT_CHARS) still gets a chars count --
    the one field that never depends on the parse succeeding."""
    from ai_brain.loop import _tool_result_stats

    assert _tool_result_stats("not json at all") == {"chars": len("not json at all")}
    assert _tool_result_stats("[1, 2, 3]") == {"chars": 9}  # valid JSON, not a dict
    assert _tool_result_stats("") == {"chars": 0}


async def test_tool_results_carry_stats_alongside_the_preview(make_loop, registry):
    registry.register(
        Tool(
            spec=ToolSpec(name="vm_query", description="d", parameters={"type": "object"}),
            fn=_static(json.dumps({"ok": True, "series": [1, 2, 3, 4], "body": "a\nb"})),
        )
    )
    loop, _ = make_loop(
        [
            reply("looking", call("vm_query", "a")),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    await loop.run_cycle()

    stats = loop.trace.rounds[0].tool_results[0]["stats"]
    assert stats["ok"] is True
    assert stats["series"] == 4
    assert stats["lines"] == 2
    assert "hits" not in stats


async def test_tool_result_stats_use_the_full_result_not_the_truncated_preview(
    make_loop, registry
):
    """lines/series/hits must be counted off the tool's real output, never off
    result_preview (capped at TRACE_PREVIEW_CHARS) or the loop's own
    MAX_TOOL_RESULT_CHARS-truncated copy -- both would undercount a result
    long enough to hit either limit."""
    big_series = list(range(50))
    huge_body_lines = "\n".join("x" * 200 for _ in range(200))  # well past both caps
    registry.register(
        Tool(
            spec=ToolSpec(name="huge_query", description="d", parameters={"type": "object"}),
            fn=_static(json.dumps({"ok": True, "series": big_series, "body": huge_body_lines})),
        )
    )
    loop, _ = make_loop(
        [
            reply("looking", call("huge_query", "a")),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    await loop.run_cycle()

    stats = loop.trace.rounds[0].tool_results[0]["stats"]
    assert stats["series"] == 50
    assert stats["lines"] == 200
    # The preview itself did get truncated -- otherwise this test would not
    # be exercising what it claims to.
    assert "…[+" in loop.trace.rounds[0].tool_results[0]["result_preview"]


async def test_the_trace_is_visible_from_inside_a_tool_while_the_cycle_runs(make_loop, registry):
    """The whole point of the trace: an HTTP reader sees an unfinished cycle."""
    seen = {}

    async def _peek(ctx, _args) -> str:
        trace = ctx.extras["loop"].trace
        seen["in_progress"] = trace.in_progress
        seen["finished_at"] = trace.finished_at
        seen["rounds"] = len(trace.rounds)
        return "peeked"

    registry.register(
        Tool(
            spec=ToolSpec(name="peek", description="d", parameters={"type": "object"}),
            fn=_peek,
        )
    )
    loop, _ = make_loop(
        [
            reply("looking", call("peek", "a")),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )
    loop.ctx.extras["loop"] = loop

    await loop.run_cycle()

    assert seen == {"in_progress": True, "finished_at": None, "rounds": 1}


# -- the cycle's angle -------------------------------------------------


def test_angle_rotates_once_per_heartbeat_slot():
    """Every heartbeat slot gets a new angle; a full rotation covers them all."""
    for name, priority, heartbeat_s, angles in (
        ("brain", "brain", 1800, BRAIN_ANGLES),
        ("energy", "expert", 7200, EXPERT_ANGLES),
    ):
        seen = [
            _angle_for(name, priority, 1_000_000.0 + i * heartbeat_s, heartbeat_s)
            for i in range(len(angles))
        ]
        assert set(seen) == set(angles), f"{name}: a full rotation must cover every angle"


def test_consecutive_brain_heartbeats_never_repeat_an_angle():
    """Hourly rotation on a 30-minute heartbeat handed the brain each angle twice."""
    seen = [_angle_for("brain", "brain", 1_000_000.0 + i * 1800, 1800) for i in range(40)]
    assert all(a != b for a, b in zip(seen, seen[1:], strict=False))


def test_a_wake_inside_one_slot_keeps_its_angle():
    slot_start = 1800.0 * 600
    angle = _angle_for("brain", "brain", slot_start, 1800)
    assert _angle_for("brain", "brain", slot_start + 1799, 1800) == angle


def test_loops_waking_together_do_not_share_an_angle():
    """Five loops on one heartbeat taking the same angle is the rut, repeated."""
    now = 1_000_000.0
    angles = {name: _angle_for(name, "expert", now) for name in ("energy", "health", "house-ops")}
    assert len(set(angles.values())) > 1, angles


def test_experts_are_offered_expert_angles():
    # An expert cannot propose or reach Slack, so a brain angle telling it to
    # would be an instruction it can only fail.
    for h in range(24):
        angle = _angle_for("energy", "expert", 1_000_000.0 + h * 3600)
        assert angle in EXPERT_ANGLES
        assert "propose" not in angle


def test_a_restart_does_not_reset_the_rotation():
    """No stored counter: the angle follows the clock, not the process."""
    now = 1_000_000.0 + 5 * 3600
    assert _angle_for("brain", "brain", now) == _angle_for("brain", "brain", now)


async def test_the_user_turn_carries_the_angle(make_loop, wall):
    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))]
    )

    await loop.run_cycle()

    user = provider.calls[0][0][1]
    assert user.role == "user"
    assert "Angle for this cycle:" in user.content
    assert _angle_for("brain", "brain", wall(), HEARTBEAT) in user.content


def test_cycle_instructions_set_the_bar_for_a_cycle():
    """The behaviour we are buying: no screensaver cycles, and a voice."""
    assert "screensaver" in CYCLE_INSTRUCTIONS
    assert "all systems operating normally" in CYCLE_INSTRUCTIONS
    # Speaking to Filip stays finding-gated rather than becoming a status report.
    assert "The bar for speaking is a finding" in CYCLE_INSTRUCTIONS.replace("Otherwise the b", "The b")


def test_cycle_instructions_point_at_introspection_tools():
    assert "system_status" in CYCLE_INSTRUCTIONS
    assert "code_overview" in CYCLE_INSTRUCTIONS


def test_brain_angles_include_the_review_and_code_angles():
    assert any("review_expert" in angle for angle in BRAIN_ANGLES)
    assert any("code_overview" in angle for angle in BRAIN_ANGLES)


async def test_the_turn_records_which_model_produced_it(make_loop):
    """Without it a provider cannot tell its own history from another's, which
    is what makes a mid-cycle model switch a 400 rather than a fallback."""
    loop, provider = make_loop(
        [
            reply("looking", call("list_facts", "a"), model="fake:1"),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    await loop.run_cycle()

    convo, _ = provider.calls[-1]
    assistant = [m for m in convo if m.role == "assistant"]
    assert assistant and all(m.model == "fake:1" for m in assistant)


# -- end-to-end: the review angle in one scripted cycle -----------------


async def test_a_scripted_cycle_reviews_an_expert_and_the_note_lands_in_its_inbox(
    brain_dir, expert_dir, wall, tmp_path
):
    """The plan's own local verification scenario: the brain calls
    expert_overview, then review_expert(wrong), and the note reaches the
    expert's inbox -- exercised through a real AgentLoop.run_cycle rather
    than dispatching the tools directly, so the whole path (registry
    allowlist, ctx.extras wiring, send_note-style delivery, wake) is proved
    together."""
    registry = ToolRegistry()
    register_memory_tools(registry)
    register_slack_tools(registry)
    register_introspect(registry)

    expert_dir.write_fact("heater", "Bedroom heater", "misconfigured, needs 18C floor")

    memories = {"brain": brain_dir, "energy": expert_dir}
    # call()'s own first parameter is named "name", which collides with the
    # tool argument these two calls need to pass -- ToolCall built directly
    # instead of going through that helper.
    provider = FakeProvider(
        "fake:1",
        script=[
            reply(
                "checking energy",
                ToolCall(id="a", name="expert_overview", args={"name": "energy"}),
            ),
            reply(
                "found it",
                ToolCall(
                    id="b",
                    name="review_expert",
                    args={
                        "name": "energy",
                        "verdict": "wrong",
                        "findings": "18C floor was corrected by Filip; fact is stale",
                    },
                ),
            ),
            reply("done", call("end_cycle", "c", next_wake_minutes=30, summary="reviewed energy")),
        ],
    )
    ledger = Ledger(
        {"fake:1": Limits(rpm=100, tpm=1_000_000, rpd=1000)}, tmp_path / "ledger.json", clock=wall
    )
    chain = ProviderChain([provider], ledger, call_timeout_s=60)

    woken: list[str] = []
    loops: dict = {}
    ctx = ToolContext(
        loop="brain",
        memory=brain_dir,
        memories=memories,
        settings=load_settings({}),
        wake=woken.append,
        extras={"loops": loops},
    )
    loop = AgentLoop(
        name="brain",
        memory=brain_dir,
        chain=chain,
        registry=registry,
        ctx=ctx,
        heartbeat_s=HEARTBEAT,
        priority="brain",
        constitution="be useful",
        clock=wall,
        pause_file=tmp_path / "PAUSE",
    )
    loops["brain"] = loop

    result = await loop.run_cycle()

    assert result.status == "ok"
    notes = expert_dir.unread_notes()
    assert len(notes) == 1
    assert notes[0].sender == "brain"
    assert "Brain review: wrong" in notes[0].body
    assert "stale" in notes[0].body
    assert woken == ["energy"]
    assert brain_dir.recent_reviews("energy", 1)[0]["verdict"] == "wrong"


# -- cycle-state persistence (_state.json) ------------------------------


async def test_last_cycle_and_counts_persist_to_state_json(make_loop, brain_dir):
    loop, _provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=30, summary="all quiet"))]
    )

    await loop.run_cycle()

    state_path = brain_dir.root / STATE_FILENAME
    assert state_path.exists()
    body = json.loads(state_path.read_text(encoding="utf-8"))
    assert body["last_cycle"]["status"] == "ok"
    assert body["last_cycle"]["rounds"] == 1
    assert body["last_cycle"]["summary"] == "all quiet"
    assert body["cycle_counts"] == {"ok": 1}
    assert body["last_cycle_at"] == loop.last_cycle_at


async def test_a_fresh_loop_loads_last_cycle_from_state_json(
    make_loop, brain_dir, registry, wall, tmp_path
):
    loop, _provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=30, summary="all quiet"))]
    )
    await loop.run_cycle()

    # Simulate a restart: a brand new AgentLoop built over the very same
    # memory directory, the way supervisor.py builds one from settings.
    # last_cycle/cycle_counts must come back from disk even though nothing
    # in this new object has run a cycle yet.
    provider = FakeProvider("fake:1", script=[])
    ledger = Ledger(
        {"fake:1": Limits(rpm=100, tpm=1_000_000, rpd=1000)},
        tmp_path / "ledger2.json",
        clock=wall,
    )
    chain = ProviderChain([provider], ledger)
    ctx = ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir},
        settings=load_settings({}),
        wake=lambda name: None,
    )
    restarted = AgentLoop(
        name="brain",
        memory=brain_dir,
        chain=chain,
        registry=registry,
        ctx=ctx,
        heartbeat_s=HEARTBEAT,
        priority="brain",
        constitution="be useful",
        clock=wall,
        pause_file=tmp_path / "PAUSE",
    )

    assert restarted.last_cycle is not None
    assert restarted.last_cycle.status == "ok"
    assert restarted.last_cycle.rounds == 1
    assert restarted.cycle_counts == {"ok": 1}
    assert restarted.last_cycle_at == loop.last_cycle_at


async def test_cycle_counts_accumulate_across_cycles_in_state_json(make_loop, brain_dir):
    loop, _provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=30, summary="one"))],
    )
    await loop.run_cycle()
    loop.chain.providers[0].script.append(
        reply("done", call("end_cycle", "c2", next_wake_minutes=30, summary="two"))
    )
    await loop.run_cycle()

    body = json.loads((brain_dir.root / STATE_FILENAME).read_text(encoding="utf-8"))
    assert body["cycle_counts"] == {"ok": 2}
    assert body["last_cycle"]["summary"] == "two"


async def test_missing_state_file_is_not_an_error(make_loop, brain_dir):
    """No prior state (first-ever run) must not raise -- last_cycle stays
    None and cycle_counts stays empty until the first cycle finishes."""
    assert not (brain_dir.root / STATE_FILENAME).exists()
    loop, _provider = make_loop([])
    assert loop.last_cycle is None
    assert loop.cycle_counts == {}


async def test_corrupt_state_file_is_tolerated(brain_dir, expert_dir, registry, wall, tmp_path):
    (brain_dir.root / STATE_FILENAME).write_text("{not json", encoding="utf-8")

    provider = FakeProvider("fake:1", script=[])
    ledger = Ledger(
        {"fake:1": Limits(rpm=100, tpm=1_000_000, rpd=1000)}, tmp_path / "ledger.json", clock=wall
    )
    chain = ProviderChain([provider], ledger)
    ctx = ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir, "energy": expert_dir},
        settings=load_settings({}),
        wake=lambda name: None,
    )
    loop = AgentLoop(
        name="brain",
        memory=brain_dir,
        chain=chain,
        registry=registry,
        ctx=ctx,
        heartbeat_s=HEARTBEAT,
        priority="brain",
        constitution="be useful",
        clock=wall,
        pause_file=tmp_path / "PAUSE",
    )

    assert loop.last_cycle is None
    assert loop.cycle_counts == {}


async def test_state_file_with_unknown_status_is_ignored(
    brain_dir, expert_dir, registry, wall, tmp_path
):
    (brain_dir.root / STATE_FILENAME).write_text(
        json.dumps(
            {
                "last_cycle": {
                    "status": "not_a_real_status",
                    "model": "x",
                    "rounds": 1,
                    "next_wake_s": 1,
                },
                "last_cycle_at": 123.0,
                "cycle_counts": {"ok": 3, "not_a_real_status": 5},
            }
        ),
        encoding="utf-8",
    )

    provider = FakeProvider("fake:1", script=[])
    ledger = Ledger(
        {"fake:1": Limits(rpm=100, tpm=1_000_000, rpd=1000)}, tmp_path / "ledger.json", clock=wall
    )
    chain = ProviderChain([provider], ledger)
    ctx = ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir, "energy": expert_dir},
        settings=load_settings({}),
        wake=lambda name: None,
    )
    loop = AgentLoop(
        name="brain",
        memory=brain_dir,
        chain=chain,
        registry=registry,
        ctx=ctx,
        heartbeat_s=HEARTBEAT,
        priority="brain",
        constitution="be useful",
        clock=wall,
        pause_file=tmp_path / "PAUSE",
    )

    # The bogus last_cycle is dropped rather than handed to CycleResult as-is.
    assert loop.last_cycle is None
    # last_cycle_at is a plain float, so it survives regardless.
    assert loop.last_cycle_at == 123.0
    # Only the recognised status in cycle_counts survives.
    assert loop.cycle_counts == {"ok": 3}


# -- rejection memory in the opening context ----------------------------


def _dt_clock(wall):
    """Approvals wants a datetime clock; the loop's own `wall` is epoch
    seconds -- this is the one conversion every test below shares."""

    def _now():
        return datetime.fromtimestamp(wall(), UTC)

    return _now


async def _reject_one(approvals, kind: str, payload: dict, topic: str = "#home") -> None:
    p = await approvals.propose(kind, payload, "why", topic)
    await approvals.on_reaction(p.slack_ts, "x")


async def test_a_rejected_target_appears_in_the_opening_context(make_loop, brain_dir, wall):
    approvals = Approvals(brain_dir, executors=None, clock=_dt_clock(wall), on_message=_always_ts)
    await _reject_one(approvals, "ha_todo_add", {"item": "buy filters"}, "house-ops")

    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))]
    )
    loop.ctx.extras["approvals"] = approvals

    await loop.run_cycle()

    system = provider.calls[0][0][0].content
    assert "Filip has said no to" in system
    assert "buy filters" in system


async def test_no_rejections_means_no_section_at_all(make_loop, brain_dir, wall):
    """An empty heading would cost prompt budget for nothing -- see
    rejection_memory_section's own docstring."""
    approvals = Approvals(brain_dir, executors=None, clock=_dt_clock(wall), on_message=_always_ts)

    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))]
    )
    loop.ctx.extras["approvals"] = approvals

    await loop.run_cycle()

    system = provider.calls[0][0][0].content
    assert "Filip has said no to" not in system


async def test_no_approvals_configured_is_silent_not_an_error(make_loop):
    """ctx.extras has no "approvals" key at all -- the default for every other
    make_loop test in this file. Must not raise."""
    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))]
    )

    result = await loop.run_cycle()

    assert result.status == "ok"
    system = provider.calls[0][0][0].content
    assert "Filip has said no to" not in system


async def test_an_old_rejection_outside_the_window_is_not_shown(make_loop, brain_dir, wall):
    approvals = Approvals(brain_dir, executors=None, clock=_dt_clock(wall), on_message=_always_ts)
    await _reject_one(approvals, "ha_todo_add", {"item": "buy filters"}, "house-ops")

    wall.state["t"] += 40 * 24 * 3600  # 40 days, past the 30-day default

    loop, provider = make_loop(
        [reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))]
    )
    loop.ctx.extras["approvals"] = approvals

    await loop.run_cycle()

    system = provider.calls[0][0][0].content
    assert "Filip has said no to" not in system


async def test_an_expert_loop_does_not_see_rejection_memory(
    registry, wall, tmp_path, brain_dir, expert_dir
):
    """propose is brain-only -- an expert can act on nothing this section
    would tell it, so it costs prompt budget (and an outbox read every
    cycle) for zero benefit. Confirmed the other way in
    test_a_rejected_target_appears_in_the_opening_context."""
    approvals = Approvals(brain_dir, executors=None, clock=_dt_clock(wall), on_message=_always_ts)
    await _reject_one(approvals, "ha_todo_add", {"item": "buy filters"}, "house-ops")

    provider = FakeProvider(
        "fake:1",
        script=[reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))],
    )
    ledger = Ledger(
        {"fake:1": Limits(rpm=100, tpm=1_000_000, rpd=1000)}, tmp_path / "ledger.json", clock=wall
    )
    chain = ProviderChain([provider], ledger)
    ctx = ToolContext(
        loop="energy",
        memory=expert_dir,
        memories={"brain": brain_dir, "energy": expert_dir},
        settings=load_settings({}),
        wake=lambda _name: None,
        extras={"approvals": approvals},
    )
    loop = AgentLoop(
        name="energy",
        memory=expert_dir,
        chain=chain,
        registry=registry,
        ctx=ctx,
        heartbeat_s=HEARTBEAT,
        priority="expert",
        constitution="be useful",
        clock=wall,
        pause_file=tmp_path / "PAUSE",
    )

    await loop.run_cycle()

    system = provider.calls[0][0][0].content
    assert "Filip has said no to" not in system


async def test_an_expert_loop_never_calls_approvals_all(
    registry, wall, tmp_path, brain_dir, expert_dir
):
    """Not just "hidden from the prompt" -- skipped outright, so an outbox
    that has grown large over weeks costs an expert cycle nothing extra."""
    approvals = Approvals(brain_dir, executors=None, clock=_dt_clock(wall), on_message=_always_ts)
    await _reject_one(approvals, "ha_todo_add", {"item": "buy filters"}, "house-ops")
    calls = []
    original_all = approvals.all

    def _spy():
        calls.append(1)
        return original_all()

    approvals.all = _spy

    provider = FakeProvider(
        "fake:1",
        script=[reply("done", call("end_cycle", "c", next_wake_minutes=5, summary="s"))],
    )
    ledger = Ledger(
        {"fake:1": Limits(rpm=100, tpm=1_000_000, rpd=1000)}, tmp_path / "ledger.json", clock=wall
    )
    chain = ProviderChain([provider], ledger)
    ctx = ToolContext(
        loop="energy",
        memory=expert_dir,
        memories={"brain": brain_dir, "energy": expert_dir},
        settings=load_settings({}),
        wake=lambda _name: None,
        extras={"approvals": approvals},
    )
    loop = AgentLoop(
        name="energy",
        memory=expert_dir,
        chain=chain,
        registry=registry,
        ctx=ctx,
        heartbeat_s=HEARTBEAT,
        priority="expert",
        constitution="be useful",
        clock=wall,
        pause_file=tmp_path / "PAUSE",
    )

    await loop.run_cycle()

    assert calls == []


async def _always_ts(topic: str, text: str, blocks=None) -> str:
    return "1.1"
