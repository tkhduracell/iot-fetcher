import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ai_brain.approvals import Proposal
from ai_brain.config import load_settings
from ai_brain.llm import ToolCall
from ai_brain.slack_io import SlackIn, SlackOut, SlackRateCapped, quiet_until, start_slack
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.slack_tools import register_slack_tools

START = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
USER = "U-filip"


def make_proposal(status: str = "executed", result: str = "", **overrides) -> Proposal:
    """A resolved Proposal, exactly as approvals.on_button/on_reaction return one."""
    fields = {
        "id": "20260906T100000-sonos_say-abcd",
        "kind": "sonos_say",
        "payload": {"text": "hej"},
        "reason": "testing",
        "topic": "pool",
        "created": "2026-09-06T10:00:00Z",
        "status": status,
        "result": result,
    }
    fields.update(overrides)
    return Proposal(**fields)


class SlackError(Exception):
    """Stands in for slack_sdk.errors.SlackApiError without the response object."""


class FakeClient:
    """Records every call and answers with the shapes the real API returns.

    ``fail_methods`` names methods that should raise instead of answering, so a
    test can make exactly ``chat.postMessage`` fail while ``conversations.open``
    keeps working.
    """

    def __init__(self, fail_methods: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_methods = fail_methods
        self.next_ts = 0

    def _record(self, method: str, kwargs: dict) -> None:
        self.calls.append((method, kwargs))
        if method in self.fail_methods:
            raise SlackError(f"{method} is unhappy")

    def methods(self, name: str) -> list[dict]:
        return [kwargs for method, kwargs in self.calls if method == name]

    async def conversations_open(self, **kwargs):
        self._record("conversations_open", kwargs)
        return {"ok": True, "channel": {"id": "D1"}}

    async def chat_postMessage(self, **kwargs):  # mirrors the SDK method name
        self._record("chat_postMessage", kwargs)
        self.next_ts += 1
        return {"ok": True, "ts": f"{self.next_ts}.1"}

    async def reactions_add(self, **kwargs):
        self._record("reactions_add", kwargs)
        return {"ok": True}

    async def reactions_remove(self, **kwargs):
        self._record("reactions_remove", kwargs)
        return {"ok": True}

    async def chat_update(self, **kwargs):
        self._record("chat_update", kwargs)
        return {"ok": True}

    async def api_call(self, api_method: str, **kwargs):
        self._record(api_method, kwargs.get("json", {}))
        return {"ok": True}


@pytest.fixture
def moving_clock():
    state = {"now": START}

    def _now():
        return state["now"]

    _now.state = state
    return _now


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def out(client, brain_dir, moving_clock):
    return SlackOut(client, USER, brain_dir, moving_clock)


async def _no_sleep(_seconds: float) -> None:
    return None


# -- posting -----------------------------------------------------------


async def test_first_post_opens_dm_and_creates_a_top_level_thread(out, client, brain_dir):
    ts = await out.post("pool", "the pool is cold")

    assert ts == "1.1"
    assert client.methods("conversations_open") == [{"users": USER}]
    assert client.methods("chat_postMessage") == [
        {"channel": "D1", "text": "the pool is cold", "thread_ts": None}
    ]
    # No Agents-session chrome call is made: a topic's first post is an
    # ordinary top-level message so Slack's own notification deep-links to it.
    assert client.methods("agents.sessions.rename") == []
    assert client.methods("agents.sessions.setStatus") == []

    sessions = json.loads((brain_dir.root / "sessions.json").read_text())
    assert sessions == {
        "pool": {
            "thread_ts": "1.1",
            "channel": "D1",
            "last_ts": "1.1",
            "last_at": START.isoformat(timespec="seconds"),
        }
    }


async def test_second_post_threads_under_the_same_topic(out, client):
    await out.post("pool", "first")
    await out.post("pool", "second")

    assert client.methods("chat_postMessage") == [
        {"channel": "D1", "text": "first", "thread_ts": None},
        {"channel": "D1", "text": "second", "thread_ts": "1.1"},
    ]
    # The DM is opened once; only the reply threads under the first post.
    assert len(client.methods("conversations_open")) == 1


async def test_a_second_topic_gets_its_own_thread(out, client, brain_dir):
    await out.post("pool", "first")
    await out.post("energy", "hello")

    sessions = json.loads((brain_dir.root / "sessions.json").read_text())
    assert sessions["pool"]["thread_ts"] == "1.1"
    assert sessions["energy"]["thread_ts"] == "2.1"


async def test_sessions_survive_a_restart(client, brain_dir, moving_clock):
    first = SlackOut(client, USER, brain_dir, moving_clock)
    await first.post("pool", "before restart")

    second = SlackOut(client, USER, brain_dir, moving_clock)
    await second.post("pool", "after restart")

    assert client.methods("chat_postMessage")[-1]["thread_ts"] == "1.1"


# -- rate cap ----------------------------------------------------------


async def test_rate_cap_raises_after_the_cap_and_reopens_an_hour_later(
    client, brain_dir, moving_clock
):
    out = SlackOut(client, USER, brain_dir, moving_clock, max_per_hour=3)
    for i in range(3):
        await out.post("pool", f"msg {i}")

    with pytest.raises(SlackRateCapped):
        await out.post("pool", "one too many")
    assert len(client.methods("chat_postMessage")) == 3

    # The window slides: an hour after the first post there is room again.
    moving_clock.state["now"] = START + timedelta(hours=1, seconds=1)
    await out.post("pool", "later")
    assert len(client.methods("chat_postMessage")) == 4


async def test_rate_cap_counts_across_topics(client, brain_dir, moving_clock):
    out = SlackOut(client, USER, brain_dir, moving_clock, max_per_hour=2)
    await out.post("pool", "a")
    await out.post("energy", "b")

    with pytest.raises(SlackRateCapped):
        await out.post("weather", "c")


# -- retries and the outbox queue --------------------------------------


async def test_post_retries_three_times_then_queues_to_the_outbox(brain_dir, moving_clock):
    client = FakeClient(fail_methods=frozenset({"chat_postMessage"}))
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    out = SlackOut(client, USER, brain_dir, moving_clock, sleep=sleep)
    assert await out.post("pool", "nobody hears this") == "queued"

    assert len(client.methods("chat_postMessage")) == 3
    assert slept == [0.5, 1.0]

    queued = sorted((brain_dir.outbox_dir / "slack").glob("*.json"))
    assert len(queued) == 1
    assert json.loads(queued[0].read_text()) == {"topic": "pool", "text": "nobody hears this"}


async def test_a_transient_failure_is_retried_and_then_succeeds(brain_dir, moving_clock):
    """Two failures inside the retry budget still land the message."""

    class FlakyClient(FakeClient):
        def __init__(self, failures: int) -> None:
            super().__init__()
            self.remaining = failures

        async def chat_postMessage(self, **kwargs):  # mirrors the SDK method name
            if self.remaining:
                self.remaining -= 1
                self.calls.append(("chat_postMessage", kwargs))
                raise SlackError("flaky")
            return await super().chat_postMessage(**kwargs)

    client = FlakyClient(failures=2)
    out = SlackOut(client, USER, brain_dir, moving_clock, sleep=_no_sleep)

    assert await out.post("pool", "eventually lands") == "1.1"
    assert len(client.methods("chat_postMessage")) == 3
    assert not list((brain_dir.outbox_dir / "slack").glob("*.json"))


async def test_flush_queue_resends_oldest_first_and_deletes_on_success(brain_dir, moving_clock):
    failing = FakeClient(fail_methods=frozenset({"chat_postMessage"}))
    out = SlackOut(failing, USER, brain_dir, moving_clock, sleep=_no_sleep)
    await out.post("pool", "first queued")
    moving_clock.state["now"] = START + timedelta(minutes=1)
    await out.post("energy", "second queued")
    assert len(list((brain_dir.outbox_dir / "slack").glob("*.json"))) == 2

    working = FakeClient()
    flusher = SlackOut(working, USER, brain_dir, moving_clock, sleep=_no_sleep)
    assert await flusher.flush_queue() == 2

    assert [kwargs["text"] for kwargs in working.methods("chat_postMessage")] == [
        "first queued",
        "second queued",
    ]
    assert not list((brain_dir.outbox_dir / "slack").glob("*.json"))


async def test_flush_queue_keeps_files_it_could_not_send(brain_dir, moving_clock):
    failing = FakeClient(fail_methods=frozenset({"chat_postMessage"}))
    out = SlackOut(failing, USER, brain_dir, moving_clock, sleep=_no_sleep)
    await out.post("pool", "still stuck")

    assert await out.flush_queue() == 0
    assert len(list((brain_dir.outbox_dir / "slack").glob("*.json"))) == 1


async def test_flush_queue_is_zero_when_nothing_is_queued(out):
    assert await out.flush_queue() == 0


async def test_flush_queue_hitting_the_rate_cap_loses_nothing(client, brain_dir, moving_clock):
    """The queue file is the only copy, so a capped flush must leave it alone."""
    queue = brain_dir.outbox_dir / "slack"
    queue.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (queue / f"2026090{i}.json").write_text(json.dumps({"topic": f"t{i}", "text": f"m{i}"}))

    out = SlackOut(client, USER, brain_dir, moving_clock, max_per_hour=1)
    assert await out.flush_queue() == 1

    # One sent and deleted; the two it had no budget for are still on disk.
    assert sorted(p.name for p in queue.glob("*.json")) == ["20260901.json", "20260902.json"]
    assert len(client.methods("chat_postMessage")) == 1


async def test_flush_queue_discards_a_corrupt_file(out, brain_dir):
    queue = brain_dir.outbox_dir / "slack"
    queue.mkdir(parents=True, exist_ok=True)
    (queue / "broken.json").write_text("{not json")

    assert await out.flush_queue() == 0
    assert not list(queue.glob("*.json"))


# -- the pending spinner -------------------------------------------------


async def test_mark_pending_reacts_loading_on_the_message(out, client):
    await out.mark_pending("pool", "D1", "100.1")

    assert client.methods("reactions_add") == [
        {"channel": "D1", "timestamp": "100.1", "name": "loading"}
    ]


async def test_a_reply_clears_the_topics_pending_spinner(out, client):
    await out.mark_pending("pool", "D1", "100.1")
    await out.post("pool", "it's warm again")

    assert client.methods("reactions_remove") == [
        {"channel": "D1", "timestamp": "100.1", "name": "loading"}
    ]
    assert client.methods("reactions_add") == [
        {"channel": "D1", "timestamp": "100.1", "name": "loading"}
    ]


async def test_a_reply_to_an_unrelated_topic_leaves_the_spinner(out, client):
    await out.mark_pending("pool", "D1", "100.1")
    await out.post("energy", "hello")

    assert client.methods("reactions_remove") == []


async def test_watchdog_leaves_a_fresh_spinner_alone(out, client, moving_clock):
    await out.mark_pending("pool", "D1", "100.1")
    moving_clock.state["now"] = START + timedelta(minutes=1)

    await out.check_watchdog()

    assert client.methods("reactions_remove") == []


async def test_mark_unanswered_swaps_the_spinner_at_once(out, client):
    await out.mark_pending("pool", "D1", "100.1")
    client.calls.clear()

    await out.mark_unanswered("pool")
    await out.mark_unanswered("pool")  # once only

    assert client.methods("reactions_remove") == [
        {"channel": "D1", "timestamp": "100.1", "name": "loading"}
    ]
    assert client.methods("reactions_add") == [
        {"channel": "D1", "timestamp": "100.1", "name": "red_circle"}
    ]


async def test_mark_unanswered_is_a_no_op_without_a_spinner(out, client):
    await out.mark_unanswered("pool")

    assert client.calls == []


async def test_watchdog_swaps_a_stale_spinner_for_a_red_circle(out, client, moving_clock):
    await out.mark_pending("pool", "D1", "100.1")
    moving_clock.state["now"] = START + timedelta(minutes=61)

    await out.check_watchdog()

    assert client.methods("reactions_remove") == [
        {"channel": "D1", "timestamp": "100.1", "name": "loading"}
    ]
    assert client.methods("reactions_add")[-1] == {
        "channel": "D1",
        "timestamp": "100.1",
        "name": "red_circle",
    }


async def test_watchdog_only_fires_once_per_topic(out, client, moving_clock):
    await out.mark_pending("pool", "D1", "100.1")
    moving_clock.state["now"] = START + timedelta(minutes=61)
    await out.check_watchdog()
    client.calls.clear()

    await out.check_watchdog()

    assert client.methods("reactions_remove") == []
    assert client.methods("reactions_add") == []


async def test_a_late_reply_after_the_watchdog_fired_posts_normally(out, client, moving_clock):
    await out.mark_pending("pool", "D1", "100.1")
    moving_clock.state["now"] = START + timedelta(minutes=61)
    await out.check_watchdog()
    client.calls.clear()

    ts = await out.post("pool", "sorted now")

    assert ts != "queued"
    assert client.methods("reactions_remove") == []


async def test_a_second_mark_pending_on_the_same_topic_replaces_the_first(out, client):
    await out.mark_pending("pool", "D1", "100.1")
    await out.mark_pending("pool", "D1", "101.1")
    await out.post("pool", "reply")

    assert client.methods("reactions_remove") == [
        {"channel": "D1", "timestamp": "101.1", "name": "loading"}
    ]


# -- inbound -----------------------------------------------------------


class FakeApprovals:
    def __init__(self, resolves: object = None) -> None:
        self.reactions: list[tuple[str, str]] = []
        self.buttons: list[tuple[str, str]] = []
        self.resolves = resolves

    async def on_reaction(self, slack_ts: str, emoji: str):
        self.reactions.append((slack_ts, emoji))
        return self.resolves

    async def on_button(self, slack_ts: str, action: str):
        self.buttons.append((slack_ts, action))
        return self.resolves


class FakeApp:
    """Collects the handlers ``register`` attaches, so tests can call them."""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.actions: dict[str, object] = {}
        self.client = FakeClient()

    def event(self, name: str):
        def _decorate(fn):
            self.handlers[name] = fn
            return fn

        return _decorate

    def action(self, action_id: str):
        def _decorate(fn):
            self.actions[action_id] = fn
            return fn

        return _decorate


@pytest.fixture
def approvals():
    return FakeApprovals()


@pytest.fixture
def woken():
    return []


@pytest.fixture
def app():
    return FakeApp()


@pytest.fixture
def slack_in(app, brain_dir, approvals, out, woken):
    listener = SlackIn(app, brain_dir, approvals, out, woken.append, USER)
    listener.register()
    return listener


async def _ack() -> None:
    """Stands in for Bolt's ack callable."""


async def test_dm_from_filip_becomes_an_inbox_note_and_wakes_the_brain(
    slack_in, app, brain_dir, woken
):
    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "is the spa on?"}, _ack
    )

    notes = brain_dir.unread_notes()
    assert [(n.sender, n.body) for n in notes] == [("filip", "is the spa on?")]
    assert woken == ["brain"]


