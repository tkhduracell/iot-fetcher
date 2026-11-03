import asyncio
import gzip
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from sessions_helpers import build_loop, build_registry, call, end, reply

from ai_brain.llm import ChainExhausted, Reply, ToolCall, ToolSpec, Usage
from ai_brain.loop import MAX_TOOL_RESULT_CHARS
from ai_brain.memory import MEMORY_SINK
from ai_brain.redact import MASK
from ai_brain.sessions import (
    GZ_SUFFIX,
    LOG_SUFFIX,
    MAX_EVENT_TEXT_CHARS,
    SessionLog,
    SessionStore,
    clean,
    compress,
    new_cycle_id,
    read_events,
    session_files,
)
from ai_brain.tools import Tool

SECRET = "AIza" + "x" * 35


@pytest.fixture
def wall():
    state = {"t": 1_800_000_000.0}

    def _now() -> float:
        return state["t"]

    _now.state = state
    return _now


def _static_tool(name: str, text: str) -> Tool:
    async def _fn(_ctx, _args) -> str:
        return text

    return Tool(
        spec=ToolSpec(name=name, description=name, parameters={"type": "object", "properties": {}}),
        fn=_fn,
    )


def _only_session(memory) -> list[dict]:
    files = list(session_files(memory.root / "sessions"))
    assert len(files) == 1, files
    return read_events(files[0])


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# -- what a cycle writes ---------------------------------------------------


async def test_cycle_writes_every_event_in_order(brain_dir, tmp_path, wall):
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [
            reply("looking", call("list_facts", "a")),
            reply("done", end("all quiet")),
        ],
    )

    await loop.run_cycle()

    events = _only_session(brain_dir)
    assert _types(events) == [
        "opening",
        "reply",
        "tool_call",
        "tool_result",
        "reply",
        "tool_call",
        "tool_result",
        "cycle_end",
    ]
    assert [e["seq"] for e in events] == list(range(1, 9))
    opening = events[0]
    assert opening["agent"] == "brain"
    assert opening["cycle"] == loop.trace.cycle_id
    assert [m["role"] for m in opening["messages"]] == ["system", "system", "user"]
    assert opening["angle"]
    ended = events[-1]
    assert ended["status"] == "ok"
    assert ended["rounds"] == 2
    assert ended["summary"] == "all quiet"
    assert ended["prompt_tokens"] == 20
    assert ended["lost_events"] == 0


async def test_reply_event_holds_everything_a_message_needs(brain_dir, tmp_path, wall):
    signed = Reply(
        text="thinking aloud",
        tool_calls=(ToolCall(id="a", name="list_facts", args={"x": 1}, thought_signature="sig-1"),),
        usage=Usage(prompt_tokens=100, completion_tokens=7, cached_tokens=50),
        model="fake:1 @ host",
        thought_signature="sig-turn",
        thinking="because reasons",
        key="fake:1",
        provider_blocks=({"type": "thinking", "thinking": "hm", "signature": "sig-block"},),
    )
    loop, _ = build_loop(brain_dir, tmp_path, wall, [signed, reply("", end())])

    await loop.run_cycle()

    first = next(e for e in _only_session(brain_dir) if e["type"] == "reply")
    assert first["text"] == "thinking aloud"
    assert first["thinking"] == "because reasons"
    assert first["thought_signature"] == "sig-turn"
    assert first["model"] == "fake:1 @ host"
    assert first["chain_key"] == "fake:1"
    assert first["tool_calls"] == [
        {"id": "a", "name": "list_facts", "args": {"x": 1}, "thought_signature": "sig-1"}
    ]
    assert first["provider_blocks"] == [
        {"type": "thinking", "thinking": "hm", "signature": "sig-block"}
    ]
    assert first["redacted"] is False
    assert first["budget_tokens"] == 55


