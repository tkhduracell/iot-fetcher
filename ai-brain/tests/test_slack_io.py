import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from ai_brain.config import load_settings
from ai_brain.llm import ToolCall
from ai_brain.slack_io import SlackIn, SlackOut, SlackRateCapped, start_slack
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.slack_tools import register_slack_tools

START = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
USER = "U-filip"


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


async def test_first_post_opens_dm_creates_thread_and_renames_session(out, client, brain_dir):
    ts = await out.post("pool", "the pool is cold")

    assert ts == "1.1"
    assert client.methods("conversations_open") == [{"users": USER}]
    assert client.methods("chat_postMessage") == [
        {"channel": "D1", "text": "the pool is cold", "thread_ts": None}
    ]
    assert client.methods("agents.sessions.rename") == [
        {"channel_id": "D1", "thread_ts": "1.1", "title": "pool"}
    ]
    assert client.methods("agents.sessions.setStatus") == [
        {"channel_id": "D1", "thread_ts": "1.1", "status": "processing"}
    ]

    sessions = json.loads((brain_dir.root / "sessions.json").read_text())
    assert sessions == {"pool": {"thread_ts": "1.1", "channel": "D1", "status": "processing"}}


async def test_second_post_threads_under_the_same_session(out, client):
    await out.post("pool", "first")
    await out.post("pool", "second")

    assert client.methods("chat_postMessage") == [
        {"channel": "D1", "text": "first", "thread_ts": None},
        {"channel": "D1", "text": "second", "thread_ts": "1.1"},
    ]
    # The DM is opened once and the session named once; only the reply is new.
    assert len(client.methods("conversations_open")) == 1
    assert len(client.methods("agents.sessions.rename")) == 1


async def test_a_second_topic_gets_its_own_thread(out, client, brain_dir):
    await out.post("pool", "first")
    await out.post("energy", "hello")

    assert client.methods("agents.sessions.rename") == [
        {"channel_id": "D1", "thread_ts": "1.1", "title": "pool"},
        {"channel_id": "D1", "thread_ts": "2.1", "title": "energy"},
    ]
    sessions = json.loads((brain_dir.root / "sessions.json").read_text())
    assert sessions["pool"]["thread_ts"] == "1.1"
    assert sessions["energy"]["thread_ts"] == "2.1"


async def test_sessions_survive_a_restart(client, brain_dir, moving_clock):
    first = SlackOut(client, USER, brain_dir, moving_clock)
    await first.post("pool", "before restart")

    second = SlackOut(client, USER, brain_dir, moving_clock)
    await second.post("pool", "after restart")

    assert client.methods("chat_postMessage")[-1]["thread_ts"] == "1.1"
    assert len(client.methods("agents.sessions.rename")) == 1


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


# -- status ------------------------------------------------------------


async def test_set_status_updates_the_session_and_the_file(out, client, brain_dir):
    await out.post("pool", "hello")
    await out.set_status("pool", "active")

    assert client.methods("agents.sessions.setStatus")[-1] == {
        "channel_id": "D1",
        "thread_ts": "1.1",
        "status": "active",
    }
    sessions = json.loads((brain_dir.root / "sessions.json").read_text())
    assert sessions["pool"]["status"] == "active"


async def test_close_marks_the_session_closed(out, client, brain_dir):
    await out.post("pool", "hello")
    await out.close("pool")

    assert client.methods("agents.sessions.setStatus")[-1]["status"] == "closed"
    sessions = json.loads((brain_dir.root / "sessions.json").read_text())
    assert sessions["pool"]["status"] == "closed"


async def test_set_status_on_an_unknown_topic_does_nothing(out, client):
    await out.set_status("never-posted", "closed")
    assert client.methods("agents.sessions.setStatus") == []


async def test_set_status_swallows_api_errors(brain_dir, moving_clock):
    client = FakeClient()
    out = SlackOut(client, USER, brain_dir, moving_clock)
    await out.post("pool", "hello")
    client.fail_methods = frozenset({"agents.sessions.setStatus"})

    await out.set_status("pool", "active")  # must not raise


# -- inbound -----------------------------------------------------------