async def test_a_dm_with_a_ts_marks_the_topic_pending(slack_in, app, out, client, brain_dir):
    await app.handlers["message"](
        {
            "channel_type": "im",
            "user": USER,
            "text": "is the spa on?",
            "ts": "500.1",
            "channel": "D1",
            "thread_ts": "500.1",
        },
        _ack,
    )

    assert client.methods("reactions_add") == [
        {"channel": "D1", "timestamp": "500.1", "name": "loading"}
    ]


async def test_a_dm_without_a_ts_is_not_marked_pending(slack_in, app, client, brain_dir):
    """Existing tests build events without ts/channel; that must stay harmless."""
    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "is the spa on?"}, _ack
    )

    assert client.methods("reactions_add") == []


async def test_a_dm_inside_a_topic_thread_is_tagged_with_the_topic(slack_in, app, out, brain_dir):
    await out.post("pool", "the pool is cold")

    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "turn it up", "thread_ts": "1.1"}, _ack
    )

    assert brain_dir.unread_notes()[0].body == "topic: pool\nturn it up"


async def test_a_dm_in_an_unknown_thread_is_tagged_as_chat(slack_in, app, brain_dir):
    """Unknown means the assistant thread: in the agent UI there is no other kind."""
    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "hi", "thread_ts": "9.9"}, _ack
    )

    assert brain_dir.unread_notes()[0].body == "topic: chat\nhi"