async def test_full_result_logged_before_truncation(brain_dir, tmp_path, wall):
    big = "y" * (MAX_TOOL_RESULT_CHARS + 500)
    registry = build_registry(_static_tool("big", big))
    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", call("big", "a")), reply("", end())], registry=registry
    )

    await loop.run_cycle()

    logged = next(e for e in _only_session(brain_dir) if e["type"] == "tool_result")
    assert logged["result"] == big
    assert logged["chars"] == len(big)
    # The conversation still only ever saw the cut version.
    seen = [m for m in provider.calls[-1][0] if m.role == "tool"][0]
    assert len(seen.content) < len(big)


async def test_tool_call_logged_before_dispatch(brain_dir, tmp_path, wall):
    """A tool the process dies inside must still be on record as started."""
    seen: list[list[str]] = []

    async def _spy(_ctx, _args) -> str:
        seen.append(_types(_only_session(brain_dir)))
        return "{}"

    spy = Tool(
        spec=ToolSpec(name="spy", description="spy", parameters={"type": "object", "properties": {}}),
        fn=_spy,
    )
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("spy", "a")), reply("", end())],
        registry=build_registry(spy),
    )

    await loop.run_cycle()

    assert seen == [["opening", "reply", "tool_call"]]


async def test_memory_mutations_logged_with_the_old_body(brain_dir, tmp_path, wall):
    brain_dir.write_fact("pool", "Pool", "the old body of the fact")
    brain_dir.write_fact("doomed", "Doomed", "about to be deleted")
    brain_dir.rewrite_goals("old goals")
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [
            reply(
                "",
                call("write_fact", "a", name="pool", title="Pool", body="a new body for the fact"),
                call("delete_fact", "b", name="doomed"),
                call("rewrite_goals", "c", body="new goals"),
            ),
            reply("", end()),
        ],
    )

    await loop.run_cycle()

    mutations = [e for e in _only_session(brain_dir) if e["type"] == "memory"]
    assert [(m["op"], m["name"], m["old"]) for m in mutations] == [
        ("write_fact", "pool", "the old body of the fact"),
        ("delete_fact", "doomed", "about to be deleted"),
        ("rewrite_goals", "goals", "old goals"),
    ]


async def test_outside_edit_mid_cycle_is_not_logged_as_the_agents(brain_dir, tmp_path, wall):
    """The MCP writes through the same MemoryDir from another task."""

    async def _outside(_ctx, _args) -> str:
        # What the MCP server's request handler is: another task, whose context
        # has no sink. copy_context would inherit one; a fresh Context does not.
        task = asyncio.get_running_loop().create_task(_mcp_edit(), context=_empty_context())
        await task
        return "{}"

    async def _mcp_edit() -> None:
        assert MEMORY_SINK.get() is None
        brain_dir.write_fact("human", "Human", "written by a person over the MCP")

    tool = Tool(
        spec=ToolSpec(name="edit", description="", parameters={"type": "object", "properties": {}}),
        fn=_outside,
    )
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("edit", "a")), reply("", end())],
        registry=build_registry(tool),
    )

    await loop.run_cycle()

    assert not [e for e in _only_session(brain_dir) if e["type"] == "memory"]
    assert brain_dir.read_fact("human") is not None
    # And the sink is gone again once the cycle is over.
    assert MEMORY_SINK.get() is None


def _empty_context():
    import contextvars

    return contextvars.Context()


async def test_nudge_is_logged(brain_dir, tmp_path, wall):
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("list_facts", f"a{i}")) for i in range(2)] + [reply("", end())],
        max_rounds=3,
    )

    await loop.run_cycle()

    nudges = [e for e in _only_session(brain_dir) if e["type"] == "message"]
    assert [n["kind"] for n in nudges] == ["nudge"]
    assert "left" in nudges[0]["content"]


async def test_paused_cycle_leaves_no_session(brain_dir, tmp_path, wall):
    (tmp_path / "PAUSE").touch()
    loop, _ = build_loop(brain_dir, tmp_path, wall, [])

    result = await loop.run_cycle()

    assert result.status == "paused"
    assert list(session_files(brain_dir.root / "sessions")) == []