class FakeApprovals:
    def __init__(self, resolves: object = None) -> None:
        self.reactions: list[tuple[str, str]] = []
        self.resolves = resolves

    async def on_reaction(self, slack_ts: str, emoji: str):
        self.reactions.append((slack_ts, emoji))
        return self.resolves


class FakeApp:
    """Collects the handlers ``register`` attaches, so tests can call them."""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.client = FakeClient()

    def event(self, name: str):
        def _decorate(fn):
            self.handlers[name] = fn
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


async def test_a_dm_inside_a_topic_thread_is_tagged_with_the_topic(slack_in, app, out, brain_dir):
    await out.post("pool", "the pool is cold")

    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "turn it up", "thread_ts": "1.1"}, _ack
    )

    assert brain_dir.unread_notes()[0].body == "topic: pool\nturn it up"


async def test_a_dm_in_an_unknown_thread_is_left_untagged(slack_in, app, brain_dir):
    await app.handlers["message"](
        {"channel_type": "im", "user": USER, "text": "hi", "thread_ts": "9.9"}, _ack
    )

    assert brain_dir.unread_notes()[0].body == "hi"


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


async def test_stopping_a_session_closes_it_and_tells_the_brain(
    slack_in, app, out, client, brain_dir, woken
):
    await out.post("pool", "the pool is cold")

    await app.handlers["agent_session_stopped"]({"thread_ts": "1.1"}, _ack)

    assert client.methods("agents.sessions.setStatus")[-1]["status"] == "closed"
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


async def test_slack_post_tool_posts_and_records_the_topic(registry, make_ctx, out, client):
    ctx = make_ctx("brain", slack_out=out)

    result = await call(registry, ctx, "slack_post", topic="pool", text="hello")

    assert result["ok"] is True
    assert result["ts"] == "1.1"
    assert client.methods("chat_postMessage")[0]["text"] == "hello"
    assert ctx.extras["cycle_topics"] == ["pool"]


async def test_slack_post_tool_reports_the_rate_cap_as_an_error(
    registry, make_ctx, client, brain_dir, moving_clock
):
    capped = SlackOut(client, USER, brain_dir, moving_clock, max_per_hour=1)
    ctx = make_ctx("brain", slack_out=capped)
    await call(registry, ctx, "slack_post", topic="pool", text="first")

    result = await call(registry, ctx, "slack_post", topic="pool", text="second")

    assert "error" in result
    assert "cap" in result["error"].lower()


async def test_slack_close_tool_closes_the_session(registry, make_ctx, out, client):
    ctx = make_ctx("brain", slack_out=out)
    await call(registry, ctx, "slack_post", topic="pool", text="hello")

    assert (await call(registry, ctx, "slack_close", topic="pool"))["ok"] is True
    assert client.methods("agents.sessions.setStatus")[-1]["status"] == "closed"


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


# -- malformed sessions.json --------------------------------------------


async def test_set_status_skips_a_session_missing_its_channel(out, client, brain_dir):
    (brain_dir.root / "sessions.json").write_text(json.dumps({"pool": {"thread_ts": "1.1"}}))

    await out.set_status("pool", "active")

    assert client.methods("agents.sessions.setStatus") == []


async def test_set_status_skips_a_session_that_is_not_an_object(out, client, brain_dir):
    (brain_dir.root / "sessions.json").write_text(json.dumps({"pool": "1.1"}))

    await out.close("pool")

    assert client.methods("agents.sessions.setStatus") == []


# -- a resolved proposal wakes the brain ---------------------------------


async def test_a_reaction_that_resolves_a_proposal_wakes_the_brain(
    app, brain_dir, out, woken
):
    listener = SlackIn(app, brain_dir, FakeApprovals(resolves=object()), out, woken.append, USER)
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

    assert out.sessions() == {"pool": {"thread_ts": "1.1", "channel": "D1", "status": "processing"}}


def test_sessions_is_empty_before_anything_is_posted(out):
    assert out.sessions() == {}


async def test_queued_count_counts_what_slack_would_not_take(brain_dir, moving_clock):
    down = SlackOut(FakeClient(frozenset({"chat_postMessage"})), USER, brain_dir, moving_clock)
    down.sleep = _no_sleep
    assert down.queued_count() == 0

    assert await down.post("pool", "first") == "queued"
    assert await down.post("pool", "second") == "queued"

    assert down.queued_count() == 2