@pytest.mark.parametrize(
    "event",
    [
        pytest.param({"channel_type": "im", "user": "U-someone-else", "text": "hi"}, id="other"),
        pytest.param({"channel_type": "channel", "user": USER, "text": "hi"}, id="not-a-dm"),
        pytest.param(
            {"channel_type": "im", "user": USER, "text": "hi", "bot_id": "B1"}, id="a-bot"
        ),
        pytest.param(
            {"channel_type": "im", "user": USER, "text": "hi", "subtype": "message_changed"},
            id="an-edit",
        ),
        pytest.param({"channel_type": "im", "user": USER, "text": "   "}, id="empty"),
    ],
)
async def test_messages_that_are_not_filip_talking_are_ignored(
    slack_in, app, brain_dir, woken, event
):
    await app.handlers["message"](event, _ack)

    assert brain_dir.unread_notes() == []
    assert woken == []


async def test_a_dropped_message_says_why_in_the_log(slack_in, app, caplog):
    """A DM that goes nowhere must leave a trace, or it looks like a dead socket."""
    with caplog.at_level(logging.INFO, logger="ai_brain.slack_io"):
        await app.handlers["message"](
            {"channel_type": "channel", "user": "U-someone-else", "text": "secret", "bot_id": "B1"},
            _ack,
        )

    line = "\n".join(caplog.messages)
    assert "[slack] dropped message" in line
    assert "channel_type=channel" in line
    assert "user=U-someone-else" in line
    assert f"expected_user={USER}" in line
    assert "bot=B1" in line
    assert "secret" not in line


async def test_an_accepted_message_is_logged_without_its_text(slack_in, app, out, caplog):
    await out.post("pool", "the pool is cold")

    with caplog.at_level(logging.INFO, logger="ai_brain.slack_io"):
        await app.handlers["message"](
            {"channel_type": "im", "user": USER, "text": "turn it up", "thread_ts": "1.1"}, _ack
        )

    line = "\n".join(caplog.messages)
    assert "[slack] note from filip (10 chars, topic=pool)" in line
    assert "turn it up" not in line


async def test_the_assistant_pane_events_are_registered_and_acknowledged(slack_in, app):
    for name in ("assistant_thread_started", "assistant_thread_context_changed"):
        assert name in app.handlers
        await app.handlers[name]({"type": name}, _ack)


async def test_a_reaction_from_filip_is_routed_to_approvals(slack_in, app, approvals):
    await app.handlers["reaction_added"](
        {"user": USER, "reaction": "white_check_mark", "item": {"ts": "7.7"}}, _ack
    )

    assert approvals.reactions == [("7.7", "white_check_mark")]


async def test_a_reaction_from_anyone_else_is_ignored(slack_in, app, approvals):
    await app.handlers["reaction_added"](
        {"user": "U-stranger", "reaction": "white_check_mark", "item": {"ts": "7.7"}}, _ack
    )

    assert approvals.reactions == []


async def test_an_approve_button_click_is_routed_to_approvals(slack_in, app, approvals):
    from ai_brain.approvals import APPROVE_ACTION

    assert APPROVE_ACTION in app.actions

    await app.actions[APPROVE_ACTION](
        {
            "user": {"id": USER},
            "actions": [{"action_id": APPROVE_ACTION, "value": "some-proposal-id"}],
            "message": {"ts": "7.7"},
        },
        _ack,
    )

    assert approvals.buttons == [("7.7", APPROVE_ACTION)]


async def test_a_reject_button_click_is_routed_to_approvals(slack_in, app, approvals):
    from ai_brain.approvals import REJECT_ACTION

    await app.actions[REJECT_ACTION](
        {
            "user": {"id": USER},
            "actions": [{"action_id": REJECT_ACTION, "value": "some-proposal-id"}],
            "message": {"ts": "7.7"},
        },
        _ack,
    )

    assert approvals.buttons == [("7.7", REJECT_ACTION)]


async def test_a_button_click_from_anyone_else_is_ignored(slack_in, app, approvals):
    from ai_brain.approvals import APPROVE_ACTION

    await app.actions[APPROVE_ACTION](
        {
            "user": {"id": "U-stranger"},
            "actions": [{"action_id": APPROVE_ACTION, "value": "some-proposal-id"}],
            "message": {"ts": "7.7"},
        },
        _ack,
    )

    assert approvals.buttons == []


async def test_a_button_payload_with_no_actions_is_ignored(slack_in, app, approvals):
    from ai_brain.approvals import APPROVE_ACTION

    await app.actions[APPROVE_ACTION](
        {"user": {"id": USER}, "actions": [], "message": {"ts": "7.7"}}, _ack
    )

    assert approvals.buttons == []


# -- resolving the approval message --------------------------------------


async def test_an_approved_button_click_replaces_the_buttons_with_a_verdict(
    app, brain_dir, out, client, woken
):
    from ai_brain.approvals import APPROVE_ACTION

    proposal = make_proposal(status="executed", result="ok")
    listener = SlackIn(app, brain_dir, FakeApprovals(resolves=proposal), out, woken.append, USER)
    listener.register()

    await app.actions[APPROVE_ACTION](
        {
            "user": {"id": USER},
            "actions": [{"action_id": APPROVE_ACTION, "value": proposal.id}],
            "message": {"ts": "7.7"},
            "channel": {"id": "D1"},
        },
        _ack,
    )

    [update] = client.methods("chat_update")
    assert update["channel"] == "D1"
    assert update["ts"] == "7.7"
    assert "✅ Approved and run." in update["text"]
    assert update["blocks"] == [{"type": "section", "text": {"type": "mrkdwn", "text": update["text"]}}]
    # No buttons block survives -- there is exactly one block, the section.
    assert len(update["blocks"]) == 1


async def test_a_rejected_button_click_replaces_the_buttons_with_a_verdict(
    app, brain_dir, out, client, woken
):
    from ai_brain.approvals import REJECT_ACTION

    proposal = make_proposal(status="rejected")
    listener = SlackIn(app, brain_dir, FakeApprovals(resolves=proposal), out, woken.append, USER)
    listener.register()

    await app.actions[REJECT_ACTION](
        {
            "user": {"id": USER},
            "actions": [{"action_id": REJECT_ACTION, "value": proposal.id}],
            "message": {"ts": "7.7"},
            "channel": {"id": "D1"},
        },
        _ack,
    )

    [update] = client.methods("chat_update")
    assert "❌ Rejected." in update["text"]


async def test_an_ignored_button_click_leaves_the_message_alone(
    slack_in, app, client, approvals
):
    """approvals.resolves defaults to None -- an already-resolved or unknown
    proposal id -- and that must not touch the message at all."""
    from ai_brain.approvals import APPROVE_ACTION

    await app.actions[APPROVE_ACTION](
        {
            "user": {"id": USER},
            "actions": [{"action_id": APPROVE_ACTION, "value": "some-proposal-id"}],
            "message": {"ts": "7.7"},
            "channel": {"id": "D1"},
        },
        _ack,
    )

    assert client.methods("chat_update") == []


async def test_an_approving_reaction_replaces_the_buttons_with_a_verdict(
    app, brain_dir, out, client, woken
):
    proposal = make_proposal(status="executed")
    listener = SlackIn(app, brain_dir, FakeApprovals(resolves=proposal), out, woken.append, USER)
    listener.register()

    await app.handlers["reaction_added"](
        {
            "user": USER,
            "reaction": "white_check_mark",
            "item": {"ts": "7.7", "channel": "D1"},
        },
        _ack,
    )

    [update] = client.methods("chat_update")
    assert update["channel"] == "D1"
    assert update["ts"] == "7.7"


async def test_a_failed_execution_shows_the_result_in_the_verdict(
    app, brain_dir, out, client, woken
):
    proposal = make_proposal(status="failed", result="boom: connection refused")
    listener = SlackIn(app, brain_dir, FakeApprovals(resolves=proposal), out, woken.append, USER)
    listener.register()

    await app.handlers["reaction_added"](
        {"user": USER, "reaction": "white_check_mark", "item": {"ts": "7.7", "channel": "D1"}},
        _ack,
    )

    [update] = client.methods("chat_update")
    assert "⚠️" in update["text"]
    assert "boom: connection refused" in update["text"]


async def test_stopping_a_session_tells_the_brain(slack_in, app, out, brain_dir, woken):
    await out.post("pool", "the pool is cold")

    await app.handlers["agent_session_stopped"]({"thread_ts": "1.1"}, _ack)

    assert brain_dir.unread_notes()[0].body == "Filip stopped pool"
    assert woken == ["brain"]


async def test_a_stop_event_nests_the_thread_ts_under_session(slack_in, app, out, brain_dir):
    await out.post("pool", "the pool is cold")

    await app.handlers["agent_session_stopped"]({"session": {"thread_ts": "1.1"}}, _ack)

    assert brain_dir.unread_notes()[0].body == "Filip stopped pool"


