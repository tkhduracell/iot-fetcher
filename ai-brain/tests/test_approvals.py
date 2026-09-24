import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_brain.approvals import KINDS, Approvals, Proposal, resolved_text
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
        self.blocks: list[list[dict] | None] = []
        self.next_ts = 0

    async def __call__(self, topic: str, text: str, blocks: list[dict] | None = None) -> str:
        self.posts.append((topic, text))
        self.blocks.append(blocks)
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
                "payload": {"text": "the pool pump has been off for an hour"},
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


async def test_propose_posts_approve_and_reject_buttons(approvals, slack):
    from ai_brain.approvals import APPROVE_ACTION, REJECT_ACTION

    p = await approvals.propose("sonos_say", {"text": "hi"}, "user asked", "#home")

    [_section, actions_block] = slack.blocks[0]
    assert actions_block["type"] == "actions"
    buttons = {b["action_id"]: b["value"] for b in actions_block["elements"]}
    assert buttons == {APPROVE_ACTION: p.id, REJECT_ACTION: p.id}


async def test_propose_renders_the_ask_beside_the_buttons(approvals, slack):
    """A message with blocks renders only its blocks.

    Slack drops the top-level ``text`` from the conversation once ``blocks`` is
    present, so the kind, the reason and the payload have to be a block of their
    own. Without this the DM was two buttons and nothing to read -- which is
    what shipped, and what nobody could approve.
    """
    p = await approvals.propose("sonos_say", {"text": "hi"}, "user asked", "#home")

    [section, _actions] = slack.blocks[0]
    assert section["type"] == "section"
    rendered = section["text"]["text"]
    assert p.id in rendered
    assert "sonos_say" in rendered
    assert "user asked" in rendered
    assert '{"text": "hi"}' in rendered


async def test_a_huge_payload_still_fits_a_section(approvals, slack):
    """Slack rejects a section over 3000 chars, which would fail the post and
    leave a proposal nobody was ever asked about."""
    from ai_brain.approvals import SECTION_LIMIT

    await approvals.propose("sonos_say", {"text": "x" * 6000}, "long", "#home")

    [section, _actions] = slack.blocks[0]
    rendered = section["text"]["text"]
    assert len(rendered) <= SECTION_LIMIT
    assert rendered.endswith("…")


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

    async def capped(topic: str, text: str, blocks: list[dict] | None = None) -> str:
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
    async def capped(topic: str, text: str, blocks: list[dict] | None = None) -> str:
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


async def test_propose_rejects_the_reserved_chat_topic(approvals):
    with pytest.raises(ValueError, match="reserved"):
        await approvals.propose("sonos_say", {"text": "hi"}, "why", "chat")
    assert approvals.pending() == []


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


# --- buttons ----------------------------------------------------------


async def test_approve_button_executes_same_as_check_mark(approvals, executors, brain_dir):
    from ai_brain.approvals import APPROVE_ACTION

    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    done = await approvals.on_button(p.slack_ts, APPROVE_ACTION)
    assert (done.status, done.result) == ("executed", "queued")
    assert executors.calls == [("sonos_say", {"text": "hi"})]


async def test_reject_button_rejects_without_executing(approvals, executors):
    from ai_brain.approvals import REJECT_ACTION

    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    done = await approvals.on_button(p.slack_ts, REJECT_ACTION)
    assert done.status == "rejected"
    assert executors.calls == []


async def test_an_unknown_action_id_is_ignored(approvals):
    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    assert await approvals.on_button(p.slack_ts, "some_other_action") is None
    assert approvals.pending() != []


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

    async def posts_nothing(topic: str, text: str, blocks: list[dict] | None = None) -> str | None:
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

    async def queues(topic: str, text: str, blocks: list[dict] | None = None) -> str:
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

    async def queues(topic: str, text: str, blocks: list[dict] | None = None) -> str:
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
                    "payload": {"text": "the garage door is still open"},
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


async def call_list(registry, ctx):
    return json.loads(
        await registry.dispatch(ctx, ToolCall(id="1", name="list_proposals", args={}))
    )


def test_propose_is_brain_only(registry):
    assert {s.name for s in registry.specs_for("brain")} == {"propose", "list_proposals"}
    assert registry.specs_for("energy") == []


