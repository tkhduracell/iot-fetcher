import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_brain.approvals import KINDS, Approvals, Proposal
from ai_brain.config import load_settings
from ai_brain.executors import QuietHours
from ai_brain.llm import ToolCall
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.propose import register_propose_tool

START = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)


class FakeExecutors:
    """Records what it was asked to run, and can be told to misbehave."""

    def __init__(self, raises: Exception | None = None, result: str = "queued") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.raises = raises
        self.result = result

    async def run(self, kind: str, payload: dict) -> str:
        self.calls.append((kind, payload))
        if self.raises is not None:
            raise self.raises
        return self.result


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []
        self.next_ts = 0

    async def __call__(self, topic: str, text: str) -> str:
        self.posts.append((topic, text))
        self.next_ts += 1
        return f"ts-{self.next_ts}"


@pytest.fixture
def executors():
    return FakeExecutors()


@pytest.fixture
def slack():
    return FakeSlack()


@pytest.fixture
def moving_clock():
    state = {"now": START}

    def _now():
        return state["now"]

    _now.state = state
    return _now


@pytest.fixture
def approvals(brain_dir, executors, moving_clock, slack):
    return Approvals(brain_dir, executors, moving_clock, on_message=slack)


def notes(brain_dir) -> list[str]:
    return [n.body for n in brain_dir.unread_notes() if n.sender == "approvals"]


async def call_propose(brain_dir, approvals) -> str:
    """Drive the propose *tool*, so the caller sees what the model would."""
    registry = ToolRegistry()
    register_propose_tool(registry)
    ctx = ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir},
        settings=load_settings({}),
        wake=lambda name: None,
        extras={"approvals": approvals},
    )
    return await registry.dispatch(
        ctx,
        ToolCall(
            id="1",
            name="propose",
            args={
                "kind": "sonos_say",
                "payload": {"text": "hi"},
                "reason": "why",
                "topic": "#home",
            },
        ),
    )


# --- propose --------------------------------------------------------------


async def test_propose_writes_a_pending_file(approvals, brain_dir):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "user asked", "#home")
    assert p.status == "pending"
    path = brain_dir.outbox_dir / f"{p.id}.json"
    assert json.loads(path.read_text()) == {
        "id": p.id,
        "kind": "sonos_say",
        "payload": {"text": "hi"},
        "reason": "user asked",
        "topic": "#home",
        "created": p.created,
        "status": "pending",
        "slack_ts": "ts-1",
        "result": "",
    }


async def test_propose_id_encodes_time_and_kind(approvals):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    stamp, kind, suffix = p.id.split("-")
    assert stamp == "20260906T100000"
    assert kind == "sonos_say"
    assert len(suffix) == 4
    assert int(suffix, 16) >= 0


async def test_two_proposals_in_the_same_second_get_distinct_ids(approvals):
    a = await approvals.propose("sonos_say", {"text": "a"}, "why", "#home")
    b = await approvals.propose("sonos_say", {"text": "b"}, "why", "#home")
    assert a.id != b.id


async def test_propose_posts_to_slack_and_keeps_the_ts(approvals, slack):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "user asked", "#home")
    assert p.slack_ts == "ts-1"
    topic, text = slack.posts[0]
    assert topic == "#home"
    assert f"Proposal {p.id} (sonos_say): user asked" in text
    assert '{"text": "hi"}' in text
    assert "React" in text


async def test_propose_without_slack_is_failed_not_pending(brain_dir, executors, moving_clock):
    """No Slack means no ✅ to press, so nothing may be left waiting for one."""
    approvals = Approvals(brain_dir, executors, moving_clock)
    with pytest.raises(RuntimeError, match="slack not configured"):
        await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")

    assert approvals.pending() == []
    stored = [json.loads(f.read_text()) for f in brain_dir.outbox_dir.glob("*.json")]
    assert [(s["status"], s["result"]) for s in stored] == [
        ("failed", "slack not configured, not proposed")
    ]
    assert notes(brain_dir) == [
        f"proposal {stored[0]['id']} failed: slack not configured, not proposed"
    ]