async def test_a_stop_for_an_unknown_thread_is_ignored(slack_in, app, brain_dir, woken):
    await app.handlers["agent_session_stopped"]({"thread_ts": "nope"}, _ack)

    assert brain_dir.unread_notes() == []
    assert woken == []


async def test_the_quiet_events_are_acknowledged(slack_in, app):
    for name in (
        "app_home_opened",
        "agent_session_title_changed",
        "assistant_thread_started",
        "assistant_thread_context_changed",
    ):
        await app.handlers[name]({}, _ack)


# -- wiring ------------------------------------------------------------


async def test_start_slack_wires_posting_into_approvals(
    brain_dir, approvals, woken, monkeypatch
):
    """Only the wiring is ours; Bolt's own constructors are stubbed.

    A real ``AsyncApp`` opens an aiohttp session bound to the running loop and
    never gets closed here, which wedges the test loop at teardown -- so the
    two Slack classes are replaced with the fakes the rest of this file uses.
    """
    import slack_bolt.adapter.socket_mode.aiohttp as bolt_socket
    import slack_bolt.async_app as bolt_app

    built: dict = {}

    class StubApp(FakeApp):
        def __init__(self, **kwargs):
            super().__init__()
            built["app_kwargs"] = kwargs

    def stub_handler(app, app_token):
        built["handler"] = (app, app_token)
        return "handler"

    monkeypatch.setattr(bolt_app, "AsyncApp", StubApp)
    monkeypatch.setattr(bolt_socket, "AsyncSocketModeHandler", stub_handler)

    settings = load_settings(
        {"SLACK_BOT_TOKEN": "xoxb-x", "SLACK_APP_TOKEN": "xapp-x", "SLACK_USER_ID": USER}
    )
    out, handler = await start_slack(settings, brain_dir, approvals, woken.append)

    # Bound methods are built fresh per attribute access, so `is` would compare
    # two different wrappers of the same function; `==` is the identity test.
    assert approvals.on_message == out.post
    assert approvals.on_message.__self__ is out
    assert built["app_kwargs"]["token"] == "xoxb-x"
    assert built["handler"][1] == "xapp-x"
    assert handler == "handler"
    # The inbound handlers are attached to the same app the handler drives.
    assert set(built["handler"][0].handlers) == {
        "message",
        "reaction_added",
        "agent_session_stopped",
        "app_home_opened",
        "agent_session_title_changed",
        "assistant_thread_started",
        "assistant_thread_context_changed",
    }
    # Approve/reject buttons are registered as actions, not events.
    from ai_brain.approvals import APPROVE_ACTION, REJECT_ACTION

    assert set(built["handler"][0].actions) == {
        APPROVE_ACTION,
        REJECT_ACTION,
        "answer_yes",
        "answer_no",
    }


# -- tools -------------------------------------------------------------


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_slack_tools(reg)
    return reg


@pytest.fixture
def make_ctx(brain_dir, expert_dir, woken):
    memories = {"brain": brain_dir, "energy": expert_dir}

    def _make(loop: str, **extras) -> ToolContext:
        return ToolContext(
            loop=loop,
            memory=memories[loop],
            memories=memories,
            settings=load_settings({}),
            wake=woken.append,
            extras=extras,
        )

    return _make


async def call(registry, ctx, tool, **args):
    return json.loads(await registry.dispatch(ctx, ToolCall(id="1", name=tool, args=args)))


async def test_slack_post_tool_posts_and_returns_the_ts(registry, make_ctx, out, client):
    ctx = make_ctx("brain", slack_out=out)

    result = await call(registry, ctx, "slack_post", topic="pool", text="hello")

    assert result["ok"] is True
    assert result["ts"] == "1.1"
    assert client.methods("chat_postMessage")[0]["text"] == "hello"


async def test_slack_post_tool_reports_the_rate_cap_as_an_error(
    registry, make_ctx, client, brain_dir, moving_clock
):
    capped = SlackOut(client, USER, brain_dir, moving_clock, max_per_hour=1)
    ctx = make_ctx("brain", slack_out=capped)
    await call(registry, ctx, "slack_post", topic="pool", text="first")

    result = await call(registry, ctx, "slack_post", topic="pool", text="second")

    assert "error" in result
    assert "cap" in result["error"].lower()


async def test_slack_tools_error_when_slack_is_not_configured(registry, make_ctx):
    result = await call(registry, make_ctx("brain"), "slack_post", topic="pool", text="hello")

    assert result["error"] == "slack disabled"


async def test_experts_may_not_use_the_slack_tools(registry, make_ctx, out):
    ctx = make_ctx("energy", slack_out=out)

    result = await call(registry, ctx, "slack_post", topic="pool", text="hello")

    assert "policy" in result["error"]


# -- rate cap is charged on delivery, not on attempt --------------------


async def test_a_queued_post_does_not_spend_the_hourly_budget(brain_dir, moving_clock):
    """An outage must not burn the hour on posts that only reached the outbox."""
    failing = FakeClient(fail_methods=frozenset({"chat_postMessage"}))
    out = SlackOut(failing, USER, brain_dir, moving_clock, max_per_hour=2, sleep=_no_sleep)

    assert await out.post("pool", "a") == "queued"
    assert await out.post("pool", "b") == "queued"
    assert out._recent == []

    # Slack comes back: the budget is intact, so both queued posts can go.
    working = FakeClient()
    flusher = SlackOut(working, USER, brain_dir, moving_clock, max_per_hour=2, sleep=_no_sleep)
    assert await flusher.flush_queue() == 2


async def test_a_successful_post_charges_the_cap_exactly_once(out):
    await out.post("pool", "a")
    assert len(out._recent) == 1


async def test_a_flushed_message_charges_the_cap_exactly_once(client, brain_dir, moving_clock):
    queue = brain_dir.outbox_dir / "slack"
    queue.mkdir(parents=True, exist_ok=True)
    (queue / "20260901.json").write_text(json.dumps({"topic": "pool", "text": "m"}))

    out = SlackOut(client, USER, brain_dir, moving_clock, sleep=_no_sleep)
    assert await out.flush_queue() == 1
    assert len(out._recent) == 1


# -- flush order ---------------------------------------------------------


async def test_a_failed_flush_keeps_the_original_file_and_its_place(brain_dir, moving_clock):
    """Delete-after-success: the file that failed keeps its name, so its order."""
    failing = FakeClient(fail_methods=frozenset({"chat_postMessage"}))
    queue = brain_dir.outbox_dir / "slack"
    queue.mkdir(parents=True, exist_ok=True)
    (queue / "20260901-0.json").write_text(json.dumps({"topic": "pool", "text": "first"}))

    out = SlackOut(failing, USER, brain_dir, moving_clock, sleep=_no_sleep)
    assert await out.flush_queue() == 0

    assert [p.name for p in sorted(queue.glob("*.json"))] == ["20260901-0.json"]
    assert json.loads((queue / "20260901-0.json").read_text()) == {
        "topic": "pool",
        "text": "first",
    }


async def test_a_failed_flush_stops_before_the_next_message(brain_dir, moving_clock):
    """Sending the second while the first is stuck would reorder the conversation."""

    class FirstFails(FakeClient):
        async def chat_postMessage(self, **kwargs):
            if kwargs.get("text") == "first":
                self.calls.append(("chat_postMessage", kwargs))
                raise SlackError("stuck")
            return await super().chat_postMessage(**kwargs)

    queue = brain_dir.outbox_dir / "slack"
    queue.mkdir(parents=True, exist_ok=True)
    (queue / "20260901-0.json").write_text(json.dumps({"topic": "pool", "text": "first"}))
    (queue / "20260901-1.json").write_text(json.dumps({"topic": "pool", "text": "second"}))

    client = FirstFails()
    out = SlackOut(client, USER, brain_dir, moving_clock, sleep=_no_sleep)
    assert await out.flush_queue() == 0

    assert [k["text"] for k in client.methods("chat_postMessage")] == ["first"] * 3
    assert [p.name for p in sorted(queue.glob("*.json"))] == [
        "20260901-0.json",
        "20260901-1.json",
    ]


# -- a resolved proposal wakes the brain ---------------------------------


