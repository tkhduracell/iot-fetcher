"""Resuming an unfinished cycle from its session log.

A crash is simulated the honest way: the first loop's cycle is stopped by an
exception that nothing in ``run_cycle`` is allowed to catch, leaving a log with
no ``cycle_end``, and a second AgentLoop -- what a restart is -- is built over
the same memory directory.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sessions_helpers import ScriptedProvider, build_loop, build_registry, call, end, reply

from ai_brain.llm import (
    ChainExhausted,
    Message,
    ProviderError,
    Reply,
    ToolCall,
    ToolSpec,
    Usage,
)
from ai_brain.llm.gemini import build_request as gemini_request
from ai_brain.loop import MAX_TOOL_RESULT_CHARS
from ai_brain.sessions import (
    UNSIGNED_MODEL,
    read_events,
    recover,
    session_files,
)
from ai_brain.supervisor import build
from ai_brain.tools import READ_ONLY_TOOLS, Tool

SECRET = "AIza" + "x" * 35


class _Killed(BaseException):
    """Stands in for SIGKILL: not an Exception, so run_cycle's handlers miss it,
    and the test below strips the ``finally`` that a real kill would skip too."""


@pytest.fixture
def wall():
    state = {"t": 1_800_000_000.0}

    def _now() -> float:
        return state["t"]

    _now.state = state
    return _now


def _tool(name: str, fn=None, text: str = "{}") -> Tool:
    async def _static(_ctx, _args) -> str:
        return text

    return Tool(
        spec=ToolSpec(name=name, description=name, parameters={"type": "object", "properties": {}}),
        fn=fn or _static,
    )


def _log_path(memory):
    return next(iter(session_files(memory.root / "sessions")))


def _crashed_session(memory, events: list[dict], cycle: str = "20270115-080000-aaaa"):
    """A log exactly as a dead process would leave it, seq and all."""
    directory = memory.root / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{cycle}.jsonl"
    with path.open("w") as fh:
        for seq, event in enumerate(events, start=1):
            fh.write(json.dumps({"seq": seq, "at": event.pop("at", 1_800_000_000.0), **event}) + "\n")
    return path


def _opening(notes=()) -> dict:
    return {
        "type": "opening",
        "agent": "brain",
        "notes": list(notes),
        "messages": [
            {"role": "system", "content": "stable"},
            {"role": "system", "content": "volatile"},
            {"role": "user", "content": "Begin your think cycle."},
        ],
    }


def _reply_event(*calls, text="", round_=1, model="fake:1", **extra) -> dict:
    return {
        "type": "reply",
        "round": round_,
        "model": model,
        "chain_key": model,
        "text": text,
        "thinking": "",
        "tool_calls": [{"id": c[0], "name": c[1], "args": c[2], "thought_signature": ""} for c in calls],
        "thought_signature": "",
        "provider_blocks": [],
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "budget_tokens": 10,
        **extra,
    }


def _tool_messages(messages) -> dict[str, str]:
    return {m.tool_call_id: m.content for m in messages if m.role == "tool"}


async def _crash_mid_cycle(memory, tmp_path, wall, script, *, die_in="die", **kwargs):
    """Run a cycle until a tool named ``die_in`` kills the process."""

    async def _die(_ctx, _args):
        raise _Killed

    registry = kwargs.pop("registry", None) or build_registry(_tool(die_in, _die))
    loop, provider = build_loop(memory, tmp_path, wall, script, registry=registry, **kwargs)
    original_finish = loop._finish

    async def _no_finish(*_a, **_k):
        # A kill runs no finally block at all: no journal, no archive, no
        # cycle_end, nothing but what is already on disk.
        session = loop._session
        if session is not None:
            session.close()
        loop._session = None
        return None

    loop._finish = _no_finish  # type: ignore[method-assign]
    with pytest.raises(_Killed):
        await loop.run_cycle()
    loop._finish = original_finish  # type: ignore[method-assign]
    return loop, provider


# -- rebuilding ------------------------------------------------------------


async def test_crashed_cycle_continues_with_its_conversation(brain_dir, tmp_path, wall):
    await _crash_mid_cycle(
        brain_dir,
        tmp_path,
        wall,
        [
            reply("first look", call("list_facts", "a")),
            reply("about to die", call("die", "b")),
        ],
    )
    log_before = list(session_files(brain_dir.root / "sessions"))

    wall.state["t"] += 600
    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("back", end("recovered"))])
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert result.rounds == 3
    assert len(provider.calls) == 1
    sent = provider.calls[0][0]
    assert [m.role for m in sent] == [
        "system",
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "user",
    ]
    assert sent[3].content == "first look"
    assert sent[3].tool_calls[0].name == "list_facts"
    assert sent[5].content == "about to die"
    # One cycle, one file: the resume appended to it rather than starting anew.
    files = list(session_files(brain_dir.root / "sessions"))
    assert len(files) == 1 and files[0].stem.split(".")[0] == log_before[0].stem.split(".")[0]
    types = [e["type"] for e in read_events(files[0])]
    assert types.count("opening") == 1
    assert types.count("resume") == 1
    assert types[-1] == "cycle_end"
    assert [e["seq"] for e in read_events(files[0])] == list(range(1, len(types) + 1))


async def test_resumed_cycle_is_told_how_long_it_has_been(brain_dir, tmp_path, wall):
    await _crash_mid_cycle(
        brain_dir, tmp_path, wall, [reply("", call("die", "b"))]
    )
    wall.state["t"] += 2 * 3600

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await loop.run_cycle()

    notice = provider.calls[0][0][-1]
    assert notice.role == "user"
    assert "2.0 h later" in notice.content
    assert "read it again" in notice.content
    assert "(resumed after 2.0 h)" in brain_dir.journal_text(1)
    assert loop.trace.resumed_after_s == pytest.approx(7200, abs=1)


# -- side effects ----------------------------------------------------------


async def test_side_effects_are_never_replayed(brain_dir, tmp_path, wall):
    ran: list[str] = []

    async def _write(_ctx, _args):
        ran.append("ran")
        raise _Killed

    registry = build_registry(_tool("propose", _write))
    await _crash_mid_cycle(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("propose", "p"))],
        die_in="propose",
        registry=registry,
    )
    assert ran == ["ran"]

    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end())], registry=registry
    )
    await loop.run_cycle()

    assert ran == ["ran"]
    assert "outcome unknown" in _tool_messages(provider.calls[0][0])["p"]
    assert "Check before retrying" in _tool_messages(provider.calls[0][0])["p"]


async def test_read_gets_a_call_again_result_not_outcome_unknown(brain_dir, tmp_path, wall):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("r", "vm_query", {"query": "up"})),
            {"type": "tool_call", "round": 1, "id": "r", "name": "vm_query"},
        ],
    )
    state = recover(path, read_events(path), lambda n: n in READ_ONLY_TOOLS, lambda _n, r: r)

    text = _tool_messages(state.messages)["r"]
    assert "call it again" in text
    assert "outcome unknown" not in text
    assert state.pending_results[0]["synthetic"] == "read_interrupted"


async def test_unknown_tool_with_no_result_counts_as_a_write(brain_dir, tmp_path):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("w", "brand_new_tool", {})),
            {"type": "tool_call", "round": 1, "id": "w", "name": "brand_new_tool"},
        ],
    )
    state = recover(path, read_events(path), lambda n: n in READ_ONLY_TOOLS, lambda _n, r: r)
    assert "outcome unknown" in _tool_messages(state.messages)["w"]


async def test_calls_that_never_started_are_reported_as_not_run(brain_dir, tmp_path):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("a", "list_facts", {}), ("b", "slack_post", {"topic": "x"})),
            {"type": "tool_call", "round": 1, "id": "a", "name": "list_facts"},
            {"type": "tool_result", "round": 1, "id": "a", "name": "list_facts", "result": "[]"},
        ],
    )
    state = recover(path, read_events(path), lambda _n: False, lambda _n, r: r)
    assert _tool_messages(state.messages) == {
        "a": "[]",
        "b": _tool_messages(state.messages)["b"],
    }
    assert "not run" in _tool_messages(state.messages)["b"]
    # It reached nobody, so it answers nobody.
    assert state.answers == []


async def test_synthetic_results_are_logged_so_a_second_resume_agrees(brain_dir, tmp_path, wall):
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])

    # Resume, and die again before the model answers.
    second, provider = build_loop(brain_dir, tmp_path, wall, [])

    async def _boom(*_a, **_k):
        raise ChainExhausted(retry_at=None)

    second.chain.complete = _boom  # type: ignore[method-assign]
    assert (await second.run_cycle()).status == "no_budget"

    third, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await third.run_cycle()

    tools = [m for m in provider.calls[0][0] if m.role == "tool"]
    assert len(tools) == 1
    assert "outcome unknown" in tools[0].content
    events = read_events(_log_path(brain_dir))
    assert [e["synthetic"] for e in events if e.get("id") == "b" and e["type"] == "tool_result"] == [
        "outcome_unknown"
    ]


def test_every_read_only_tool_is_a_real_tool(tmp_path):
    """The allowlist is the safety boundary, so a typo in it must fail here."""
    from conftest import env, fake_chain

    from ai_brain.config import load_settings
    from ai_brain.tools.airbnb_tools import register_airbnb_tools
    from ai_brain.tools.introspect import register_introspect

    system = build(load_settings(env(tmp_path)), chain_factory=fake_chain)
    register_introspect(system.registry)
    register_airbnb_tools(system.registry)
    known = set(system.registry._tools)
    assert READ_ONLY_TOOLS <= known, READ_ONLY_TOOLS - known


def test_read_only_list_has_no_write_tool():
    assert not READ_ONLY_TOOLS & {
        "propose",
        "slack_post",
        "write_fact",
        "delete_fact",
        "send_note",
        "append_journal",
        "rewrite_goals",
        "rewrite_identity",
        "review_expert",
        "note_gap",
        "close_gap",
        "end_cycle",
    }


# -- a truncated result ----------------------------------------------------


async def test_rebuilt_results_are_cut_as_the_conversation_saw_them(brain_dir, tmp_path, wall):
    big = "y" * (MAX_TOOL_RESULT_CHARS + 100)
    registry = build_registry(_tool("big", text=big), _tool("die"))

    async def _die(_ctx, _args):
        raise _Killed

    registry.register(_tool("die", _die))
    await _crash_mid_cycle(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("big", "a")), reply("", call("die", "b"))],
        registry=registry,
    )

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())], registry=registry)
    await loop.run_cycle()

    assert len(_tool_messages(provider.calls[0][0])["a"]) < len(big)


# -- state -----------------------------------------------------------------


async def test_rounds_and_tokens_carry_over_and_grant_no_fresh_rounds(brain_dir, tmp_path, wall):
    await _crash_mid_cycle(
        brain_dir,
        tmp_path,
        wall,
        [
            reply("", call("list_facts", "a")),
            reply("", call("list_facts", "b")),
            reply("", call("die", "c")),
        ],
        max_rounds=5,
    )

    loop, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("list_facts", "d")), reply("", call("list_facts", "e"))],
        max_rounds=5,
    )
    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    assert result.rounds == 5
    assert len(provider.calls) == 2
    # All five rounds in the trace and its totals, from two processes.
    assert len(loop.trace.rounds) == 5
    assert loop.trace.prompt_tokens == 50


async def test_token_budget_stop_survives_a_crash(brain_dir, tmp_path, wall):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(
                ("a", "list_facts", {}),
                caps={"effective": 3, "budget_stopped": True, "floor": 3},
                budget_tokens=999,
            ),
            {"type": "tool_call", "round": 1, "id": "a", "name": "list_facts"},
            {"type": "tool_result", "round": 1, "id": "a", "name": "list_facts", "result": "[]"},
        ],
    )
    state = recover(path, read_events(path), lambda _n: False, lambda _n, r: r)
    assert state.budget_stopped is True
    assert state.floor_cap == 3
    assert state.effective_cap == 3
    assert state.budget_tokens == 999

    loop, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("list_facts", "b")), reply("", call("list_facts", "c"))],
        max_rounds=30,
    )
    result = await loop.run_cycle()

    assert result.rounds == 3
    assert result.status == "max_rounds"


async def test_owed_reply_survives_a_crash(brain_dir, tmp_path, wall):
    brain_dir.drop_note("filip", "topic: pool\nwhat is the temperature?")
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])

    loop, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", end()), reply("", end("gave up", "end2"))],
    )
    await loop.run_cycle()

    # end_cycle refused once, because Filip is still owed his answer.
    assert len(provider.calls) == 2
    refusal = _tool_messages(provider.calls[1][0])["end"]
    assert "still waiting for a reply" in refusal


async def test_answered_topic_stays_answered(brain_dir, tmp_path, wall):
    brain_dir.drop_note("filip", "topic: pool\nwhat is the temperature?")

    class _Slack:
        posts: list[str] = []

        async def post(self, topic, text):
            self.posts.append(topic)
            return "1.0"

        def resolve_topic(self, topic):
            return topic

        def recent_topics(self):
            return []

        async def mark_unanswered(self, topic):
            pass

    slack = _Slack()
    await _crash_mid_cycle(
        brain_dir,
        tmp_path,
        wall,
        [
            reply("", call("slack_post", "s", topic="pool", text="27 degrees")),
            reply("", call("die", "b")),
        ],
        extras={"slack_out": slack},
    )
    assert slack.posts == ["pool"]

    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end())], extras={"slack_out": slack}
    )
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert len(provider.calls) == 1
    assert slack.posts == ["pool"]


async def test_uncertain_slack_post_is_not_pushed_again(brain_dir, tmp_path, wall):
    """Dying inside slack_post: it may have landed, so end_cycle must not
    refuse and talk the model into a second copy."""
    brain_dir.drop_note("filip", "topic: pool\nwhat is the temperature?")

    async def _die(_ctx, _args):
        raise _Killed

    registry = build_registry()
    registry.register(_tool("slack_post", _die))
    await _crash_mid_cycle(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("slack_post", "s", topic="pool", text="27 degrees"))],
        registry=registry,
    )

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert len(provider.calls) == 1


async def test_owed_refusal_is_not_granted_twice(brain_dir, tmp_path):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("e", "end_cycle", {"next_wake_minutes": 5, "summary": "x"})),
            {"type": "tool_call", "round": 1, "id": "e", "name": "end_cycle"},
            {
                "type": "tool_result",
                "round": 1,
                "id": "e",
                "name": "end_cycle",
                "result": json.dumps({"error": "Filip is still waiting"}),
            },
        ],
    )
    state = recover(path, read_events(path), lambda _n: False, lambda _n, r: r)
    assert state.refused_seq == 4
    assert state.end_cycle is None


# -- the inbox -------------------------------------------------------------


async def test_notes_read_before_the_crash_are_archived_once(brain_dir, tmp_path, wall):
    brain_dir.drop_note("energy", "the pool pump looks odd")
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])
    # A crash archives nothing: the note must still be there for the resume.
    assert len(brain_dir.unread_notes()) == 1

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await loop.run_cycle()

    assert brain_dir.unread_notes() == []
    assert len(list(brain_dir.done_dir.glob("*.md"))) == 1
    # Not rendered a second time: the opening turn already had it.
    rendered = "\n".join(m.content for m in provider.calls[0][0])
    assert rendered.count("the pool pump looks odd") == 1


async def test_note_arriving_while_down_is_shown_and_archived(brain_dir, tmp_path, wall):
    brain_dir.drop_note("energy", "first note")
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])
    brain_dir.drop_note("filip", "topic: pool\nsecond note, from Filip")

    loop, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", end()), reply("", end("gave up", "end2"))],
    )
    await loop.run_cycle()

    rendered = "\n".join(m.content for m in provider.calls[0][0])
    assert rendered.count("first note") == 1
    assert rendered.count("second note, from Filip") == 1
    assert brain_dir.unread_notes() == []
    # He is owed a reply for the note that arrived late, too.
    assert "still waiting" in _tool_messages(provider.calls[1][0])["end"]


async def test_note_arriving_during_the_resumed_cycle_waits_for_the_next(brain_dir, tmp_path, wall):
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])

    async def _late(ctx, _args):
        ctx.memory.drop_note("energy", "too late for this one")
        return "{}"

    registry = build_registry(_tool("late", _late))
    loop, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("late", "l")), reply("", end())],
        registry=registry,
    )
    await loop.run_cycle()

    assert [n.body for n in brain_dir.unread_notes()] == ["too late for this one"]


async def test_note_shown_on_a_first_resume_is_not_shown_again_on_a_second(brain_dir, tmp_path, wall):
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])
    brain_dir.drop_note("energy", "note from the gap")

    second, _ = build_loop(brain_dir, tmp_path, wall, [])

    async def _boom(*_a, **_k):
        raise ChainExhausted(retry_at=None)

    second.chain.complete = _boom  # type: ignore[method-assign]
    await second.run_cycle()
    assert len(brain_dir.unread_notes()) == 1

    third, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await third.run_cycle()

    rendered = "\n".join(m.content for m in provider.calls[0][0])
    assert rendered.count("note from the gap") == 1
    assert brain_dir.unread_notes() == []


# -- what resumes and what does not ---------------------------------------


async def test_no_budget_mid_cycle_resumes_where_it_stopped(brain_dir, tmp_path, wall):
    first, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("found something", call("list_facts", "a"))],
    )
    real = first.chain.complete
    calls = {"n": 0}

    async def _then_dry(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ChainExhausted(retry_at=wall() + 3600)
        return await real(*a, **k)

    first.chain.complete = _then_dry  # type: ignore[method-assign]

    result = await first.run_cycle()
    assert result.status == "no_budget"
    assert result.rounds == 1
    assert result.next_wake_s == 3600
    # Still plain: it is waiting to be picked up.
    assert _log_path(brain_dir).name.endswith(".jsonl")

    wall.state["t"] += 3600
    second, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end("finished"))])
    result = await second.run_cycle()

    assert result.status == "ok"
    assert result.rounds == 2
    sent = provider.calls[0][0]
    assert sent[3].content == "found something"
    assert _log_path(brain_dir).name.endswith(".jsonl.gz")


async def test_no_budget_before_any_reply_starts_fresh(brain_dir, tmp_path, wall):
    first, _ = build_loop(brain_dir, tmp_path, wall, [])

    async def _dry(*_a, **_k):
        raise ChainExhausted(retry_at=None)

    first.chain.complete = _dry  # type: ignore[method-assign]
    await first.run_cycle()

    second, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await second.run_cycle()

    assert len(list(session_files(brain_dir.root / "sessions"))) == 2
    assert not any("interrupted" in m.content for m in provider.calls[0][0])


async def test_restart_resumes_a_cancelled_cycle(brain_dir, tmp_path, wall):
    started = asyncio.Event()
    never = asyncio.Event()

    async def _hang(_ctx, _args):
        started.set()
        await never.wait()
        return "{}"

    registry = build_registry(_tool("hang", _hang))
    first, _ = build_loop(
        brain_dir, tmp_path, wall, [reply("", call("hang", "h"))], registry=registry
    )
    task = asyncio.create_task(first.run_cycle())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    second, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end())], registry=registry
    )
    result = await second.run_cycle()

    assert result.status == "ok"
    assert "outcome unknown" in _tool_messages(provider.calls[0][0])["h"]


async def test_mid_cycle_pause_resumes_after_it_lifts(brain_dir, tmp_path, wall):
    pause = tmp_path / "PAUSE"

    async def _pause(_ctx, _args):
        pause.touch()
        return "{}"

    registry = build_registry(_tool("pause", _pause))
    first, _ = build_loop(
        brain_dir, tmp_path, wall, [reply("", call("pause", "p"))], registry=registry
    )
    assert (await first.run_cycle()).status == "paused"

    # Still paused: nothing happens to the log, no attempt spent.
    waiting, provider = build_loop(brain_dir, tmp_path, wall, [], registry=registry)
    assert (await waiting.run_cycle()).status == "paused"
    assert provider.calls == []

    pause.unlink()
    second, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end())], registry=registry
    )
    result = await second.run_cycle()
    assert result.status == "ok"
    assert result.rounds == 2


@pytest.mark.parametrize("status", ["timeout", "error", "max_rounds", "ok"])
async def test_finished_statuses_are_not_resumed(brain_dir, tmp_path, wall, status):
    _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("a", "list_facts", {})),
            {"type": "cycle_end", "status": status, "rounds": 1},
        ],
    )

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await loop.run_cycle()

    assert not any("interrupted" in m.content for m in provider.calls[0][0])
    assert len(list(session_files(brain_dir.root / "sessions"))) == 2
    assert all(p.name.endswith(".gz") for p in session_files(brain_dir.root / "sessions"))


async def test_crash_before_the_first_reply_is_resumed_not_started_over(brain_dir, tmp_path, wall):
    brain_dir.drop_note("energy", "a note")
    name = brain_dir.unread_notes()[0].path.name
    _crashed_session(brain_dir, [_opening([name])])

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert len(list(session_files(brain_dir.root / "sessions"))) == 1
    assert any("interrupted" in m.content for m in provider.calls[0][0])
    assert brain_dir.unread_notes() == []


async def _die_in_the_first_call(memory, tmp_path, wall):
    loop, _ = build_loop(memory, tmp_path, wall, [])

    async def _killed(*_a, **_k):
        raise _Killed

    async def _no_finish(*_a, **_k):
        loop._session.close()

    loop.chain.complete = _killed  # type: ignore[method-assign]
    loop._finish = _no_finish  # type: ignore[method-assign]
    with pytest.raises(_Killed):
        await loop.run_cycle()


async def test_dying_on_every_first_call_is_bounded(brain_dir, tmp_path, wall):
    """A note that takes the process down before any reply used to start a fresh
    file each time, so nothing ever counted the deaths."""
    brain_dir.drop_note("energy", "this note kills the process")
    for _ in range(4):
        await _die_in_the_first_call(brain_dir, tmp_path, wall)
        assert "[abandoned]" not in brain_dir.journal_text(1)
        assert len(brain_dir.unread_notes()) == 1

    loop, provider = build_loop(brain_dir, tmp_path, wall, [])
    result = await loop.run_cycle()

    assert result.status == "abandoned"
    assert provider.calls == []
    assert "[abandoned]" in brain_dir.journal_text(1)
    assert len(brain_dir.unread_notes()) == 1


async def test_model_already_finished_closes_without_a_call(brain_dir, tmp_path, wall):
    _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("e", "end_cycle", {"next_wake_minutes": 45, "summary": "all done"})),
            {"type": "tool_call", "round": 1, "id": "e", "name": "end_cycle"},
            {
                "type": "tool_result",
                "round": 1,
                "id": "e",
                "name": "end_cycle",
                "result": json.dumps({"ok": True, "next_wake_minutes": 45}),
            },
        ],
    )

    loop, provider = build_loop(brain_dir, tmp_path, wall, [])
    result = await loop.run_cycle()

    assert provider.calls == []
    assert result.status == "ok"
    assert result.next_wake_s == 45 * 60
    assert "all done" in brain_dir.journal_text(1)


async def test_answer_with_no_tool_calls_closes_without_a_call(brain_dir, tmp_path, wall):
    _crashed_session(brain_dir, [_opening(), _reply_event(text="that is all")])

    loop, provider = build_loop(brain_dir, tmp_path, wall, [])
    result = await loop.run_cycle()

    assert provider.calls == []
    assert result.status == "ok"


# -- caps ------------------------------------------------------------------


async def test_too_old_is_abandoned_and_the_inbox_survives(brain_dir, tmp_path, wall):
    brain_dir.drop_note("energy", "keep me")
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])
    wall.state["t"] += 27 * 3600

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    result = await loop.run_cycle()

    journal = brain_dir.journal_text(2)
    assert "[abandoned]" in journal
    assert "old" in journal
    assert loop.cycle_counts["abandoned"] == 1
    # A fresh cycle ran instead, in the same call, and read the note.
    assert result.status == "ok"
    assert result.rounds == 1
    rendered = "\n".join(m.content for m in provider.calls[0][0])
    assert "keep me" in rendered
    assert "interrupted" not in rendered
    assert all(p.name.endswith(".gz") for p in session_files(brain_dir.root / "sessions"))


async def test_repeat_killer_is_abandoned_and_its_notes_stay_unread(brain_dir, tmp_path, wall):
    brain_dir.drop_note("energy", "this note may kill the process")
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])

    for attempt in range(3):
        await _crash_mid_cycle(
            brain_dir, tmp_path, wall, [reply("", call("die", f"again{attempt}"))]
        )
        assert "[abandoned]" not in brain_dir.journal_text(1)

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    result = await loop.run_cycle()

    assert "[abandoned]" in brain_dir.journal_text(1)
    assert "1 notes left unread" in brain_dir.journal_text(1)
    assert result.status == "abandoned"
    assert result.next_wake_s == loop.heartbeat_s
    # Held off: no model was asked, and nothing was archived.
    assert provider.calls == []
    assert len(brain_dir.unread_notes()) == 1
    assert list(brain_dir.done_dir.glob("*.md")) == []
    assert all(p.name.endswith(".gz") for p in session_files(brain_dir.root / "sessions"))

    # A heartbeat later a fresh cycle reads it.
    result = await loop.run_cycle()
    assert result.status == "ok"
    assert "this note may kill" in "\n".join(m.content for m in provider.calls[0][0])
    assert brain_dir.unread_notes() == []

async def test_repeated_no_budget_is_not_a_death(brain_dir, tmp_path, wall):
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])

    for _ in range(8):
        wall.state["t"] += 60
        waiting, _ = build_loop(brain_dir, tmp_path, wall, [])

        async def _dry(*_a, **_k):
            raise ChainExhausted(retry_at=None)

        waiting.chain.complete = _dry  # type: ignore[method-assign]
        assert (await waiting.run_cycle()).status == "no_budget"

    assert "[abandoned]" not in brain_dir.journal_text(1)
    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    assert (await loop.run_cycle()).status == "ok"
    # One notice, the latest, and it counts from when the cycle last did
    # anything rather than from the last failed retry.
    notices = [m for m in provider.calls[0][0] if "interrupted" in m.content]
    assert len(notices) == 1
    assert "8 min later" in notices[0].content


async def test_log_with_a_hole_is_abandoned(brain_dir, tmp_path, wall):
    path = _crashed_session(
        brain_dir,
        [_opening(), _reply_event(("a", "propose", {}))],
    )
    with path.open("a") as fh:
        fh.write(json.dumps({"seq": 9, "at": 1_800_000_000.0, "type": "tool_result", "id": "z"}) + "\n")
    brain_dir.drop_note("energy", "keep me")

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await loop.run_cycle()

    assert "holes" in brain_dir.journal_text(1)
    assert "keep me" in "\n".join(m.content for m in provider.calls[0][0])


async def test_resume_off_abandons_without_archiving(brain_dir, tmp_path, wall):
    brain_dir.drop_note("energy", "keep me")
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])

    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end())], resume_max_deaths=0
    )
    await loop.run_cycle()

    assert "switched off" in brain_dir.journal_text(1)
    assert "keep me" in "\n".join(m.content for m in provider.calls[0][0])


async def test_torn_last_line_is_tolerated(brain_dir, tmp_path, wall):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("a", "list_facts", {})),
            {"type": "tool_call", "round": 1, "id": "a", "name": "list_facts"},
        ],
    )
    with path.open("a") as fh:
        fh.write('{"seq": 4, "type": "tool_res')

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    assert (await loop.run_cycle()).status == "ok"

    events = read_events(next(iter(session_files(brain_dir.root / "sessions"))))
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


async def test_unreadable_log_never_stops_a_cycle(brain_dir, tmp_path, wall, monkeypatch):
    _crashed_session(brain_dir, [_opening(), _reply_event()])
    import ai_brain.loop as module

    def _boom(*_a, **_k):
        raise RuntimeError("corrupt")

    monkeypatch.setattr(module, "recover", _boom)
    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    result = await loop.run_cycle()
    assert result.status == "ok"
    assert len(provider.calls) == 1


async def test_only_the_newest_unfinished_cycle_is_resumed(brain_dir, tmp_path, wall):
    _crashed_session(
        brain_dir, [_opening(), _reply_event(text="", model="old")], cycle="20270115-070000-aaaa"
    )
    _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("a", "list_facts", {}), text="newest"),
            {"type": "tool_call", "round": 1, "id": "a", "name": "list_facts"},
            {"type": "tool_result", "round": 1, "id": "a", "name": "list_facts", "result": "[]"},
        ],
        cycle="20270115-080000-bbbb",
    )

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await loop.run_cycle()

    assert provider.calls[0][0][3].content == "newest"
    assert "20270115-070000-aaaa" in brain_dir.journal_text(1)


# -- model changes ---------------------------------------------------------


def _signed_turn() -> Reply:
    return Reply(
        text="",
        tool_calls=(ToolCall(id="a", name="list_facts", args={}, thought_signature="sig-call"),),
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        model="gemini-a",
        key="gemini:gemini-a",
        thought_signature="sig-turn",
        thinking="deep thoughts",
    )


async def _crash_after_signed_turn(brain_dir, tmp_path, wall):
    provider = ScriptedProvider("gemini:gemini-a", [_signed_turn(), reply("", call("die", "b"))])
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [], providers=[provider])


async def test_same_model_gets_its_signatures_back(brain_dir, tmp_path, wall):
    await _crash_after_signed_turn(brain_dir, tmp_path, wall)

    provider = ScriptedProvider("gemini:gemini-a", [reply("", end(), model="gemini-a", key="gemini:gemini-a")])
    loop, _ = build_loop(brain_dir, tmp_path, wall, [], providers=[provider])
    await loop.run_cycle()

    body = json.dumps(gemini_request(provider.calls[0][0], [], 64, model="gemini-a"))
    assert "sig-call" in body
    assert "sig-turn" not in body or "sig-call" in body


async def test_other_model_never_sees_a_signature(brain_dir, tmp_path, wall):
    await _crash_after_signed_turn(brain_dir, tmp_path, wall)

    provider = ScriptedProvider("gemini:gemini-b", [reply("", end(), model="gemini-b", key="gemini:gemini-b")])
    loop, _ = build_loop(brain_dir, tmp_path, wall, [], providers=[provider])
    result = await loop.run_cycle()

    assert result.status == "ok"
    body = json.dumps(gemini_request(provider.calls[0][0], [], 64, model="gemini-b"))
    assert "sig-call" not in body
    assert "sig-turn" not in body
    # The history survives, as text the new model can read.
    assert "I called list_facts" in body


async def test_turn_redaction_changed_is_replayed_as_text(brain_dir, tmp_path, wall):
    signed = Reply(
        text=f"found {SECRET}",
        tool_calls=(ToolCall(id="a", name="list_facts", args={}, thought_signature="sig-call"),),
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        model="gemini-a",
        key="gemini:gemini-a",
    )
    provider = ScriptedProvider("gemini:gemini-a", [signed, reply("", call("die", "b"))])
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [], providers=[provider])

    provider = ScriptedProvider("gemini:gemini-a", [reply("", end(), model="gemini-a", key="gemini:gemini-a")])
    loop, _ = build_loop(brain_dir, tmp_path, wall, [], providers=[provider])
    await loop.run_cycle()

    sent = provider.calls[0][0]
    assert sent[3].model == UNSIGNED_MODEL
    body = json.dumps(gemini_request(sent, [], 64, model="gemini-a"))
    assert "sig-call" not in body
    assert SECRET not in body


async def test_refused_history_is_replayed_as_text_once(brain_dir, tmp_path, wall):
    await _crash_after_signed_turn(brain_dir, tmp_path, wall)

    provider = ScriptedProvider(
        "gemini:gemini-a",
        [
            ProviderError("invalid signature", kind="bad_request"),
            reply("", end("ok now"), model="gemini-a", key="gemini:gemini-a"),
        ],
    )
    loop, _ = build_loop(brain_dir, tmp_path, wall, [], providers=[provider])
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert result.rounds == 3
    second = provider.calls[1][0]
    assert all(
        m.model == UNSIGNED_MODEL and not m.thought_signature
        for m in second
        if m.role == "assistant"
    )


async def test_refused_twice_is_an_error_not_a_loop(brain_dir, tmp_path, wall):
    await _crash_after_signed_turn(brain_dir, tmp_path, wall)

    provider = ScriptedProvider(
        "gemini:gemini-a",
        [
            ProviderError("nope", kind="bad_request"),
            ProviderError("still no", kind="bad_request"),
        ],
    )
    loop, _ = build_loop(brain_dir, tmp_path, wall, [], providers=[provider])
    result = await loop.run_cycle()

    assert result.status == "error"
    assert len(provider.calls) == 2


async def test_fresh_cycle_bad_request_is_still_fatal(brain_dir, tmp_path, wall):
    provider = ScriptedProvider("fake:1", [ProviderError("mine", kind="bad_request")])
    loop, _ = build_loop(brain_dir, tmp_path, wall, [], providers=[provider])
    result = await loop.run_cycle()
    assert result.status == "error"
    assert len(provider.calls) == 1


async def test_later_bad_request_on_a_resumed_cycle_is_fatal(brain_dir, tmp_path, wall):
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])

    provider = ScriptedProvider(
        "fake:1",
        [reply("", call("list_facts", "a")), ProviderError("mine", kind="bad_request")],
    )
    loop, _ = build_loop(brain_dir, tmp_path, wall, [], providers=[provider])
    result = await loop.run_cycle()
    assert result.status == "error"
    assert len(provider.calls) == 2


def test_unsigned_turn_leaves_other_roles_alone():
    from ai_brain.sessions import unsigned_turn

    user = Message("user", "hi")
    assert unsigned_turn(user) is user


async def test_external_sources_come_back_from_the_log(brain_dir, tmp_path, wall):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("w", "web_fetch", {})),
            {"type": "tool_call", "round": 1, "id": "w", "name": "web_fetch"},
            {
                "type": "tool_result",
                "round": 1,
                "id": "w",
                "name": "web_fetch",
                "result": json.dumps({"text": '<external source="web">hello</external>'}),
            },
        ],
    )
    state = recover(path, read_events(path), lambda _n: True, lambda _n, r: r)
    assert state.external_sources == {"web"}


def test_an_agents_memory_is_not_an_outside_read_but_its_tag_is(brain_dir):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("a", "read_expert", {}), ("b", "read_fact", {})),
            {
                "type": "tool_result",
                "round": 1,
                "id": "a",
                "name": "read_expert",
                "result": json.dumps(
                    {
                        "body": '<external source="fact-energy">x</external>',
                        "written_after_reading": ["web"],
                    }
                ),
            },
            {
                "type": "tool_result",
                "round": 1,
                "id": "b",
                "name": "read_fact",
                "result": json.dumps({"body": "y", "written_after_reading": ["docker"]}),
            },
        ],
    )
    state = recover(path, read_events(path), lambda _n: True, lambda _n, r: r)
    assert state.external_sources == {"web", "docker"}


def test_real_sigkill_inside_a_tool_resumes_without_rerunning_it(brain_dir, tmp_path, wall):
    """No simulation at all: a child process runs a cycle whose tool SIGKILLs it.

    The marker file is the side effect: it must exist once, written by the
    child, and the resumed cycle in this process must not touch it.
    """
    tests = Path(__file__).resolve().parent
    src = tests.parent / "src"
    marker = tmp_path / "side-effect"
    child = f"""