async def test_propose_reports_a_raising_poster_as_failed(brain_dir, executors, moving_clock):
    """The hourly cap raises out of post(); that is a failed proposal, not a pending one."""

    async def capped(topic: str, text: str) -> str:
        raise RuntimeError("slack post cap reached (20/h); try again next cycle")

    approvals = Approvals(brain_dir, executors, moving_clock, on_message=capped)
    with pytest.raises(RuntimeError, match="slack unavailable"):
        await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")

    assert approvals.pending() == []
    stored = [json.loads(f.read_text()) for f in brain_dir.outbox_dir.glob("*.json")]
    assert stored[0]["status"] == "failed"
    assert "cap reached" in stored[0]["result"]
    assert notes(brain_dir) == [f"proposal {stored[0]['id']} failed: {stored[0]['result']}"]


async def test_a_capped_post_makes_the_propose_tool_return_an_error(
    brain_dir, executors, moving_clock
):
    async def capped(topic: str, text: str) -> str:
        raise RuntimeError("slack post cap reached (20/h)")

    approvals = Approvals(brain_dir, executors, moving_clock, on_message=capped)
    out = json.loads(await call_propose(brain_dir, approvals))
    assert "slack unavailable" in out["error"]
    assert approvals.pending() == []


async def test_propose_rejects_an_unknown_kind(approvals):
    with pytest.raises(ValueError, match="unknown kind"):
        await approvals.propose("launch_missiles", {}, "why", "#home")


async def test_propose_rejects_a_payload_missing_its_key(approvals):
    with pytest.raises(ValueError, match="text"):
        await approvals.propose("sonos_say", {"nope": "hi"}, "why", "#home")


async def test_propose_never_executes(approvals, executors):
    await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    assert executors.calls == []


# --- pending --------------------------------------------------------------


async def test_pending_lists_only_pending_proposals(approvals):
    a = await approvals.propose("sonos_say", {"text": "a"}, "why", "#home")
    await approvals.propose("sonos_say", {"text": "b"}, "why", "#home")
    await approvals.on_reaction(a.slack_ts, "x")
    assert [p.payload["text"] for p in approvals.pending()] == ["b"]


def test_pending_is_empty_on_a_fresh_brain(approvals):
    assert approvals.pending() == []


async def test_pending_reads_from_disk(brain_dir, executors, moving_clock, slack):
    writer = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    await writer.propose("ha_todo_add", {"item": "milk"}, "why", "#home")
    reader = Approvals(brain_dir, executors, moving_clock)
    assert [p.kind for p in reader.pending()] == ["ha_todo_add"]


# --- approve --------------------------------------------------------------


async def test_check_mark_executes_and_marks_executed(approvals, executors, brain_dir):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    done = await approvals.on_reaction(p.slack_ts, "white_check_mark")
    assert (done.status, done.result) == ("executed", "queued")
    assert executors.calls == [("sonos_say", {"text": "hi"})]
    assert approvals.pending() == []
    assert notes(brain_dir) == [f"proposal {p.id} executed: queued"]


async def test_a_second_reaction_does_not_execute_again(approvals, executors):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(p.slack_ts, "white_check_mark")
    assert await approvals.on_reaction(p.slack_ts, "white_check_mark") is None
    assert len(executors.calls) == 1


async def test_two_concurrent_check_marks_execute_once(brain_dir, moving_clock, slack):
    """Slack retries deliveries; two in the same tick must not both execute.

    The executor yields inside ``run``, which is exactly the window the old
    code lost the race in: both callers had already read ``pending`` from disk.
    """

    class SlowExecutors:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def run(self, kind: str, payload: dict) -> str:
            self.calls.append((kind, payload))
            await asyncio.sleep(0)
            return "queued"

    executors = SlowExecutors()
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")

    await asyncio.gather(
        approvals.on_reaction(p.slack_ts, "white_check_mark"),
        approvals.on_reaction(p.slack_ts, "white_check_mark"),
    )

    assert executors.calls == [("sonos_say", {"text": "hi"})]
    stored = json.loads((brain_dir.outbox_dir / f"{p.id}.json").read_text())
    assert stored["status"] == "executed"
    assert notes(brain_dir) == [f"proposal {p.id} executed: queued"]