async def test_a_reaction_that_resolves_a_proposal_wakes_the_brain(
    app, brain_dir, out, woken
):
    listener = SlackIn(
        app, brain_dir, FakeApprovals(resolves=make_proposal()), out, woken.append, USER
    )
    listener.register()

    await app.handlers["reaction_added"](
        {"user": USER, "reaction": "white_check_mark", "item": {"ts": "7.7"}}, _ack
    )

    assert woken == ["brain"]


async def test_a_reaction_matching_no_proposal_does_not_wake(slack_in, app, woken):
    await app.handlers["reaction_added"](
        {"user": USER, "reaction": "white_check_mark", "item": {"ts": "7.7"}}, _ack
    )

    assert woken == []


# -- reading the session map -------------------------------------------


async def test_sessions_exposes_the_stored_topic_map(out):
    await out.post("pool", "the pool is cold")

    assert out.sessions()["pool"]["thread_ts"] == "1.1"
    assert out.sessions()["pool"]["channel"] == "D1"


def test_sessions_is_empty_before_anything_is_posted(out):
    assert out.sessions() == {}


async def test_queued_count_counts_what_slack_would_not_take(brain_dir, moving_clock):
    down = SlackOut(FakeClient(frozenset({"chat_postMessage"})), USER, brain_dir, moving_clock)
    down.sleep = _no_sleep
    assert down.queued_count() == 0

    assert await down.post("pool", "first") == "queued"
    assert await down.post("pool", "second") == "queued"

    assert down.queued_count() == 2


# -- the assistant thread ------------------------------------------------


async def test_a_dm_in_the_assistant_thread_binds_chat_and_tags_the_note(
    slack_in, app, out, brain_dir
):
    await app.handlers["message"](
        {
            "channel_type": "im",
            "user": USER,
            "text": "hur mar poolen?",
            "channel": "D9",
            "thread_ts": "5.5",
        },
        _ack,
    )

    chat = out.sessions()["chat"]
    assert (chat["thread_ts"], chat["channel"]) == ("5.5", "D9")
    assert brain_dir.unread_notes()[0].body == "topic: chat\nhur mar poolen?"


async def test_a_dm_in_a_known_topic_thread_keeps_its_own_topic_tag(slack_in, app, out, brain_dir):
    """Binding the chat session must not relabel a thread we already own."""
    await out.post("pool", "the pool is cold")

    await app.handlers["message"](
        {
            "channel_type": "im",
            "user": USER,
            "text": "turn it up",
            "channel": "D1",
            "thread_ts": "1.1",
        },
        _ack,
    )

    assert brain_dir.unread_notes()[0].body == "topic: pool\nturn it up"


async def test_a_top_level_dm_binds_nothing(slack_in, app, out, brain_dir):
    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "hi", "channel": "D1"}, _ack
    )

    assert "chat" not in out.sessions()
    assert brain_dir.unread_notes()[0].body == "hi"


async def test_assistant_thread_started_binds_chat_and_suggests_prompts(slack_in, app, out, client):
    await app.handlers["assistant_thread_started"](
        {
            "type": "assistant_thread_started",
            "assistant_thread": {"channel_id": "D7", "thread_ts": "8.8", "user_id": USER},
        },
        _ack,
    )

    assert out.sessions()["chat"] == {"thread_ts": "8.8", "channel": "D7"}
    prompts = client.methods("assistant.threads.setSuggestedPrompts")
    assert len(prompts) == 1
    assert prompts[0]["channel_id"] == "D7"
    assert prompts[0]["thread_ts"] == "8.8"
    assert [p["title"] for p in prompts[0]["prompts"]] == ["Status", "El"]


async def test_assistant_thread_started_for_another_user_is_ignored(slack_in, app, out, client):
    await app.handlers["assistant_thread_started"](
        {"assistant_thread": {"channel_id": "D7", "thread_ts": "8.8", "user_id": "U-stranger"}},
        _ack,
    )

    assert out.sessions() == {}
    assert client.methods("assistant.threads.setSuggestedPrompts") == []


async def test_a_failing_setSuggestedPrompts_still_leaves_the_binding(slack_in, app, out):
    out.client.fail_methods = frozenset({"assistant.threads.setSuggestedPrompts"})

    await app.handlers["assistant_thread_started"](
        {"assistant_thread": {"channel_id": "D7", "thread_ts": "8.8", "user_id": USER}}, _ack
    )

    assert out.sessions()["chat"]["thread_ts"] == "8.8"


async def test_a_new_topic_gets_its_own_thread_even_with_chat_open(out, client, brain_dir):
    # A topic is never an alias onto whatever thread `chat` happens to point
    # at: `chat` is rebound every time Filip opens a fresh assistant
    # conversation and never expires on its own, so piggybacking here used to
    # mean every unrelated topic the brain ever raised silently landed in
    # whichever thread was first, untitled, and impossible to find later.
    out.bind_chat(channel="D7", thread_ts="8.8")

    ts = await out.post("solar", "vi producerar 4 kW")

    # A fresh DM (D1), not D7 -- solar has never had a session, so it never
    # looks at what chat is bound to.
    assert client.methods("chat_postMessage") == [
        {"channel": "D1", "text": "vi producerar 4 kW", "thread_ts": None}
    ]
    solar = out.sessions()["solar"]
    assert (solar["thread_ts"], solar["channel"]) == ("1.1", "D1")
    assert ts == "1.1"


async def test_a_second_post_to_the_same_topic_reuses_its_own_thread(out, client):
    await out.post("solar", "first")

    await out.post("solar", "second")

    assert [k["text"] for k in client.methods("chat_postMessage")] == ["first", "second"]
    assert [k["thread_ts"] for k in client.methods("chat_postMessage")] == [None, "1.1"]


async def test_posting_to_chat_itself_lands_in_the_bound_thread(out, client):
    out.bind_chat(channel="D7", thread_ts="8.8")

    await out.post("chat", "hej")

    # "8.8" is 1970 to Slack and nothing else has happened in the thread, so
    # it is stale and the reply is broadcast to the DM's main view as well.
    assert client.methods("chat_postMessage") == [
        {"channel": "D7", "text": "hej", "thread_ts": "8.8", "reply_broadcast": True}
    ]


async def test_bind_chat_repoints_where_chat_itself_replies(out, client):
    out.bind_chat(channel="D7", thread_ts="8.8")
    out.bind_chat(channel="D7", thread_ts="9.9")

    await out.post("chat", "hello")

    assert client.methods("chat_postMessage")[0]["thread_ts"] == "9.9"


# -- normalize_topic ---------------------------------------------------


def test_normalize_topic_lowercases_and_strips():
    from ai_brain.slack_io import normalize_topic

    assert normalize_topic("  Pool Pump  ") == "pool-pump"


def test_normalize_topic_turns_spaces_and_underscores_into_dashes():
    from ai_brain.slack_io import normalize_topic

    assert normalize_topic("house_ops findings") == "house-ops-findings"


def test_normalize_topic_strips_a_trailing_date():
    from ai_brain.slack_io import normalize_topic

    assert normalize_topic("think-cycle-2026-09-24") == "think-cycle"


def test_normalize_topic_strips_a_trailing_index_when_a_segment_remains():
    from ai_brain.slack_io import normalize_topic

    # "pool-pump-2" has three segments -- stripping "-2" still leaves the real
    # two-segment name "pool-pump", so the index is genuinely just an index.
    assert normalize_topic("pool-pump-2") == "pool-pump"


def test_normalize_topic_does_not_strip_a_trailing_index_that_would_erase_the_name():
    from ai_brain.slack_io import normalize_topic

    # "floor-1" has only two segments; stripping "-1" would leave the single
    # segment "floor", indistinguishable from a topic that was always just
    # called "floor" -- and would merge "floor-1" with "floor-2". So it does
    # not strip at all.
    assert normalize_topic("floor-1") == "floor-1"
    assert normalize_topic("floor-2") == "floor-2"


def test_normalize_topic_collapses_repeated_dashes():
    from ai_brain.slack_io import normalize_topic

    assert normalize_topic("think - cycle") == "think-cycle"


def test_normalize_topic_variants_converge():
    from ai_brain.slack_io import normalize_topic

    variants = ["think", "Think Cycle", "think-cycle", "think_cycle", "think-cycle-2026-09-24"]
    normalized = {normalize_topic(v) for v in variants}
    # "think" alone normalizes to itself, distinct from the "think cycle"
    # family -- normalize_topic only strips trailing dates/indices, it does
    # not fold unrelated words together. The other four all agree.
    assert normalized == {"think", "think-cycle"}