import asyncio, os, signal, sys
sys.path[:0] = [{str(src)!r}, {str(tests)!r}]
from pathlib import Path
from sessions_helpers import build_loop, build_registry, call, reply
from ai_brain.llm import ToolSpec
from ai_brain.memory import MemoryDir
from ai_brain.tools import Tool

async def fatal(_ctx, _args):
    with open({str(marker)!r}, "a") as fh:
        fh.write("x")
    os.kill(os.getpid(), signal.SIGKILL)

tool = Tool(
    spec=ToolSpec(name="fatal", description="", parameters={{"type": "object", "properties": {{}}}}),
    fn=fatal,
)
memory = MemoryDir(Path({str(brain_dir.root)!r}), "brain", True)
loop, _ = build_loop(
    memory,
    Path({str(tmp_path)!r}),
    lambda: 1_800_000_000.0,
    [reply("", call("list_facts", "a")), reply("about to die", call("fatal", "f"))],
    registry=build_registry(tool),
)
asyncio.run(loop.run_cycle())
"""
    done = subprocess.run([sys.executable, "-c", child], check=False)
    assert done.returncode == -9
    assert marker.read_text() == "x"
    assert list(brain_dir.done_dir.glob("*.md")) == []

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end("back"))])
    result = asyncio.run(loop.run_cycle())

    assert result.status == "ok"
    assert result.rounds == 3
    assert marker.read_text() == "x"
    assert "outcome unknown" in _tool_messages(provider.calls[0][0])["f"]
    assert provider.calls[0][0][5].content == "about to die"


# -- what a second look at the order of things turned up -------------------


@pytest.mark.parametrize(
    "tail",
    [
        [
            _reply_event(("e", "end_cycle", {"next_wake_minutes": 5, "summary": "done"})),
            {"type": "tool_call", "round": 1, "id": "e", "name": "end_cycle"},
            {
                "type": "tool_result",
                "round": 1,
                "id": "e",
                "name": "end_cycle",
                "result": json.dumps({"ok": True}),
            },
        ],
        [_reply_event(text="that is all")],
    ],
    ids=["end_cycle", "no_tool_calls"],
)
async def test_note_arriving_after_the_model_finished_is_not_archived_unread(
    brain_dir, tmp_path, wall, tail
):
    _crashed_session(brain_dir, [_opening(), *[dict(e) for e in tail]])
    brain_dir.drop_note("filip", "topic: pool\nare you there?")

    loop, provider = build_loop(brain_dir, tmp_path, wall, [])
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert provider.calls == []
    assert [n.body for n in brain_dir.unread_notes()] == ["topic: pool\nare you there?"]
    # And nothing waits for the next heartbeat to read it.
    assert loop.wake.is_set()


async def test_note_arriving_with_the_cycle_at_its_cap_is_not_archived_unread(
    brain_dir, tmp_path, wall
):
    _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("a", "list_facts", {}), caps={"effective": 1}),
            {"type": "tool_call", "round": 1, "id": "a", "name": "list_facts"},
            {"type": "tool_result", "round": 1, "id": "a", "name": "list_facts", "result": "[]"},
        ],
    )
    brain_dir.drop_note("filip", "topic: pool\nare you there?")

    loop, provider = build_loop(brain_dir, tmp_path, wall, [])
    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    assert provider.calls == []
    assert len(brain_dir.unread_notes()) == 1
    assert loop.wake.is_set()


class _Slack:
    def __init__(self) -> None:
        self.posts: list[str] = []
        self.unanswered: list[str] = []

    async def post(self, topic, text):
        self.posts.append(topic)
        return "1.0"

    def resolve_topic(self, topic):
        return topic

    def recent_topics(self):
        return []

    async def mark_unanswered(self, topic):
        self.unanswered.append(topic)


async def _no_budget(loop):
    async def _dry(*_a, **_k):
        raise ChainExhausted(retry_at=None)

    loop.chain.complete = _dry  # type: ignore[method-assign]
    assert (await loop.run_cycle()).status == "no_budget"


async def test_second_question_is_still_owed_after_a_second_resume(brain_dir, tmp_path, wall):
    """An earlier post on the topic must not count as answering a later question."""
    brain_dir.drop_note("filip", "topic: pool\nfirst question")
    slack = _Slack()
    first, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("slack_post", "s", topic="pool", text="an answer"))],
        extras={"slack_out": slack},
    )
    real = first.chain.complete
    calls = {"n": 0}

    async def _then_dry(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ChainExhausted(retry_at=None)
        return await real(*a, **k)

    first.chain.complete = _then_dry  # type: ignore[method-assign]
    assert (await first.run_cycle()).status == "no_budget"

    brain_dir.drop_note("filip", "topic: pool\nsecond question")
    resumed, _ = build_loop(brain_dir, tmp_path, wall, [], extras={"slack_out": slack})
    await _no_budget(resumed)

    again, provider = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", end()), reply("", call("slack_post", "t", topic="pool", text="second answer")), reply("", end("done", "e2"))],
        extras={"slack_out": slack},
    )
    await again.run_cycle()

    assert "still waiting" in _tool_messages(provider.calls[1][0])["end"]
    assert slack.posts == ["pool", "pool"]
    assert slack.unanswered == []


async def test_bare_dm_is_not_answered_by_an_earlier_post(brain_dir, tmp_path, wall):
    path = _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(("s", "slack_post", {"topic": "pool", "text": "x"})),
            {"type": "tool_call", "round": 1, "id": "s", "name": "slack_post"},
            {
                "type": "tool_result",
                "round": 1,
                "id": "s",
                "name": "slack_post",
                "result": json.dumps({"ok": True}),
            },
            {"type": "message", "kind": "inbox", "content": "new", "notes": ["late.md"]},
        ],
    )
    state = recover(path, read_events(path), lambda _n: False, lambda _n, r, *_: r)
    from ai_brain.loop import _owed_after
    from ai_brain.memory import Note

    note = Note(path=brain_dir.inbox_dir / "late.md", sender="filip", body="bare", created=None)
    assert _owed_after(state, [note]) == {"*"}


async def test_refusal_does_not_waive_a_note_that_came_after_it(brain_dir, tmp_path, wall):
    brain_dir.drop_note("filip", "topic: pool\nfirst")
    name = brain_dir.unread_notes()[0].path.name
    _crashed_session(
        brain_dir,
        [
            _opening([name]),
            _reply_event(("e", "end_cycle", {"next_wake_minutes": 5, "summary": "x"})),
            {"type": "tool_call", "round": 1, "id": "e", "name": "end_cycle"},
            {
                "type": "tool_result",
                "round": 1,
                "id": "e",
                "name": "end_cycle",
                "result": json.dumps({"error": "Filip is still waiting"}),
            },
        ],
    )
    brain_dir.drop_note("filip", "topic: spa\nsecond")

    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end()), reply("", end("gave up", "end2"))]
    )
    await loop.run_cycle()

    assert "still waiting" in _tool_messages(provider.calls[1][0])["end"]


async def test_abandoned_cycle_does_not_answer_filip_twice(brain_dir, tmp_path, wall):
    brain_dir.drop_note("filip", "topic: pool\nwhat is the temperature?")
    brain_dir.drop_note("energy", "unrelated note from an expert")
    slack = _Slack()
    await _crash_mid_cycle(
        brain_dir,
        tmp_path,
        wall,
        [
            reply("", call("slack_post", "s", topic="pool", text="27 degrees")),
            reply("", call("die", "b")),
        ],
        extras={"slack_out": slack},
    )
    wall.state["t"] += 27 * 3600

    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end())], extras={"slack_out": slack}
    )
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert len(provider.calls) == 1
    assert slack.posts == ["pool"]
    rendered = "\n".join(m.content for m in provider.calls[0][0])
    assert "what is the temperature" not in rendered
    assert "unrelated note from an expert" in rendered
    assert "already answered" in brain_dir.journal_text(2)


async def test_unanswered_note_survives_an_abandon(brain_dir, tmp_path, wall):
    brain_dir.drop_note("filip", "topic: pool\nstill unanswered")
    await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])
    wall.state["t"] += 27 * 3600

    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end()), reply("", end("x", "end2"))]
    )
    await loop.run_cycle()

    assert "still unanswered" in "\n".join(m.content for m in provider.calls[0][0])


async def test_log_with_lost_events_at_the_end_is_not_resumed(brain_dir, tmp_path, wall, monkeypatch):
    """The disk fills after round 1, a tool still runs, the disk is freed. Nothing
    of round 2 is on record, so resuming would have the model repeat what it did."""
    import ai_brain.sessions as module

    effects: list[str] = []
    real_write = module.os.write
    failing = {"on": False}

    def _write(fd, data):
        if failing["on"]:
            raise OSError("disk full")
        return real_write(fd, data)

    monkeypatch.setattr(module.os, "write", _write)

    async def _effect(_ctx, _args):
        effects.append("posted")
        return "{}"

    async def _fill(_ctx, _args):
        failing["on"] = True
        return "[]"

    registry = build_registry(_tool("effect", _effect), _tool("fill", _fill))
    first, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("fill", "a")), reply("", call("effect", "b"))],
        registry=registry,
    )
    real = first.chain.complete
    calls = {"n": 0}

    async def _then_dry(*a, **k):
        calls["n"] += 1
        if calls["n"] > 2:
            raise ChainExhausted(retry_at=None)
        return await real(*a, **k)

    first.chain.complete = _then_dry  # type: ignore[method-assign]
    assert (await first.run_cycle()).status == "no_budget"
    assert effects == ["posted"]
    failing["on"] = False

    second, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())], registry=registry)
    await second.run_cycle()

    assert not any("interrupted" in m.content for m in provider.calls[0][0])
    assert any(p.name.endswith(".holes") for p in session_files(brain_dir.root / "sessions"))


async def test_budget_stop_summary_names_the_budget_that_stopped_it(brain_dir, tmp_path, wall):
    _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(
                ("a", "list_facts", {}),
                caps={"effective": 2, "budget_stopped": True, "floor": 2, "budget_hit": 5},
            ),
            {"type": "tool_call", "round": 1, "id": "a", "name": "list_facts"},
            {"type": "tool_result", "round": 1, "id": "a", "name": "list_facts", "result": "[]"},
        ],
    )

    loop, _ = build_loop(
        brain_dir, tmp_path, wall, [reply("", call("list_facts", "b"))], max_prompt_tokens=999_999
    )
    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    assert "CYCLE_MAX_PROMPT_TOKENS (5)" in brain_dir.journal_text(1)


# -- with the quota clamp and the saved wake --------------------------------


async def test_cap_the_quota_set_is_not_restored_as_it_stands(brain_dir, tmp_path, wall):
    """Clamped by yesterday's quota and killed: today's cycle carries on, rather
    than closing at a cap that was only ever about what was left back then."""
    _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(
                ("a", "list_facts", {}),
                caps={"effective": 1, "budget_stopped": False, "floor": None, "quota_stopped": True},
            ),
            {"type": "tool_call", "round": 1, "id": "a", "name": "list_facts"},
            {"type": "tool_result", "round": 1, "id": "a", "name": "list_facts", "result": "[]"},
        ],
    )

    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end("carried on"))], max_rounds=8)
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert result.rounds == 2
    assert len(provider.calls) == 1


async def test_budget_stop_beats_a_quota_clamp(brain_dir, tmp_path, wall):
    _crashed_session(
        brain_dir,
        [
            _opening(),
            _reply_event(
                ("a", "list_facts", {}),
                caps={"effective": 1, "budget_stopped": True, "floor": 1, "quota_stopped": True},
            ),
            {"type": "tool_call", "round": 1, "id": "a", "name": "list_facts"},
            {"type": "tool_result", "round": 1, "id": "a", "name": "list_facts", "result": "[]"},
        ],
    )

    loop, provider = build_loop(brain_dir, tmp_path, wall, [], max_rounds=8)
    result = await loop.run_cycle()

    assert result.status == "max_rounds"
    assert provider.calls == []


async def test_caps_on_the_log_say_when_the_quota_set_them(brain_dir, tmp_path, wall):
    loop, _ = build_loop(
        brain_dir, tmp_path, wall, [reply("", call("list_facts", "a")), reply("", end())], max_rounds=8
    )
    loop.chain.quota_runway = lambda *_a, **_k: 1  # type: ignore[method-assign]

    await loop.run_cycle()

    events = read_events(next(iter(session_files(brain_dir.root / "sessions"))))
    assert next(e for e in events if e["type"] == "reply")["caps"]["quota_stopped"] is True


async def test_unfinished_cycle_does_not_wait_out_an_earlier_wake(brain_dir, tmp_path, wall):
    loop, _ = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    await loop.run_cycle()
    assert loop._startup_delay_s() > 0

    _crashed_session(brain_dir, [_opening(), _reply_event(("a", "list_facts", {}))])

    assert loop._startup_delay_s() == 0


async def test_no_budget_still_waits_for_the_wake_it_chose(brain_dir, tmp_path, wall):
    loop, _ = build_loop(brain_dir, tmp_path, wall, [reply("", call("list_facts", "a"))])
    real = loop.chain.complete
    calls = {"n": 0}

    async def _then_dry(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ChainExhausted(retry_at=wall() + 3600)
        return await real(*a, **k)

    loop.chain.complete = _then_dry  # type: ignore[method-assign]
    assert (await loop.run_cycle()).status == "no_budget"

    assert loop._startup_delay_s() == pytest.approx(3600, abs=1)


# -- a death is the whole process -------------------------------------------


async def test_whole_process_deaths_cost_no_loop_its_unread_notes(
    brain_dir, expert_dir, tmp_path, wall
):
    """Only the brain's cycle is at fault here, but a kill takes both loops, so
    both count every death. The innocent expert must lose nothing, and nor must
    the brain: nothing on record can tell them apart."""
    brain_dir.drop_note("filip", "topic: pool\nfilip's question")
    expert_dir.drop_note("brain", "the brain's request")
    memories = {"brain": brain_dir, "energy": expert_dir}

    for attempt in range(4):
        for memory in (brain_dir, expert_dir):
            await _crash_mid_cycle(
                memory,
                tmp_path,
                wall,
                [reply("", call("die", f"d{attempt}"))],
                memories=memories,
            )

    for memory, text in ((brain_dir, "filip's question"), (expert_dir, "the brain's request")):
        loop, provider = build_loop(memory, tmp_path, wall, [reply("", end()), reply("", end("x", "e2"))], memories=memories)
        result = await loop.run_cycle()

        # The brain's note is Filip's, which skips the hold; the expert's waits.
        if memory is expert_dir:
            assert result.status == "abandoned"
            assert provider.calls == []
            assert list(memory.done_dir.glob("*.md")) == []
            assert [text in n.body for n in memory.unread_notes()] == [True]
            await loop.run_cycle()
        assert text in "\n".join(m.content for m in provider.calls[0][0])


async def test_giving_up_holds_off_across_a_restart_even_with_notes_unread(
    brain_dir, tmp_path, wall
):
    """Unread notes normally end a restart's wait, but these were in the cycle
    that kept dying, or a crash loop would run at the speed of the restart."""
    brain_dir.drop_note("energy", "maybe the cause")
    for _ in range(4):
        await _crash_mid_cycle(brain_dir, tmp_path, wall, [reply("", call("die", "b"))])
    gave_up, _ = build_loop(brain_dir, tmp_path, wall, [])
    assert (await gave_up.run_cycle()).status == "abandoned"

    restarted, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end()), reply("", end("x", "e2"))]
    )
    assert restarted._startup_delay_s() == pytest.approx(restarted.heartbeat_s, abs=1)

    task = asyncio.create_task(restarted.run_forever())
    for _ in range(50):
        await asyncio.sleep(0)
    assert provider.calls == []
    assert not task.done()

    # A note that arrives while the process is up still rings.
    restarted.wake.set()
    for _ in range(50):
        await asyncio.sleep(0)
    task.cancel()
    assert provider.calls


async def test_a_waiting_filip_note_skips_the_hold_once(brain_dir, tmp_path, wall):
    """Up to 4h of silence is what the hold costs him, so his note gets one fresh
    cycle straight away, even though it was in the cycle that kept dying."""
    brain_dir.drop_note("filip", "topic: pool\nare you there")
    for _ in range(4):
        await _die_in_the_first_call(brain_dir, tmp_path, wall)

    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", end()), reply("", end("x", "e2"))]
    )
    result = await loop.run_cycle()

    assert result.status == "ok"
    assert "are you there" in "\n".join(m.content for m in provider.calls[0][0])
    assert "[abandoned]" in brain_dir.journal_text(1)
    assert loop.cycle_counts["abandoned"] == 1


async def test_a_filip_note_that_keeps_killing_waits_after_its_one_retry(
    brain_dir, tmp_path, wall
):
    brain_dir.drop_note("filip", "topic: pool\nthis one kills the process")
    results = []
    calls = []

    async def _round() -> None:
        for _ in range(4):
            await _die_in_the_first_call(brain_dir, tmp_path, wall)

    await _round()
    # The retry, on record before its cycle runs, so a new process knows it was spent.
    await _die_in_the_first_call(brain_dir, tmp_path, wall)
    for _ in range(3):
        await _die_in_the_first_call(brain_dir, tmp_path, wall)
    for _ in range(2):
        loop, provider = build_loop(brain_dir, tmp_path, wall, [])
        results.append((await loop.run_cycle()).status)
        calls.append(provider.calls)
        await _round()

    assert results == ["abandoned", "abandoned"]
    assert calls == [[], []]
    assert len(brain_dir.unread_notes()) == 1


async def test_a_retried_note_waits_across_a_restart_but_a_new_one_rings(
    brain_dir, tmp_path, wall
):
    brain_dir.drop_note("filip", "topic: pool\nfirst")
    for _ in range(8):
        await _die_in_the_first_call(brain_dir, tmp_path, wall)
    gave_up, _ = build_loop(brain_dir, tmp_path, wall, [])
    assert (await gave_up.run_cycle()).status == "abandoned"

    restarted, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    task = asyncio.create_task(restarted.run_forever())
    await asyncio.sleep(0.05)
    assert provider.calls == []
    task.cancel()

    brain_dir.drop_note("filip", "topic: pool\nsecond, while the process was down")
    restarted, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    task = asyncio.create_task(restarted.run_forever())
    await asyncio.sleep(0.05)
    task.cancel()
    assert provider.calls


async def test_state_without_retried_notes_loads(brain_dir, tmp_path, wall):
    """What a Pi's existing _state.json looks like on the first boot of this image."""
    (brain_dir.root / "_state.json").write_text(
        json.dumps(
            {
                "last_cycle": {"status": "ok", "model": "m", "rounds": 1, "cap": 16, "next_wake_s": 60},
                "last_cycle_at": wall(),
                "cycle_counts": {"ok": 1},
            }
        )
    )
    loop, provider = build_loop(brain_dir, tmp_path, wall, [reply("", end())])

    assert loop.last_cycle.status == "ok"
    assert loop._owner_retried == set()
    assert (await loop.run_cycle()).status == "ok"
    assert provider.calls