async def test_an_executing_proposal_is_not_pending_and_ignores_a_reaction(
    brain_dir, executors, moving_clock
):
    """A process that died mid-execution leaves ``executing`` behind for good."""
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=FakeSlack())
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    p.slack_ts = "ts-9"
    p.status = "executing"
    approvals._store(p)

    assert approvals.pending() == []
    assert await approvals.expire() == []
    assert await approvals.on_reaction("ts-9", "white_check_mark") is None
    assert executors.calls == []


async def test_a_post_that_returns_nothing_is_not_left_pending(
    brain_dir, executors, moving_clock
):
    """A client that posted nothing leaves no message to react to."""

    async def posts_nothing(topic: str, text: str) -> str | None:
        return None

    approvals = Approvals(brain_dir, executors, moving_clock, on_message=posts_nothing)
    with pytest.raises(RuntimeError, match="slack unavailable"):
        await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")

    assert approvals.pending() == []


async def test_a_queued_post_is_recorded_failed_not_pending(
    brain_dir, executors, moving_clock
):
    """Slack was down, so there is no message anyone can react to.

    ``SlackOut.post`` returns "queued" when the text went to the retry outbox
    instead. Storing that as slack_ts would leave a pending proposal whose
    identity matches no Slack message: flush_queue later delivers the text and
    Slack assigns a real ts that nothing writes back, so Filip's checkmark
    matches nothing and the proposal silently expires after 24h.
    """

    async def queues(topic: str, text: str) -> str:
        return "queued"

    approvals = Approvals(brain_dir, executors, moving_clock, on_message=queues)
    with pytest.raises(RuntimeError, match="slack unavailable"):
        await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")

    assert approvals.pending() == []
    stored = [json.loads(path.read_text()) for path in brain_dir.outbox_dir.glob("*.json")]
    assert len(stored) == 1
    assert stored[0]["status"] == "failed"
    assert stored[0]["result"] == "slack unavailable, not proposed"
    assert stored[0]["slack_ts"] == ""
    assert notes(brain_dir) == [
        f"proposal {stored[0]['id']} failed: slack unavailable, not proposed"
    ]
    assert executors.calls == []


async def test_a_queued_post_makes_the_propose_tool_return_an_error(
    brain_dir, executors, moving_clock
):
    """The model must see err(...) so it can simply propose again later."""

    async def queues(topic: str, text: str) -> str:
        return "queued"

    approvals = Approvals(brain_dir, executors, moving_clock, on_message=queues)
    registry = ToolRegistry()
    register_propose_tool(registry)
    ctx = ToolContext(
        loop="brain",
        memory=brain_dir,
        memories={"brain": brain_dir},
        settings=load_settings({}),
        wake=lambda name: None,
        extras={"approvals": approvals},
    )
    out = json.loads(
        await registry.dispatch(
            ctx,
            ToolCall(
                id="1",
                name="propose",
                args={
                    "kind": "sonos_say",
                    "payload": {"text": "hi"},
                    "reason": "why",
                    "topic": "#home",
                },
            ),
        )
    )

    assert "slack unavailable" in out["error"]
    assert approvals.pending() == []


async def test_x_rejects_without_executing(approvals, executors, brain_dir):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    done = await approvals.on_reaction(p.slack_ts, "x")
    assert done.status == "rejected"
    assert executors.calls == []
    assert notes(brain_dir) == [f"proposal {p.id} rejected: "]


async def test_an_unrelated_emoji_is_ignored(approvals, executors, brain_dir):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    assert await approvals.on_reaction(p.slack_ts, "eyes") is None
    assert executors.calls == []
    assert notes(brain_dir) == []
    assert approvals.pending()[0].status == "pending"


async def test_an_unknown_ts_is_ignored(approvals, executors):
    assert await approvals.on_reaction("ts-nope", "white_check_mark") is None
    assert executors.calls == []


async def test_quiet_hours_blocks_instead_of_failing(brain_dir, moving_clock, slack):
    executors = FakeExecutors(raises=QuietHours("22:00-07:00"))
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    done = await approvals.on_reaction(p.slack_ts, "white_check_mark")
    assert done.status == "blocked_quiet_hours"
    assert "quiet hours" in done.result
    assert notes(brain_dir) == [f"proposal {p.id} blocked_quiet_hours: {done.result}"]


async def test_a_blocked_proposal_is_no_longer_pending(brain_dir, moving_clock, slack):
    executors = FakeExecutors(raises=QuietHours("22:00-07:00"))
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(p.slack_ts, "white_check_mark")
    assert approvals.pending() == []