def test_normalize_topic_does_not_treat_an_arbitrary_suffix_as_an_index():
    from ai_brain.slack_io import normalize_topic

    # "bug" is not a date or a bare numeric index, so it survives -- this is
    # what makes the prefix match (not normalize_topic itself) responsible
    # for folding "pool-pump-bug" into "pool-pump".
    assert normalize_topic("pool-pump-bug") == "pool-pump-bug"


def test_normalize_topic_strips_only_one_trailing_index_not_repeatedly():
    from ai_brain.slack_io import normalize_topic

    # A single trailing "-N" is an index; a second one behind it is just part
    # of the name now -- unlike a date, an index suffix is not stripped
    # repeatedly, so "a-1-2" only loses its final "-2".
    assert normalize_topic("a-1-2") == "a-1"


# -- resolve_topic -------------------------------------------------------


async def test_resolve_topic_is_a_no_op_with_no_existing_sessions(out):
    assert out.resolve_topic("pool-pump") == "pool-pump"


async def test_resolve_topic_keeps_an_exact_match(out):
    await out.post("pool-pump", "hi")
    assert out.resolve_topic("pool-pump") == "pool-pump"


async def test_resolve_topic_folds_a_normalized_variant_into_the_existing_topic(out):
    await out.post("think", "hi")
    assert out.resolve_topic("Think") == "think"


async def test_resolve_topic_folds_a_dated_suffix_into_the_existing_topic(out):
    await out.post("think-cycle", "hi")
    assert out.resolve_topic("think-cycle-2026-09-24") == "think-cycle"


async def test_resolve_topic_folds_a_longer_new_ask_into_a_shorter_existing_topic(out):
    """The sprawl this exists to fix: 'pool-pump-bug' asked fresh, with
    'pool-pump' already an active thread, lands in the existing one."""
    await out.post("pool-pump", "hi")
    assert out.resolve_topic("pool-pump-bug") == "pool-pump"


async def test_resolve_topic_does_not_fold_a_shorter_new_ask_into_a_longer_existing_topic(out):
    """The over-merge a code review caught: a new, short 'pool-pump' must not
    be swallowed by a longer 'pool-pump-bug' that happened to exist first --
    routing only ever goes new-is-longer, never the reverse."""
    await out.post("pool-pump-bug", "hi")
    assert out.resolve_topic("pool-pump") == "pool-pump"


async def test_resolve_topic_does_not_let_a_single_word_topic_absorb_others(out):
    """The other over-merge a code review caught: a generic single-segment
    existing topic like 'house' or 'energy' must never be a match target, or
    the first 'house-ops-findings' or 'energy-prices' post would vanish into
    it. MIN_PREFIX_SEGMENTS requires the existing (shorter) side to have at
    least two segments."""
    await out.post("house", "hi")
    assert out.resolve_topic("house-ops-findings") == "house-ops-findings"

    await out.post("energy", "hi")
    assert out.resolve_topic("energy-prices") == "energy-prices"


async def test_resolve_topic_matches_a_whole_dash_segment_not_a_partial_word(out):
    """'poo' must not fold into 'pool-pump' -- a whole '-' segment must match,
    not a partial word."""
    await out.post("pool-pump", "hi")
    assert out.resolve_topic("poo") == "poo"


async def test_resolve_topic_never_folds_into_chat(out):
    out.bind_chat(channel="D1", thread_ts="1.1")
    assert out.resolve_topic("chat-with-filip") == "chat-with-filip"


async def test_resolve_topic_leaves_chat_itself_alone(out):
    assert out.resolve_topic("chat") == "chat"


async def test_resolve_topic_folds_a_bare_trailing_index_variant(out):
    """'pool-pump-2' is the '-2'-style suffix normalize_topic strips, so it
    folds into an existing 'pool-pump' the same way a dated suffix does."""
    await out.post("pool-pump", "hi")
    assert out.resolve_topic("pool-pump-2") == "pool-pump"


async def test_resolve_topic_does_not_merge_two_distinct_indexed_topics(out):
    """floor-1 and floor-2 are two segments each -- normalize_topic leaves
    both alone (see its own tests), so neither folds into the other or into
    a bare 'floor'."""
    await out.post("floor-1", "hi")
    assert out.resolve_topic("floor-2") == "floor-2"


async def test_a_normalized_variant_actually_posts_into_the_existing_thread(out, client):
    await out.post("pool-pump", "first")
    await out.post("pool-pump-bug", "second")

    assert [k["thread_ts"] for k in client.methods("chat_postMessage")] == [None, "1.1"]
    sessions = out.sessions()
    assert set(sessions) == {"pool-pump"}


async def test_existing_on_disk_sessions_are_never_renamed_by_resolution(out, client, brain_dir):
    """Routing a new post must not touch a session that already exists --
    posting the shorter 'pool-pump' after 'pool-pump-bug' already exists
    creates its own session rather than merging into the longer one."""
    await out.post("pool-pump-bug", "first")
    before = out.sessions()

    await out.post("pool-pump", "second")

    assert out.sessions()["pool-pump-bug"] == before["pool-pump-bug"]
    assert set(out.sessions()) == {"pool-pump-bug", "pool-pump"}


# -- recent_topics ---------------------------------------------------------


async def test_recent_topics_lists_newest_first(out):
    await out.post("pool", "a")
    await out.post("energy", "b")
    await out.post("house-ops", "c")

    assert out.recent_topics() == ["house-ops", "energy", "pool"]


async def test_recent_topics_excludes_chat(out):
    out.bind_chat(channel="D1", thread_ts="1.1")
    await out.post("pool", "a")

    assert "chat" not in out.recent_topics()


async def test_recent_topics_respects_the_limit(out):
    for name in ["alpha", "bravo", "charlie", "delta", "echo"]:
        await out.post(name, "hi")

    assert len(out.recent_topics(limit=2)) == 2


async def test_recent_topics_is_empty_on_a_fresh_brain(out):
    assert out.recent_topics() == []


# -- outbox policy: stale-thread broadcast ---------------------------------


async def test_a_reply_into_a_fresh_thread_is_not_broadcast(out, client, moving_clock):
    await out.post("pool", "first")
    moving_clock.state["now"] = START + timedelta(hours=71)

    await out.post("pool", "second")

    assert "reply_broadcast" not in client.methods("chat_postMessage")[1]


async def test_a_reply_into_a_stale_thread_is_broadcast(out, client, moving_clock):
    await out.post("pool", "first")
    moving_clock.state["now"] = START + timedelta(hours=73)

    await out.post("pool", "second")

    second = client.methods("chat_postMessage")[1]
    assert second["thread_ts"] == "1.1"
    assert second["reply_broadcast"] is True


async def test_a_top_level_post_is_never_broadcast(out, client):
    await out.post("pool", "first")
    assert "reply_broadcast" not in client.methods("chat_postMessage")[0]


async def test_a_session_from_before_the_policy_is_judged_by_its_thread_ts(
    out, client, brain_dir
):
    """A Sep/Oct thread with no last_at is stale straight away -- the case
    that put 9 of 13 DMs where Filip never saw them."""
    sept = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc).timestamp()
    (brain_dir.root / "sessions.json").write_text(
        json.dumps({"pool": {"thread_ts": f"{sept:.6f}", "channel": "D1"}})
    )

    await out.post("pool", "news")

    assert client.methods("chat_postMessage")[0]["reply_broadcast"] is True


async def test_filip_writing_in_the_thread_keeps_it_fresh(
    slack_in, app, out, client, moving_clock
):
    await out.post("pool", "first")
    moving_clock.state["now"] = START + timedelta(hours=70)
    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "ok", "thread_ts": "1.1"}, _ack
    )
    moving_clock.state["now"] = START + timedelta(hours=80)

    await out.post("pool", "second")

    assert "reply_broadcast" not in client.methods("chat_postMessage")[-1]


async def test_the_stale_threshold_is_configurable(client, brain_dir, moving_clock):
    out = SlackOut(client, USER, brain_dir, moving_clock, stale_thread_h=1)
    await out.post("pool", "first")
    moving_clock.state["now"] = START + timedelta(hours=2)

    await out.post("pool", "second")

    assert client.methods("chat_postMessage")[1]["reply_broadcast"] is True