async def test_crashing_chain_still_closes_the_session(brain_dir, tmp_path, wall):
    loop, _ = build_loop(brain_dir, tmp_path, wall, [])

    async def _boom(*_a, **_k):
        raise ChainExhausted(retry_at=None)

    loop.chain.complete = _boom  # type: ignore[method-assign]

    result = await loop.run_cycle()

    assert result.status == "no_budget"
    assert _only_session(brain_dir)[-1]["status"] == "no_budget"


async def test_no_behaviour_change_when_logging_is_off(brain_dir, tmp_path, wall):
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("list_facts", "a")), reply("", end("fine"))],
        session_retention_days=0,
    )

    result = await loop.run_cycle()

    assert result.status == "ok"
    assert result.rounds == 2
    assert list(session_files(brain_dir.root / "sessions")) == []


# -- redaction -------------------------------------------------------------


async def test_every_text_in_the_log_is_redacted(brain_dir, tmp_path, wall):
    brain_dir.drop_note("energy", f"a key leaked {SECRET}")
    brain_dir.write_fact("pool", "Pool", f"old body with {SECRET} in it")
    registry = build_registry(_static_tool("leaky", f"result with {SECRET}"))
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [
            reply(
                f"text with {SECRET}",
                call("leaky", "a", arg=f"arg with {SECRET}"),
                call("write_fact", "b", name="pool", title="t", body="a new body for the fact"),
            ),
            reply("", end(f"summary with {SECRET}")),
        ],
        registry=registry,
    )
    # write_fact redacts on the way to disk, so plant the old body directly.
    (brain_dir.facts_dir / "pool.md").write_text(f"old body with {SECRET} in it")

    await loop.run_cycle()

    raw = gzip.decompress(next(iter(session_files(brain_dir.root / "sessions"))).read_bytes())
    assert SECRET.encode() not in raw
    assert MASK.encode() in raw
    # A line that redact() rewrote is still a line of valid JSON.
    for line in raw.splitlines():
        json.loads(line)


def test_clean_redacts_by_field_name_and_keeps_opaque_blobs():
    assert clean({"token": "abcdefgh12345"}) == {"token": MASK}
    # Short values next to a key-ish word are ordinary settings, like redact().
    assert clean({"key": "on"}) == {"key": "on"}
    block = {"type": "thinking", "signature": "AIza" + "z" * 35, "data": "token=abcdefghij"}
    assert clean(block, opaque=True) == block
    assert clean(["ok", 1, None, True, {"a": [SECRET]}]) == ["ok", 1, None, True, {"a": [MASK]}]
    # Anything else a model can invent is rendered, never raised on.
    assert clean(object())


def test_opaque_names_are_only_opaque_in_a_providers_blocks():
    """A tool argument called ``data`` is just an argument, and a place a secret
    could sit."""
    assert clean({"data": SECRET}) == {"data": MASK}
    assert clean({"signature": SECRET}) == {"signature": MASK}


def test_a_secret_name_covers_what_is_nested_under_it():
    assert clean({"password": ["hunter2hunter2"]}) == {"password": [MASK]}
    assert clean({"api_key": {"value": "x"}}) == {"api_key": {"value": MASK}}
    assert clean({"client_secret": "short"}) == {"client_secret": MASK}
    assert clean({"bot_token": "abc"}) == {"bot_token": MASK}
    # Numbers are not secrets, and these go in every reply event.
    assert clean({"prompt_tokens": 10}) == {"prompt_tokens": 10}


def test_dict_keys_are_redacted_too():
    assert list(clean({SECRET: 1})) == [MASK]


def test_redact_stays_linear_on_a_long_dotted_token():
    import time

    started = time.monotonic()
    clean("a." * 256_000)
    assert time.monotonic() - started < 5


def test_lone_surrogate_does_not_leave_a_hole(tmp_path, wall):
    log = SessionLog(tmp_path / "s.jsonl", "c", wall)
    assert log.append("reply", text="half an emoji \ud83d") == 1
    assert log.append("next") == 2
    log.close()
    events = read_events(log.path)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["text"] == "half an emoji \ud83d"
    assert log.lost == 0