async def test_an_executor_crash_marks_failed(brain_dir, moving_clock, slack):
    executors = FakeExecutors(raises=RuntimeError("sonos returned HTTP 503"))
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    done = await approvals.on_reaction(p.slack_ts, "white_check_mark")
    assert done.status == "failed"
    assert "sonos returned HTTP 503" in done.result
    assert notes(brain_dir) == [f"proposal {p.id} failed: {done.result}"]


async def test_the_outcome_is_persisted(approvals, brain_dir):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(p.slack_ts, "white_check_mark")
    stored = json.loads((brain_dir.outbox_dir / f"{p.id}.json").read_text())
    assert stored["status"] == "executed"
    assert stored["result"] == "queued"


# --- expire ---------------------------------------------------------------


async def test_expire_leaves_fresh_proposals_alone(approvals, moving_clock, brain_dir):
    await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    moving_clock.state["now"] = START + timedelta(hours=23, minutes=59)
    assert await approvals.expire() == []
    assert len(approvals.pending()) == 1
    assert notes(brain_dir) == []


async def test_expire_marks_proposals_older_than_24h(approvals, moving_clock, brain_dir):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    moving_clock.state["now"] = START + timedelta(hours=24, minutes=1)
    expired = await approvals.expire()
    assert [e.id for e in expired] == [p.id]
    assert expired[0].status == "expired"
    assert approvals.pending() == []
    assert notes(brain_dir) == [f"proposal {p.id} expired: no reaction within 24h"]


async def test_expire_never_executes(approvals, moving_clock, executors):
    await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    moving_clock.state["now"] = START + timedelta(days=2)
    await approvals.expire()
    assert executors.calls == []


async def test_an_expired_proposal_ignores_a_late_check_mark(approvals, moving_clock, executors):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    moving_clock.state["now"] = START + timedelta(days=2)
    await approvals.expire()
    assert await approvals.on_reaction(p.slack_ts, "white_check_mark") is None
    assert executors.calls == []


# --- the propose tool -----------------------------------------------------


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_propose_tool(reg)
    return reg


@pytest.fixture
def make_ctx(brain_dir, expert_dir, approvals):
    memories = {"brain": brain_dir, "energy": expert_dir}

    def _make(loop: str = "brain") -> ToolContext:
        return ToolContext(
            loop=loop,
            memory=memories.get(loop, brain_dir),
            memories=memories,
            settings=load_settings({}),
            wake=lambda _loop: None,
            extras={"approvals": approvals},
        )

    return _make


async def call(registry, ctx, **args):
    return json.loads(await registry.dispatch(ctx, ToolCall(id="1", name="propose", args=args)))


def test_propose_is_brain_only(registry):
    assert [s.name for s in registry.specs_for("brain")] == ["propose"]
    assert registry.specs_for("energy") == []


async def test_propose_tool_creates_a_pending_proposal(registry, make_ctx, approvals):
    out = await call(
        registry,
        make_ctx(),
        kind="sonos_say",
        payload={"text": "hi"},
        reason="user asked",
        topic="#home",
    )
    assert out["ok"] is True
    assert out["status"] == "pending"
    assert [p.id for p in approvals.pending()] == [out["id"]]


async def test_propose_tool_is_refused_for_an_expert(registry, make_ctx):
    out = await call(
        registry,
        make_ctx("energy"),
        kind="sonos_say",
        payload={"text": "hi"},
        reason="why",
        topic="#home",
    )
    assert "not allowed" in out["error"]


async def test_propose_tool_reports_a_bad_kind_as_an_error(registry, make_ctx):
    out = await call(
        registry,
        make_ctx(),
        kind="launch_missiles",
        payload={},
        reason="why",
        topic="#home",
    )
    assert "unknown kind" in out["error"]


async def test_propose_tool_reports_a_bad_payload_as_an_error(registry, make_ctx):
    out = await call(
        registry,
        make_ctx(),
        kind="sonos_say",
        payload={"nope": "hi"},
        reason="why",
        topic="#home",
    )
    assert "text" in out["error"]