async def test_propose_tool_creates_a_pending_proposal(registry, make_ctx, approvals):
    out = await call(
        registry,
        make_ctx(),
        kind="sonos_say",
        payload={"text": "the garage door is still open"},
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


async def test_propose_tool_reports_the_reserved_chat_topic_as_an_error(registry, make_ctx):
    out = await call(
        registry,
        make_ctx(),
        kind="sonos_say",
        payload={"text": "the garage door is still open"},
        reason="why",
        topic="chat",
    )
    assert "reserved" in out["error"]


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
    assert KINDS == {
        "sonos_say": ("text",),
        "ha_todo_add": ("item",),
        "ha_service": ("service", "entity_id"),
        "docker_restart": ("container",),
    }


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


# --- resolved_text ---------------------------------------------------------


def _proposal(status: str, result: str = "") -> Proposal:
    return Proposal(
        id="20260906T100000-sonos_say-ab12",
        kind="sonos_say",
        payload={"text": "hi"},
        reason="testing",
        topic="pool",
        created="2026-09-06T10:00:00Z",
        status=status,
        result=result,
    )


def test_resolved_text_names_the_proposal_and_the_verdict():
    text = resolved_text(_proposal("executed"))
    assert "Proposal 20260906T100000-sonos_say-ab12 (sonos_say): testing" in text
    assert "✅ Approved and run." in text


def test_resolved_text_for_a_rejection():
    assert "❌ Rejected." in resolved_text(_proposal("rejected"))


def test_resolved_text_for_an_expiry():
    assert "⌛" in resolved_text(_proposal("expired"))


def test_resolved_text_includes_the_result_on_failure():
    text = resolved_text(_proposal("failed", result="RuntimeError: boom"))
    assert "⚠️" in text
    assert "RuntimeError: boom" in text


def test_resolved_text_includes_the_result_when_blocked_by_quiet_hours():
    text = resolved_text(_proposal("blocked_quiet_hours", result="not run during quiet hours"))
    assert "🌙" in text
    assert "not run during quiet hours" in text


def test_resolved_text_omits_an_empty_result():
    # executed's happy path carries no result worth repeating -- an empty
    # string must not show up as a trailing space or a stray "None".
    text = resolved_text(_proposal("executed", result=""))
    assert text.endswith("✅ Approved and run.")


def test_resolved_text_covers_every_terminal_status():
    # STATUSES includes "pending"/"executing", which a resolved message never
    # reaches -- resolved_text only has to cover what on_button/on_reaction's
    # _finish can actually produce.
    from ai_brain.approvals import RESOLVED_WORDING

    terminal = {"executed", "failed", "rejected", "blocked_quiet_hours", "expired"}
    assert terminal <= RESOLVED_WORDING.keys()


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


# --- the list_proposals tool -----------------------------------------------


async def test_list_proposals_reports_pending_and_recent(registry, make_ctx, approvals):
    ctx = make_ctx()
    pending_out = await call(
        registry,
        ctx,
        kind="sonos_say",
        payload={"text": "the garage door is still open"},
        reason="why",
        topic="#home",
    )
    done_out = await call(
        registry,
        ctx,
        kind="sonos_say",
        payload={"text": "the garage door is now closed"},
        reason="why",
        topic="#home",
    )
    await approvals.on_reaction(
        [p.slack_ts for p in approvals.all() if p.id == done_out["id"]][0], "x"
    )

    out = await call_list(registry, ctx)

    assert out["ok"] is True
    assert [p["id"] for p in out["pending"]] == [pending_out["id"]]
    assert out["pending"][0]["kind"] == "sonos_say"
    assert out["pending"][0]["payload"] == {"text": "the garage door is still open"}
    assert [p["id"] for p in out["recent"]] == [done_out["id"]]
    assert out["recent"][0]["status"] == "rejected"


async def test_list_proposals_is_empty_before_anything_is_proposed(registry, make_ctx):
    out = await call_list(registry, make_ctx())

    assert out == {"ok": True, "pending": [], "recent": []}


async def test_list_proposals_caps_recent_terminal_entries(
    registry, make_ctx, approvals, moving_clock
):
    from ai_brain.tools.propose import RECENT_TERMINAL_LIMIT

    ctx = make_ctx()
    for i in range(RECENT_TERMINAL_LIMIT + 5):
        # Ids are timestamped to the second and all() sorts by id -- advance
        # the clock so each proposal actually lands in a distinct, later
        # second, the way real cycles minutes apart always do.
        moving_clock.state["now"] = START + timedelta(seconds=i)
        out = await call(
            registry,
            ctx,
            kind="sonos_say",
            payload={"text": f"garage door check number {i}"},
            reason="why",
            topic="#home",
        )
        proposal = next(p for p in approvals.all() if p.id == out["id"])
        await approvals.on_reaction(proposal.slack_ts, "x")

    out = await call_list(registry, ctx)

    assert len(out["recent"]) == RECENT_TERMINAL_LIMIT
    # The oldest ones fell off, not the newest -- a reader wants what just
    # happened, not the earliest history.
    assert out["recent"][-1]["payload"] == {
        "text": f"garage door check number {RECENT_TERMINAL_LIMIT + 4}"
    }


async def test_list_proposals_is_refused_for_an_expert(registry, make_ctx):
    out = json.loads(
        await registry.dispatch(
            make_ctx("energy"), ToolCall(id="1", name="list_proposals", args={})
        )
    )

    assert "not allowed" in out["error"]


# --- rejection memory: proposal_target -------------------------------------


def test_proposal_target_uses_the_kind_specific_key():
    from ai_brain.approvals import proposal_target

    assert proposal_target("ha_todo_add", {"item": "buy filters"}) == "buy filters"
    assert proposal_target("docker_restart", {"container": "iot-fetcher"}) == "iot-fetcher"
    assert proposal_target("sonos_say", {"text": "hello"}) == "hello"


def test_proposal_target_falls_back_to_the_whole_payload_when_the_key_is_missing():
    from ai_brain.approvals import proposal_target

    assert proposal_target("ha_todo_add", {}) == "{}"


# --- rejection memory: proposal_target for ha_service -----------------------
#
# ha_service is not in TARGET_KEY -- the entity alone would treat
# "light.turn_off" and "light.turn_on" on the same entity as the same ask,
# which is exactly the over-blocking a code review of the first version of
# this caught. See proposal_target's own docstring.


def test_proposal_target_for_ha_service_keys_on_service_and_entity():
    from ai_brain.approvals import proposal_target

    target = proposal_target(
        "ha_service",
        {"service": "climate.set_temperature", "entity_id": "climate.living_room"},
    )
    assert target == "climate.set_temperature climate.living_room"


def test_proposal_target_for_ha_service_ignores_numeric_data():
    """A different setpoint is still "the same ask" for rejection purposes --
    only the service and entity distinguish one ha_service target from
    another, never a bare number like temperature or brightness_pct."""
    from ai_brain.approvals import proposal_target

    a = proposal_target(
        "ha_service",
        {
            "service": "climate.set_temperature",
            "entity_id": "climate.living_room",
            "data": {"temperature": 21},
        },
    )
    b = proposal_target(
        "ha_service",
        {
            "service": "climate.set_temperature",
            "entity_id": "climate.living_room",
            "data": {"temperature": 23},
        },
    )
    assert a == b == "climate.set_temperature climate.living_room"


def test_proposal_target_for_ha_service_keeps_non_numeric_data():
    """hvac_mode is not a bare setpoint -- "heat" and "off" on the same
    entity are genuinely different asks, so it stays part of the target."""
    from ai_brain.approvals import proposal_target

    heat = proposal_target(
        "ha_service",
        {
            "service": "climate.set_hvac_mode",
            "entity_id": "climate.living_room",
            "data": {"hvac_mode": "heat"},
        },
    )
    off = proposal_target(
        "ha_service",
        {
            "service": "climate.set_hvac_mode",
            "entity_id": "climate.living_room",
            "data": {"hvac_mode": "off"},
        },
    )
    assert heat != off
    assert "heat" in heat
    assert "off" in off


def test_proposal_target_for_ha_service_with_no_data():
    from ai_brain.approvals import proposal_target

    target = proposal_target(
        "ha_service", {"service": "light.turn_off", "entity_id": "light.kitchen"}
    )
    assert target == "light.turn_off light.kitchen"


# --- rejection memory: rejected_groups --------------------------------------


async def test_rejected_groups_is_empty_with_no_rejections(approvals):
    from ai_brain.approvals import rejected_groups

    await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    assert rejected_groups(approvals.all()) == []


async def test_rejected_groups_groups_by_kind_target_and_topic(approvals):
    from ai_brain.approvals import rejected_groups

    p1 = await approvals.propose(
        "ha_service",
        {"service": "climate.set_temperature", "entity_id": "climate.living_room"},
        "too cold",
        "climate",
    )
    await approvals.on_reaction(p1.slack_ts, "x")
    p2 = await approvals.propose(
        "ha_service",
        {"service": "climate.set_temperature", "entity_id": "climate.living_room"},
        "still cold",
        "climate",
    )
    await approvals.on_reaction(p2.slack_ts, "x")

    [group] = rejected_groups(approvals.all())
    assert group.kind == "ha_service"
    assert group.target == "climate.set_temperature climate.living_room"
    assert group.topic == "climate"
    assert group.count == 2
    assert group.last_at == p2.created


async def test_rejected_groups_ignores_pending_and_approved_proposals(approvals, executors):
    from ai_brain.approvals import rejected_groups

    approved = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(approved.slack_ts, "white_check_mark")
    await approvals.propose("sonos_say", {"text": "still pending"}, "why", "#home")

    assert rejected_groups(approvals.all()) == []


async def test_rejected_groups_different_targets_stay_separate(approvals):
    from ai_brain.approvals import rejected_groups

    a = await approvals.propose("ha_todo_add", {"item": "milk"}, "why", "#home")
    await approvals.on_reaction(a.slack_ts, "x")
    b = await approvals.propose("ha_todo_add", {"item": "eggs"}, "why", "#home")
    await approvals.on_reaction(b.slack_ts, "x")

    groups = rejected_groups(approvals.all())
    assert {g.target for g in groups} == {"milk", "eggs"}
    assert all(g.count == 1 for g in groups)


async def test_rejected_groups_carries_the_latest_rejection_reason(approvals, brain_dir):
    from ai_brain.approvals import rejected_groups

    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(p.slack_ts, "x")

    [group] = rejected_groups(approvals.all())
    # A bare reject carries no result text -- resolved_text's "❌ Rejected."
    # wording is display-only, so an empty reason is the honest answer here.
    assert group.reason == ""


async def test_rejected_groups_respects_the_since_cutoff(approvals, moving_clock):
    from datetime import timedelta

    from ai_brain.approvals import rejected_groups

    p = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(p.slack_ts, "x")

    cutoff = moving_clock() + timedelta(days=1)
    assert rejected_groups(approvals.all(), since=cutoff) == []
    assert len(rejected_groups(approvals.all(), since=moving_clock() - timedelta(days=1))) == 1


async def test_rejected_groups_keeps_a_group_re_rejected_after_an_old_first_miss(
    approvals, moving_clock
):
    """An old rejection alone would fall outside since -- but a second, recent
    rejection of the same target means the group is still current."""
    from datetime import timedelta

    from ai_brain.approvals import rejected_groups

    p1 = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(p1.slack_ts, "x")

    moving_clock.state["now"] += timedelta(days=60)
    p2 = await approvals.propose("sonos_say", {"text": "hi"}, "why", "#home")
    await approvals.on_reaction(p2.slack_ts, "x")

    cutoff = moving_clock() - timedelta(days=30)
    [group] = rejected_groups(approvals.all(), since=cutoff)
    assert group.count == 2
    assert group.last_at == p2.created


async def test_rejected_groups_sorts_newest_first(approvals, moving_clock):
    from datetime import timedelta

    from ai_brain.approvals import rejected_groups

    older = await approvals.propose("ha_todo_add", {"item": "milk"}, "why", "#home")
    await approvals.on_reaction(older.slack_ts, "x")

    moving_clock.state["now"] += timedelta(minutes=5)
    newer = await approvals.propose("ha_todo_add", {"item": "eggs"}, "why", "#home")
    await approvals.on_reaction(newer.slack_ts, "x")

    groups = rejected_groups(approvals.all())
    assert [g.target for g in groups] == ["eggs", "milk"]


# --- rejection memory: rejection_memory_section -----------------------------


def test_rejection_memory_section_is_empty_with_no_groups():
    from ai_brain.approvals import rejection_memory_section

    assert rejection_memory_section([]) == ""


def test_rejection_memory_section_names_kind_target_count_and_date():
    from ai_brain.approvals import RejectionGroup, rejection_memory_section

    group = RejectionGroup(
        kind="ha_todo_add",
        target="buy filters",
        topic="house-ops",
        count=2,
        last_at="2026-09-20T10:00:00Z",
        reason="",
    )
    text = rejection_memory_section([group])
    assert text.startswith("# Filip has said no to")
    assert "ha_todo_add" in text
    assert "buy filters" in text
    assert "house-ops" in text
    assert "2x" in text
    assert "2026-09-20T10:00:00Z" in text


def test_rejection_memory_section_includes_the_reason_when_present():
    from ai_brain.approvals import RejectionGroup, rejection_memory_section

    group = RejectionGroup(
        kind="sonos_say",
        target="test message",
        topic="#home",
        count=1,
        last_at="2026-09-20T10:00:00Z",
        reason="too loud at night",
    )
    assert "too loud at night" in rejection_memory_section([group])


def test_rejection_memory_section_caps_the_number_of_entries():
    from ai_brain.approvals import REJECTION_MEMORY_MAX_ENTRIES, RejectionGroup, rejection_memory_section

    groups = [
        RejectionGroup(
            kind="ha_todo_add",
            target=f"item-{i}",
            topic="house-ops",
            count=1,
            last_at="2026-09-20T10:00:00Z",
            reason="",
        )
        for i in range(REJECTION_MEMORY_MAX_ENTRIES + 5)
    ]
    text = rejection_memory_section(groups)
    assert text.count("item-") == REJECTION_MEMORY_MAX_ENTRIES


def test_rejection_memory_section_caps_total_length_by_dropping_whole_lines():
    from ai_brain.approvals import (
        REJECTION_MEMORY_MAX_CHARS,
        REJECTION_MEMORY_MAX_ENTRIES,
        RejectionGroup,
        rejection_memory_section,
    )

    # REJECTION_MEMORY_MAX_ENTRIES caps the entry count well below what would
    # ever hit the char cap on its own, so a handful of long-but-plausible
    # targets (not one absurd 5000-char one) is what actually exercises it.
    groups = [
        RejectionGroup(
            kind="ha_todo_add",
            target=f"a fairly long todo item description number {i} " * 3,
            topic="house-ops",
            count=1,
            last_at="2026-09-20T10:00:00Z",
            reason="",
        )
        for i in range(REJECTION_MEMORY_MAX_ENTRIES)
    ]
    text = rejection_memory_section(groups)

    assert len(text) <= REJECTION_MEMORY_MAX_CHARS
    # No line is a truncated fragment -- every kept line still names its own
    # kind/topic/count in full, and the drop is called out on its own line.
    for line in text.splitlines():
        assert line.startswith(("# Filip has said no to", "- ha_todo_add", "…and "))
    assert "…and " in text and " more" in text


def test_rejection_memory_section_never_drops_below_the_heading():
    """However long a single entry is, the heading itself always survives."""
    from ai_brain.approvals import RejectionGroup, rejection_memory_section

    group = RejectionGroup(
        kind="sonos_say",
        target="x" * 5000,
        topic="#home",
        count=1,
        last_at="2026-09-20T10:00:00Z",
        reason="",
    )
    text = rejection_memory_section([group])
    assert text.splitlines()[0] == "# Filip has said no to"


# --- rejection memory: the propose guard ------------------------------------


async def test_propose_tool_refuses_a_target_rejected_recently(registry, make_ctx, approvals):
    ctx = make_ctx()
    first = await call(
        registry,
        ctx,
        kind="ha_service",
        payload={"service": "climate.set_temperature", "entity_id": "climate.living_room"},
        reason="too cold",
        topic="climate",
    )
    proposal = next(p for p in approvals.all() if p.id == first["id"])
    await approvals.on_reaction(proposal.slack_ts, "x")

    out = await call(
        registry,
        ctx,
        kind="ha_service",
        payload={"service": "climate.set_temperature", "entity_id": "climate.living_room"},
        reason="still cold",
        topic="climate",
    )

    assert "error" in out
    assert "already rejected" in out["error"]
    assert "climate.living_room" in out["error"]


async def test_propose_tool_still_allows_a_different_target(registry, make_ctx, approvals):
    ctx = make_ctx()
    first = await call(
        registry,
        ctx,
        kind="ha_service",
        payload={"service": "climate.set_temperature", "entity_id": "climate.living_room"},
        reason="too cold",
        topic="climate",
    )
    proposal = next(p for p in approvals.all() if p.id == first["id"])
    await approvals.on_reaction(proposal.slack_ts, "x")

    out = await call(
        registry,
        ctx,
        kind="ha_service",
        payload={"service": "climate.set_temperature", "entity_id": "climate.bedroom"},
        reason="too cold",
        topic="climate",
    )

    assert out["ok"] is True


async def test_propose_tool_allows_turn_on_after_a_rejected_turn_off(registry, make_ctx, approvals):
    """The entity alone is too coarse a target for ha_service -- rejecting
    'turn the light off' must not also block 'turn the light on'."""
    ctx = make_ctx()
    first = await call(
        registry,
        ctx,
        kind="ha_service",
        payload={"service": "light.turn_off", "entity_id": "light.kitchen"},
        reason="testing",
        topic="lights",
    )
    proposal = next(p for p in approvals.all() if p.id == first["id"])
    await approvals.on_reaction(proposal.slack_ts, "x")

    out = await call(
        registry,
        ctx,
        kind="ha_service",
        payload={"service": "light.turn_on", "entity_id": "light.kitchen"},
        reason="testing",
        topic="lights",
    )

    assert out["ok"] is True


async def test_propose_tool_refuses_a_different_setpoint_on_a_rejected_climate_entity(
    registry, make_ctx, approvals
):
    """A numeric argument (the setpoint) does not distinguish one ha_service
    target from another -- a different temperature on the same rejected
    service+entity is still the observed repeat pattern and must be refused."""
    ctx = make_ctx()
    first = await call(
        registry,
        ctx,
        kind="ha_service",
        payload={
            "service": "climate.set_temperature",
            "entity_id": "climate.living_room",
            "data": {"temperature": 21},
        },
        reason="too cold",
        topic="climate",
    )
    proposal = next(p for p in approvals.all() if p.id == first["id"])
    await approvals.on_reaction(proposal.slack_ts, "x")

    out = await call(
        registry,
        ctx,
        kind="ha_service",
        payload={
            "service": "climate.set_temperature",
            "entity_id": "climate.living_room",
            "data": {"temperature": 23},
        },
        reason="still cold",
        topic="climate",
    )

    assert "error" in out
    assert "already rejected" in out["error"]


async def test_propose_tool_still_allows_the_same_target_after_approval(
    registry, make_ctx, approvals
):
    """Only a rejection blocks a repeat -- an approved proposal is not "no"."""
    ctx = make_ctx()
    first = await call(
        registry,
        ctx,
        kind="ha_todo_add",
        payload={"item": "buy filters"},
        reason="low stock",
        topic="house-ops",
    )
    proposal = next(p for p in approvals.all() if p.id == first["id"])
    await approvals.on_reaction(proposal.slack_ts, "white_check_mark")

    out = await call(
        registry,
        ctx,
        kind="ha_todo_add",
        payload={"item": "buy filters"},
        reason="still low",
        topic="house-ops",
    )

    assert out["ok"] is True


async def test_propose_tool_stops_blocking_after_rejection_memory_days(
    registry, make_ctx, approvals, moving_clock
):
    from datetime import timedelta

    from ai_brain.config import load_settings

    ctx = make_ctx()
    ctx.settings = load_settings({"REJECTION_MEMORY_DAYS": "30"})
    first = await call(
        registry,
        ctx,
        kind="ha_todo_add",
        payload={"item": "buy filters"},
        reason="low stock",
        topic="house-ops",
    )
    proposal = next(p for p in approvals.all() if p.id == first["id"])
    await approvals.on_reaction(proposal.slack_ts, "x")

    moving_clock.state["now"] += timedelta(days=31)

    out = await call(
        registry,
        ctx,
        kind="ha_todo_add",
        payload={"item": "buy filters"},
        reason="still low",
        topic="house-ops",
    )

    assert out["ok"] is True


async def test_propose_tool_guard_uses_the_moving_clock_not_wall_time(
    registry, make_ctx, approvals, moving_clock
):
    """The guard must read the same clock approvals itself uses, not
    datetime.now(), or a frozen-clock test suite would never trip it."""
    ctx = make_ctx()
    first = await call(
        registry,
        ctx,
        kind="ha_todo_add",
        payload={"item": "buy filters"},
        reason="low stock",
        topic="house-ops",
    )
    proposal = next(p for p in approvals.all() if p.id == first["id"])
    await approvals.on_reaction(proposal.slack_ts, "x")

    out = await call(
        registry,
        ctx,
        kind="ha_todo_add",
        payload={"item": "buy filters"},
        reason="still low",
        topic="house-ops",
    )

    assert "error" in out
