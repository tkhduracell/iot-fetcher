import asyncio
from pathlib import Path

import pytest

from ai_brain.config import load_settings
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
    CYCLE_INSTRUCTIONS,
    MAX_TOOL_RESULT_CHARS,
    AgentLoop,
    CycleResult,
)
from ai_brain.tools import Tool, ToolContext, ToolRegistry
from ai_brain.tools.memory_tools import register_memory_tools
from ai_brain.tools.slack_tools import register_slack_tools

HEARTBEAT = 900


def reply(text: str = "", *calls: ToolCall, model: str = "fake:1") -> Reply:
    return Reply(
        text=text,
        tool_calls=tuple(calls),
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        model=model,
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
        self.statuses: list[tuple[str, str]] = []

    async def post(self, topic: str, text: str) -> str:
        return "1.0"

    async def set_status(self, topic: str, status: str) -> None:
        self.statuses.append((topic, status))


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
        chain = ProviderChain([provider], ledger)
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
    assert "[ok] model=fake:1 rounds=3 all quiet" in journal
    assert "checked the pool" in journal
    assert loop.last_cycle is result
    assert loop.cycle_counts["ok"] == 1


async def test_system_and_user_messages_frame_the_cycle(make_loop, brain_dir):
    brain_dir.write_fact("pool", "the pool is a hole with water in it")
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
    assert {t.name for t in tools} == {s.name for s in loop.registry.specs_for("brain")}


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
        brain_dir.write_fact(f"fact{i}", "x")
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
    brain_dir.drop_note("filip", "important")
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


def test_slack_post_says_it_is_the_only_way_filip_hears_from_you(registry):
    spec = next(s for s in registry.specs_for("brain") if s.name == "slack_post")
    assert "only way" in spec.description


# -- slack topic status ------------------------------------------------


async def test_cycle_topics_flip_to_active_and_clear(make_loop):
    """Topics the brain spoke on this cycle stop showing as 'processing'."""
    loop, _ = make_loop(
        [
            reply(
                "speaking",
                call("slack_post", "a", topic="pool", text="warm"),
                call("slack_post", "b", topic="energy", text="cheap"),
            ),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )
    slack = FakeSlackOut()
    loop.ctx.extras["slack_out"] = slack

    await loop.run_cycle()

    assert slack.statuses == [("pool", "active"), ("energy", "active")]
    assert loop.ctx.extras["cycle_topics"] == []


async def test_cycle_topics_list_exists_before_tools_run(make_loop):
    loop, _ = make_loop([reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s"))])

    await loop.run_cycle()

    assert loop.ctx.extras["cycle_topics"] == []
    assert "end_cycle" not in loop.ctx.extras


async def test_topics_are_cleared_even_without_a_slack_out(make_loop):
    """slack_out may be absent (slack disabled); the cycle must still close."""
    loop, _ = make_loop(
        [
            reply("trying", call("slack_post", "a", topic="pool", text="warm")),
            reply("done", call("end_cycle", "c", next_wake_minutes=10, summary="s")),
        ]
    )

    result = await loop.run_cycle()

    assert result.status == "ok"
    assert loop.ctx.extras["cycle_topics"] == []


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
    """_finish awaits set_status inside a cancelled task; books close regardless."""
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
    assert _last_journal_line(brain_dir) == "[paused] model=fake:1 rounds=1 paused mid-cycle"


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