async def test_propose_tool_rejects_a_non_object_payload(registry, make_ctx):
    out = await call(
        registry,
        make_ctx(),
        kind="sonos_say",
        payload="hi",
        reason="why",
        topic="#home",
    )
    assert "error" in out


async def test_propose_tool_requires_every_argument(registry, make_ctx):
    out = await call(registry, make_ctx(), kind="sonos_say")
    assert "missing required argument" in out["error"]


# --- the kinds table ------------------------------------------------------


def test_kinds_matches_the_executor_surface():
    assert KINDS == {"sonos_say": ("text",), "ha_todo_add": ("item",)}


def test_every_kind_requires_at_least_one_key():
    # propose() rejects a non-dict payload via the missing-key check, which
    # only fires when a kind actually names a required key.
    assert all(required for required in KINDS.values())


async def test_propose_rejects_a_non_object_payload(approvals):
    with pytest.raises(ValueError, match="text"):
        await approvals.propose("sonos_say", "hi", "why", "#home")


def test_proposal_round_trips_through_json():
    p = Proposal(
        id="20260906T100000-sonos_say-ab12",
        kind="sonos_say",
        payload={"text": "hi"},
        reason="why",
        topic="#home",
        created="2026-09-06T10:00:00Z",
        status="pending",
    )
    assert Proposal(**json.loads(json.dumps(p.__dict__))) == p


# --- expiry on reaction ---------------------------------------------------


async def test_a_reaction_after_the_deadline_expires_instead_of_executing(
    brain_dir, executors, moving_clock, slack
):
    """expire() runs every ten minutes, so a stale pending proposal is reachable."""
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")

    moving_clock.state["now"] = START + timedelta(hours=25)
    done = await approvals.on_reaction(p.slack_ts, "white_check_mark")

    assert done.status == "expired"
    assert executors.calls == []
    assert approvals.pending() == []
    assert notes(brain_dir) == [f"proposal {p.id} expired: no reaction within 24h"]


async def test_a_reaction_just_inside_the_deadline_still_executes(
    brain_dir, executors, moving_clock, slack
):
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")

    moving_clock.state["now"] = START + timedelta(hours=23, minutes=59)
    done = await approvals.on_reaction(p.slack_ts, "white_check_mark")

    assert done.status == "executed"
    assert executors.calls == [("sonos_say", {"text": "hi"})]


# --- startup recovery -----------------------------------------------------


async def test_recover_stale_finishes_a_proposal_left_executing(
    brain_dir, executors, moving_clock, slack
):
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    p.status = "executing"
    approvals._store(p)

    recovered = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    assert [r.id for r in recovered.recover_stale()] == [p.id]

    stored = json.loads((brain_dir.outbox_dir / f"{p.id}.json").read_text())
    assert stored["status"] == "failed"
    assert stored["result"] == "process restarted mid-execution; outcome unknown"
    assert notes(brain_dir) == [f"proposal {p.id} failed: {stored['result']}"]
    assert executors.calls == []


async def test_recover_stale_leaves_pending_and_terminal_proposals_alone(
    brain_dir, executors, moving_clock, slack
):
    approvals = Approvals(brain_dir, executors, moving_clock, on_message=slack)
    live = await approvals.propose("sonos_say", {"text": "a"}, "why", "#home")
    done = await approvals.propose("sonos_say", {"text": "b"}, "why", "#home")
    await approvals.on_reaction(done.slack_ts, "x")

    assert Approvals(brain_dir, executors, moving_clock).recover_stale() == []
    assert [p.id for p in approvals.pending()] == [live.id]


# --- lock bookkeeping -----------------------------------------------------


async def test_finishing_a_proposal_drops_its_lock(approvals):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(p.slack_ts, "white_check_mark")
    assert approvals._locks == {}


# --- reading everything ---------------------------------------------------


async def test_all_returns_every_proposal_whatever_its_status(approvals):
    live = await approvals.propose("sonos_say", {"text": "a"}, "why", "#home")
    done = await approvals.propose("sonos_say", {"text": "b"}, "why", "#home")
    await approvals.on_reaction(done.slack_ts, "x")

    everything = approvals.all()

    assert [p.id for p in everything] == sorted([live.id, done.id])
    assert {p.status for p in everything} == {"pending", "rejected"}


def test_all_is_empty_before_anything_is_proposed(approvals):
    assert approvals.all() == []