async def test_a_post_records_its_last_ts_and_time(out, moving_clock):
    await out.post("pool", "first")
    moving_clock.state["now"] = START + timedelta(minutes=5)
    await out.post("pool", "second")

    session = out.sessions()["pool"]
    assert session["last_ts"] == "2.1"
    assert session["last_at"] == (START + timedelta(minutes=5)).isoformat(timespec="seconds")
    assert session["thread_ts"] == "1.1"


# -- outbox policy: quiet hours ----------------------------------------------

STHLM = ZoneInfo("Europe/Stockholm")


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        (datetime(2026, 9, 6, 23, 0, tzinfo=STHLM), datetime(2026, 9, 7, 7, 0, tzinfo=STHLM)),
        (datetime(2026, 9, 7, 3, 30, tzinfo=STHLM), datetime(2026, 9, 7, 7, 0, tzinfo=STHLM)),
        (datetime(2026, 9, 6, 22, 0, tzinfo=STHLM), datetime(2026, 9, 7, 7, 0, tzinfo=STHLM)),
        (datetime(2026, 9, 7, 7, 0, tzinfo=STHLM), None),
        (datetime(2026, 9, 6, 12, 0, tzinfo=STHLM), None),
    ],
)
def test_quiet_until_wraps_midnight_in_stockholm_time(local, expected):
    assert quiet_until(local.astimezone(timezone.utc), (22, 7)) == expected


def test_quiet_until_handles_a_window_that_does_not_wrap():
    inside = datetime(2026, 9, 7, 2, 0, tzinfo=STHLM)
    assert quiet_until(inside, (1, 6)) == datetime(2026, 9, 7, 6, 0, tzinfo=STHLM)
    assert quiet_until(datetime(2026, 9, 7, 23, 0, tzinfo=STHLM), (1, 6)) is None


def test_quiet_until_is_none_when_quiet_hours_are_off():
    assert quiet_until(datetime(2026, 9, 7, 3, 0, tzinfo=STHLM), None) is None


def test_quiet_until_ends_at_the_wall_clock_hour_across_dst():
    # 2026-10-25 is the CEST->CET switch; 07:00 is still 07:00 local.
    night = datetime(2026, 10, 24, 23, 0, tzinfo=STHLM)
    end = quiet_until(night, (22, 7))
    assert (end.hour, end.day) == (7, 25)
    assert end.utcoffset() == timedelta(hours=1)


# 23:00 Stockholm (CEST) on START's day, and 07:30 the next morning.
NIGHT = datetime(2026, 9, 6, 21, 0, tzinfo=timezone.utc)
MORNING = datetime(2026, 9, 7, 5, 30, tzinfo=timezone.utc)


@pytest.fixture
def quiet_out(client, brain_dir, moving_clock):
    return SlackOut(client, USER, brain_dir, moving_clock, quiet_hours=(22, 7))


async def test_a_held_post_is_not_sent_and_not_in_the_retry_queue(
    quiet_out, client, moving_clock
):
    moving_clock.state["now"] = NIGHT

    held = quiet_out.hold("pool", "pH drifting")

    assert held["until"] == datetime(2026, 9, 7, 7, 0, tzinfo=STHLM)
    assert client.methods("chat_postMessage") == []
    assert quiet_out.held_count() == 1
    assert quiet_out.queued_count() == 0
    # The retry tick must not send it in the middle of the night.
    assert await quiet_out.flush_queue() == 0
    assert client.methods("chat_postMessage") == []


async def test_holding_charges_the_rate_cap(client, brain_dir, moving_clock):
    out = SlackOut(client, USER, brain_dir, moving_clock, max_per_hour=1, quiet_hours=(22, 7))
    moving_clock.state["now"] = NIGHT
    out.hold("pool", "one")

    with pytest.raises(SlackRateCapped):
        out.hold("pool", "two")


async def test_release_does_nothing_while_still_quiet(quiet_out, client, moving_clock):
    moving_clock.state["now"] = NIGHT
    quiet_out.hold("pool", "pH drifting")

    assert await quiet_out.release_held() == 0
    assert client.methods("chat_postMessage") == []
    assert quiet_out.held_count() == 1


async def test_release_sends_one_digest_then_each_item_into_its_thread(
    quiet_out, client, moving_clock
):
    await quiet_out.post("pool", "pool thread")  # 12:00 local, thread 1.1
    moving_clock.state["now"] = NIGHT
    quiet_out.hold("pool", "pH drifting\nmore detail on a second line")
    quiet_out.hold("energy", "export price spiked")
    quiet_out.hold("pool", "filter pressure up", question=True)
    moving_clock.state["now"] = MORNING

    assert await quiet_out.release_held() == 3

    posts = client.methods("chat_postMessage")[1:]
    digest = posts[0]
    assert "thread_ts" not in digest
    assert digest["text"].splitlines() == [
        "☀️ *Overnight: 3 updates* -- details in each topic's thread.",
        "*pool*",
        "• pH drifting",
        "• ❓ filter pressure up",
        "*energy*",
        "• export price spiked",
    ]
    # Then the full text into each topic's own thread, never broadcast --
    # the digest was the notification.
    assert [(p["text"], p["thread_ts"]) for p in posts[1:]] == [
        ("pH drifting\nmore detail on a second line", "1.1"),
        ("export price spiked", None),
        ("filter pressure up", "1.1"),
    ]
    assert all("reply_broadcast" not in p for p in posts[1:])
    assert posts[3]["blocks"][1]["elements"][0]["action_id"] == "answer_yes"
    assert quiet_out.held_count() == 0
    assert await quiet_out.release_held() == 0


async def test_a_failed_digest_keeps_everything_held(brain_dir, moving_clock):
    client = FakeClient(fail_methods=frozenset({"chat_postMessage"}))
    out = SlackOut(
        client, USER, brain_dir, moving_clock, sleep=_no_sleep, quiet_hours=(22, 7)
    )
    moving_clock.state["now"] = NIGHT
    out.hold("pool", "pH drifting")
    moving_clock.state["now"] = MORNING

    assert await out.release_held() == 0
    assert out.held_count() == 1
    assert out.queued_count() == 0


async def test_a_held_correction_replaces_the_held_text(quiet_out, moving_clock):
    moving_clock.state["now"] = NIGHT
    quiet_out.hold("pool", "pH 7.9")
    quiet_out.hold("energy", "unrelated")

    held = quiet_out.hold("pool", "pH 7.6, misread", replaces_last=True)

    assert held["replaced_held"] is True
    assert quiet_out.held_count() == 2
    texts = sorted(
        json.loads(p.read_text())["text"] for p in quiet_out.held_dir.glob("*.json")
    )
    assert texts == ["pH 7.6, misread", "unrelated"]


async def test_a_held_correction_with_nothing_held_edits_on_release(
    quiet_out, client, moving_clock
):
    moving_clock.state["now"] = NIGHT - timedelta(hours=2)  # 21:00 local
    await quiet_out.post("pool", "pH 7.9")
    moving_clock.state["now"] = NIGHT
    quiet_out.hold("pool", "pH 7.6, misread", replaces_last=True)
    moving_clock.state["now"] = MORNING

    await quiet_out.release_held()

    [update] = client.methods("chat_update")
    assert update["ts"] == "1.1"
    assert update["text"] == "✏️ Updated: pH 7.6, misread"
    # Original post plus the digest; the correction itself was an edit.
    assert len(client.methods("chat_postMessage")) == 2


# -- outbox policy: corrections edit -----------------------------------------


async def test_edit_last_updates_the_last_message_in_place(out, client, moving_clock):
    await out.post("pool", "pool is 19 C")
    moving_clock.state["now"] = START + timedelta(hours=2)

    ts = await out.edit_last("pool", "pool is 21 C, sensor lagged")

    assert ts == "1.1"
    [update] = client.methods("chat_update")
    assert update["channel"] == "D1"
    assert update["ts"] == "1.1"
    assert update["text"] == "✏️ Updated: pool is 21 C, sensor lagged"
    assert len(client.methods("chat_postMessage")) == 1


async def test_edit_last_refuses_a_message_older_than_a_day(out, client, moving_clock):
    await out.post("pool", "pool is 19 C")
    moving_clock.state["now"] = START + timedelta(hours=25)

    assert await out.edit_last("pool", "correction") is None
    assert client.methods("chat_update") == []


async def test_edit_last_refuses_once_filip_has_replied(slack_in, app, out, client, moving_clock):
    await out.post("pool", "pool is 19 C")
    moving_clock.state["now"] = START + timedelta(minutes=10)
    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "thanks", "thread_ts": "1.1"}, _ack
    )

    assert await out.edit_last("pool", "correction") is None