async def test_deaths_stay_bounded_per_heartbeat(brain_dir, tmp_path, wall):
    """After holding off, a note that keeps killing starts the count again rather
    than being retried at the speed of the restart policy."""
    brain_dir.drop_note("energy", "keeps killing")
    results = []
    for _ in range(2):
        for _ in range(4):
            await _die_in_the_first_call(brain_dir, tmp_path, wall)
        loop, provider = build_loop(brain_dir, tmp_path, wall, [])
        results.append((await loop.run_cycle()).status)
        assert provider.calls == []
    assert results == ["abandoned", "abandoned"]
    assert len(brain_dir.unread_notes()) == 1


async def test_refused_history_retry_announces_the_round_once(brain_dir, tmp_path, wall):
    """The live feed adds a pending entry per round_started and resolves one per
    round, so a retry's would stay pending for good."""
    from ai_brain.events import EventBus

    _crashed_session(brain_dir, [_opening(), _reply_event(("a", "list_facts", {}))])
    provider = ScriptedProvider(
        "fake:1", [ProviderError("invalid signature", kind="bad_request"), reply("", end())]
    )
    bus = EventBus()
    loop, _ = build_loop(brain_dir, tmp_path, wall, [], providers=[provider], events=bus)

    with bus.subscribe() as queue:
        await loop.run_cycle()
        events = [queue.get_nowait() for _ in range(queue.qsize())]

    assert len(provider.calls) == 2
    assert [e["type"] for e in events].count("round_started") == 1