def test_clean_bounds_a_runaway_string():
    out = clean("z" * (MAX_EVENT_TEXT_CHARS + 10))
    assert len(out) < MAX_EVENT_TEXT_CHARS + 100
    assert out.endswith("[session log cut: 10 more chars]")


def test_arguments_too_deep_to_walk_are_left_out_rather_than_raising():
    circular: dict = {}
    circular["self"] = circular
    assert clean(circular) == "[not logged: nested too deeply]"


async def test_reply_with_redacted_text_is_flagged(brain_dir, tmp_path, wall):
    loop, _ = build_loop(
        brain_dir, tmp_path, wall, [reply(f"oops {SECRET}", end())]
    )

    await loop.run_cycle()

    logged = next(e for e in _only_session(brain_dir) if e["type"] == "reply")
    assert logged["redacted"] is True
    assert SECRET not in logged["text"]


# -- failure ---------------------------------------------------------------


async def test_log_write_failure_never_breaks_a_cycle(brain_dir, tmp_path, wall, monkeypatch):
    import ai_brain.sessions as module

    def _no_disk(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(module.os, "open", _no_disk)
    loop, provider = build_loop(
        brain_dir, tmp_path, wall, [reply("", call("list_facts", "a")), reply("", end("fine"))]
    )

    result = await loop.run_cycle()

    assert result.status == "ok"
    assert result.rounds == 2
    assert len(provider.calls) == 2
    assert "[ok]" in brain_dir.journal_text(1)


def test_failed_write_spends_its_seq_so_the_hole_shows(tmp_path, wall, monkeypatch):
    log = SessionLog(tmp_path / "s.jsonl", "c", wall)
    assert log.append("one") == 1
    real = os.write

    def _fail(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(os, "write", _fail)
    assert log.append("two") is None
    monkeypatch.setattr(os, "write", real)
    assert log.append("three") == 3
    log.close()

    events = read_events(log.path)
    assert [e["seq"] for e in events] == [1, 3]
    assert log.lost == 1


def test_first_failure_marks_the_file_incomplete(tmp_path, wall, monkeypatch):
    """A hole at the very end leaves no seq gap, so the name has to say it."""
    log = SessionLog(tmp_path / "20270101-000000-aaaa.jsonl", "c", wall)
    log.append("one")

    def _fail(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(os, "write", _fail)
    log.append("two")
    log.append("cycle_end")
    monkeypatch.undo()
    log.close()

    assert log.path.name == "20270101-000000-aaaa.jsonl.holes"
    assert not (tmp_path / "20270101-000000-aaaa.jsonl").exists()
    assert [e["seq"] for e in read_events(log.path)] == [1]
    # Still a session as far as anything listing them is concerned, and pruned.
    assert list(session_files(tmp_path)) == [log.path]
    store = SessionStore(tmp_path, wall, retention_days=30, max_mb=0)
    store.housekeep()
    assert log.path.exists()


def test_short_write_terminates_the_torn_line(tmp_path, wall, monkeypatch):
    log = SessionLog(tmp_path / "s.jsonl", "c", wall)
    log.append("one")
    real = os.write
    calls = {"n": 0}

    def _short(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            return real(fd, data[:5])
        return real(fd, data)

    monkeypatch.setattr(os, "write", _short)
    assert log.append("two") is None
    assert log.append("three") == 3
    log.close()

    assert [e["type"] for e in read_events(log.path)] == ["one", "three"]


def test_unserialisable_field_does_not_raise(tmp_path, wall):
    log = SessionLog(tmp_path / "s.jsonl", "c", wall)
    circular: dict = {}
    circular["self"] = circular
    assert log.append("bad", thing=circular) is None
    assert log.append("good") == 2
    log.close()
    assert [e["type"] for e in read_events(log.path)] == ["good"]


def test_session_over_its_byte_cap_stops_writing(tmp_path, wall, monkeypatch):
    import ai_brain.sessions as module

    monkeypatch.setattr(module, "MAX_SESSION_BYTES", 200)
    log = SessionLog(tmp_path / "s.jsonl", "c", wall)
    log.append("one", pad="x" * 50)
    log.append("two", pad="x" * 500)
    log.close()
    assert [e["type"] for e in read_events(log.path)] == ["one"]
    assert log.path.name.endswith(".holes")
    assert log.lost == 1


def test_sigkill_loses_at_most_the_line_being_written(tmp_path):
    """A real child process, killed for real, with nothing flushed by hand."""
    path = tmp_path / "s.jsonl"
    src = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import os, sys, signal, time\n"
        f"sys.path.insert(0, {str(src)!r})\n"
        "from ai_brain.sessions import SessionLog\n"
        f"log = SessionLog(__import__('pathlib').Path({str(path)!r}), 'c', time.time)\n"
        "for n in range(5):\n"
        "    log.append('event', n=n)\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], check=False)
    assert done.returncode == -9
    assert [e["n"] for e in read_events(path)] == [0, 1, 2, 3, 4]


# -- files -----------------------------------------------------------------


def test_cycle_id_is_a_safe_name_that_sorts_by_time(wall):
    from ai_brain.memory import safe_name

    earlier = new_cycle_id(1_800_000_000)
    later = new_cycle_id(1_800_000_060)
    assert safe_name(earlier) == earlier
    assert earlier < later


async def test_finished_session_is_gzipped_and_private(brain_dir, tmp_path, wall):
    loop, _ = build_loop(brain_dir, tmp_path, wall, [reply("", end())])

    await loop.run_cycle()

    files = list(session_files(brain_dir.root / "sessions"))
    assert len(files) == 1
    assert files[0].name.endswith(GZ_SUFFIX)
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


def test_live_log_is_private(tmp_path, wall):
    log = SessionLog(tmp_path / "s.jsonl", "c", wall)
    log.append("one")
    log.close()
    assert stat.S_IMODE((tmp_path / "s.jsonl").stat().st_mode) == 0o600


def test_read_events_tolerates_a_torn_last_line(tmp_path):
    path = tmp_path / "20260101-000000-aaaa.jsonl"
    path.write_text('{"seq": 1, "type": "a"}\n{"seq": 2, "ty')
    assert [e["seq"] for e in read_events(path)] == [1]


def test_read_events_tolerates_a_truncated_gzip(tmp_path):
    path = tmp_path / f"20260101-000000-aaaa{GZ_SUFFIX}"
    body = gzip.compress(b'{"seq": 1, "type": "a"}\n' * 2000)
    path.write_bytes(body[: len(body) // 2])
    assert all(e["seq"] == 1 for e in read_events(path))


def test_read_events_tolerates_a_corrupt_gzip(tmp_path):
    path = tmp_path / f"20260101-000000-aaaa{GZ_SUFFIX}"
    body = bytearray(gzip.compress(b'{"seq": 1, "type": "a"}\n' * 2000))
    body[len(body) // 2] ^= 0xFF
    path.write_bytes(bytes(body))
    assert all(e["seq"] == 1 for e in read_events(path))


def test_read_events_on_a_missing_file_is_empty(tmp_path):
    assert read_events(tmp_path / "nope.jsonl") == []


def test_compress_keeps_the_plain_log_on_failure(tmp_path, monkeypatch):
    path = tmp_path / f"20260101-000000-aaaa{LOG_SUFFIX}"
    path.write_text('{"seq": 1}\n')

    def _fail(*_a):
        raise OSError("no")

    monkeypatch.setattr(os, "replace", _fail)
    assert compress(path) is None
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def _session_file(directory: Path, day: str, suffix: str = GZ_SUFFIX, size: int = 10) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day}-120000-aaaa{suffix}"
    path.write_bytes(b"x" * size)
    return path


def test_prune_by_age_uses_the_name_not_the_mtime(tmp_path):
    now = 1_800_000_000.0  # 2027-01-15
    store = SessionStore(tmp_path, lambda: now, retention_days=30, max_mb=0)
    old = _session_file(tmp_path, "20261201")
    fresh = _session_file(tmp_path, "20270110")
    stranger = tmp_path / "notes.txt"
    stranger.write_text("not a session")
    os.utime(old, (now, now))

    assert store.prune() == 1

    assert not old.exists()
    assert fresh.exists()
    assert stranger.exists()


def test_prune_by_size_drops_the_oldest_first(tmp_path):
    now = 1_800_000_000.0
    store = SessionStore(tmp_path, lambda: now, retention_days=30, max_mb=1)
    first = _session_file(tmp_path, "20270101", size=700_000)
    second = _session_file(tmp_path, "20270102", size=700_000)

    assert store.prune() == 1

    assert not first.exists()
    assert second.exists()


def test_housekeep_compresses_what_a_crash_left_behind(tmp_path):
    now = 1_800_000_000.0
    store = SessionStore(tmp_path, lambda: now, retention_days=30, max_mb=0)
    left = tmp_path / f"20270101-000000-aaaa{LOG_SUFFIX}"
    left.write_text('{"seq": 1, "type": "opening"}\n')
    live = tmp_path / f"20270102-000000-bbbb{LOG_SUFFIX}"
    live.write_text('{"seq": 1, "type": "opening"}\n')

    store.housekeep(keep="20270102-000000-bbbb")

    assert not left.exists()
    assert (tmp_path / f"20270101-000000-aaaa{GZ_SUFFIX}").exists()
    assert live.exists()


async def test_big_log_is_compressed_off_the_event_loop(brain_dir, tmp_path, wall, monkeypatch):
    import threading

    import ai_brain.sessions as module

    monkeypatch.setattr(module, "INLINE_HOUSEKEEP_BYTES", 0)
    monkeypatch.setattr("ai_brain.loop.INLINE_HOUSEKEEP_BYTES", 0)
    seen: list[bool] = []
    real = SessionStore.housekeep

    def _spy(self, keep=""):
        seen.append(threading.current_thread() is threading.main_thread())
        real(self, keep)

    monkeypatch.setattr(SessionStore, "housekeep", _spy)
    loop, _ = build_loop(brain_dir, tmp_path, wall, [reply("", end())])

    await loop.run_cycle()

    assert seen == [False]
    assert next(iter(session_files(brain_dir.root / "sessions"))).name.endswith(GZ_SUFFIX)


async def test_small_log_does_not_yield_before_the_books_close(brain_dir, tmp_path, wall):
    """Nothing else gets to run between a cycle ending and cycle_counts knowing,
    so a reader polling the counts never sees a cycle that is over but uncounted."""
    loop, _ = build_loop(brain_dir, tmp_path, wall, [reply("", end())])
    task = asyncio.create_task(loop.run_cycle())
    for _ in range(50):
        await asyncio.sleep(0)
        if task.done():
            break
    assert task.done()
    assert loop.cycle_counts["ok"] == 1


def test_housekeep_failure_is_contained(brain_dir, tmp_path, wall, monkeypatch):
    loop, _ = build_loop(brain_dir, tmp_path, wall, [reply("", end())])

    def _boom(_self, keep=""):
        raise RuntimeError("housekeeping broke")

    monkeypatch.setattr(SessionStore, "housekeep", _boom)

    result = asyncio.run(loop.run_cycle())

    assert result.status == "ok"


async def test_expert_sessions_live_in_its_own_directory(expert_dir, tmp_path, wall):
    loop, _ = build_loop(expert_dir, tmp_path, wall, [reply("", end())])

    await loop.run_cycle()

    assert len(list(session_files(expert_dir.root / "sessions"))) == 1


async def test_trace_names_its_session_and_api_previews_only(brain_dir, tmp_path, wall):
    from ai_brain.api import _trace_json

    big = "y" * 5000
    registry = build_registry(_static_tool("big", big))
    loop, _ = build_loop(
        brain_dir, tmp_path, wall, [reply("", call("big", "a")), reply("", end())], registry=registry
    )

    await loop.run_cycle()

    body = _trace_json(loop.trace)
    assert body["cycle_id"] == loop.trace.cycle_id
    assert big not in json.dumps(body)