async def test_edit_last_with_no_history_returns_none(out):
    assert await out.edit_last("pool", "correction") is None


async def test_edit_last_falls_back_when_slack_refuses_the_edit(brain_dir, moving_clock):
    client = FakeClient(fail_methods=frozenset({"chat_update"}))
    out = SlackOut(client, USER, brain_dir, moving_clock)
    await out.post("pool", "pool is 19 C")

    assert await out.edit_last("pool", "correction") is None


async def test_edit_last_targets_the_newest_bot_message_not_the_root(out, client):
    await out.post("pool", "root")
    await out.post("pool", "reply")

    assert await out.edit_last("pool", "fixed reply") == "2.1"


async def test_slack_post_tool_reports_an_edit(registry, make_ctx, out, client):
    ctx = make_ctx("brain", slack_out=out)
    await call(registry, ctx, "slack_post", topic="pool", text="19 C")

    result = await call(
        registry, ctx, "slack_post", topic="pool", text="21 C", replaces_last=True
    )

    assert result["action"] == "updated"
    assert len(client.methods("chat_postMessage")) == 1


# -- outbox policy: questions ------------------------------------------------


async def test_a_question_post_carries_yes_no_buttons_and_is_tracked(out, client, moving_clock):
    await out.post("pool", "Run the pump tonight?", question=True)

    [message] = client.methods("chat_postMessage")
    section, actions = message["blocks"]
    assert section["text"]["text"] == "Run the pump tonight?"
    assert [(b["action_id"], b["value"]) for b in actions["elements"]] == [
        ("answer_yes", "pool"),
        ("answer_no", "pool"),
    ]
    moving_clock.state["now"] = START + timedelta(days=2)
    assert out.open_questions() == [
        {
            "topic": "pool",
            "asked_at": START.isoformat(timespec="seconds"),
            "age_days": 2.0,
            "text": "Run the pump tonight?",
        }
    ]


async def test_a_plain_post_opens_no_question(out):
    await out.post("pool", "fyi")
    assert out.open_questions() == []


async def test_filip_replying_in_the_thread_answers_the_question(slack_in, app, out):
    await out.post("pool", "Run the pump tonight?", question=True)

    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "ja", "thread_ts": "1.1"}, _ack
    )

    assert out.open_questions() == []
    assert out.sessions()["pool"]["answered_at"]


async def test_a_new_question_on_an_answered_topic_is_open_again(slack_in, app, out):
    await out.post("pool", "Q1?", question=True)
    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "ja", "thread_ts": "1.1"}, _ack
    )

    await out.post("pool", "Q2?", question=True)

    assert [q["text"] for q in out.open_questions()] == ["Q2?"]


async def test_a_queued_question_is_still_a_question_once_flushed(brain_dir, moving_clock):
    client = FakeClient(fail_methods=frozenset({"chat_postMessage"}))
    out = SlackOut(client, USER, brain_dir, moving_clock, sleep=_no_sleep)
    assert await out.post("pool", "Run it?", question=True) == "queued"

    client.fail_methods = frozenset()
    assert await out.flush_queue() == 1

    assert [q["topic"] for q in out.open_questions()] == ["pool"]
    assert client.methods("chat_postMessage")[-1]["blocks"][1]["type"] == "actions"


def _answer_click(action_id: str, topic: str = "pool", ts: str = "1.1") -> dict:
    return {
        "user": {"id": USER},
        "actions": [{"action_id": action_id, "value": topic}],
        "message": {"ts": ts, "text": "Run the pump tonight?"},
        "channel": {"id": "D1"},
    }


async def test_a_yes_click_becomes_an_owner_note_and_wakes_the_brain(
    slack_in, app, out, client, brain_dir, approvals, woken
):
    await out.post("pool", "Run the pump tonight?", question=True)

    await app.actions["answer_yes"](_answer_click("answer_yes"), _ack)

    [note] = brain_dir.unread_notes()
    assert note.sender == "filip"
    assert note.body == "topic: pool\nAnswer: yes"
    assert woken == ["brain"]
    # Never mistaken for an approval.
    assert approvals.buttons == []
    assert out.open_questions() == []
    assert out.sessions()["pool"]["answer"] == "yes"


async def test_an_answer_click_replaces_the_buttons_with_the_answer(slack_in, app, out, client):
    await out.post("pool", "Run the pump tonight?", question=True)

    await app.actions["answer_no"](_answer_click("answer_no"), _ack)

    [update] = client.methods("chat_update")
    assert update["ts"] == "1.1"
    assert update["text"] == "Run the pump tonight?\n\n*Answer:* ❌ no"
    assert [b["type"] for b in update["blocks"]] == ["section"]


async def test_an_answer_is_owed_a_reply(slack_in, app, out, brain_dir):
    from ai_brain.tools.slack_tools import owed_replies

    await out.post("pool", "Run the pump tonight?", question=True)
    await app.actions["answer_yes"](_answer_click("answer_yes"), _ack)

    assert owed_replies(brain_dir.unread_notes()) == {"pool"}


async def test_a_second_click_on_the_same_question_is_ignored(
    slack_in, app, out, brain_dir, woken
):
    await out.post("pool", "Run the pump tonight?", question=True)

    await app.actions["answer_yes"](_answer_click("answer_yes"), _ack)
    await app.actions["answer_no"](_answer_click("answer_no"), _ack)

    assert len(brain_dir.unread_notes()) == 1
    assert woken == ["brain"]


async def test_an_answer_click_from_anyone_else_is_ignored(slack_in, app, out, brain_dir):
    await out.post("pool", "Run the pump tonight?", question=True)
    click = _answer_click("answer_yes")
    click["user"] = {"id": "U-someone"}

    await app.actions["answer_yes"](click, _ack)

    assert brain_dir.unread_notes() == []


async def test_an_answer_with_an_unknown_topic_falls_back_to_the_thread(
    slack_in, app, out, brain_dir
):
    await out.post("pool", "Run the pump tonight?", question=True)

    await app.actions["answer_yes"](_answer_click("answer_yes", topic="gone"), _ack)

    assert brain_dir.unread_notes()[0].body == "topic: pool\nAnswer: yes"


async def test_slack_open_questions_tool_reads_slack_out(registry, make_ctx, out):
    await out.post("pool", "Run the pump tonight?", question=True)

    result = await call(registry, make_ctx("brain", slack_out=out), "slack_open_questions")

    assert [q["topic"] for q in result["open_questions"]] == ["pool"]


async def test_experts_may_not_list_open_questions(registry, make_ctx, out):
    result = await call(registry, make_ctx("energy", slack_out=out), "slack_open_questions")
    assert "error" in result


# -- outbox policy: config -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", (22, 7)),
        ("22-07", (22, 7)),
        ("23-6", (23, 6)),
        ("off", None),
        ("OFF", None),
        ("none", None),
        ("07-07", None),
        ("garbage", (22, 7)),
        ("25-07", (22, 7)),
    ],
)
def test_quiet_hours_setting(raw, expected):
    assert load_settings({"SLACK_QUIET_HOURS": raw}).slack_quiet_hours == expected


def test_stale_thread_hours_setting():
    assert load_settings({}).slack_stale_thread_h == 72
    assert load_settings({"SLACK_STALE_THREAD_H": "24"}).slack_stale_thread_h == 24


async def test_start_slack_passes_the_policy_settings_to_slack_out(
    brain_dir, approvals, woken, monkeypatch
):
    import slack_bolt.adapter.socket_mode.aiohttp as bolt_socket
    import slack_bolt.async_app as bolt_app

    class StubApp(FakeApp):
        def __init__(self, **_kwargs):
            super().__init__()

    monkeypatch.setattr(bolt_app, "AsyncApp", StubApp)
    monkeypatch.setattr(bolt_socket, "AsyncSocketModeHandler", lambda app, token: None)
    settings = load_settings(
        {
            "SLACK_BOT_TOKEN": "xoxb-x",
            "SLACK_APP_TOKEN": "xapp-x",
            "SLACK_USER_ID": USER,
            "SLACK_QUIET_HOURS": "23-06",
            "SLACK_STALE_THREAD_H": "48",
        }
    )

    out, _ = await start_slack(settings, brain_dir, approvals, woken.append)

    assert out.quiet_hours == (23, 6)
    assert out.stale_thread_s == 48 * 3600


