import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ai_brain.config import load_settings
from ai_brain.llm import ToolCall
from ai_brain.memory import Note
from ai_brain.slack_io import SlackRateCapped
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.slack_tools import ANY_TOPIC, owed_replies, register_slack_tools


class FakeSlackOut:
    """Mimics enough of SlackOut for the slack_post tool: resolution, posting,
    and the session map resolve_topic/recent_topics read from."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []
        self.questions: list[str] = []
        self.held: list[dict] = []
        self.edits: list[tuple[str, str]] = []
        self._sessions: dict[str, str] = {}  # topic -> thread_ts, insertion order
        self.raises: Exception | None = None
        self.next_ts = 0
        # What quiet_until reports: None outside quiet hours.
        self.quiet: datetime | None = None
        # What edit_last returns: a ts when an edit is possible, else None.
        self.editable_ts: str | None = None
        self.open: list[dict] = []

    async def post(self, topic: str, text: str, *, question: bool = False) -> str:
        if self.raises is not None:
            raise self.raises
        resolved = self.resolve_topic(topic)
        self.posts.append((resolved, text))
        if question:
            self.questions.append(resolved)
        self.next_ts += 1
        ts = f"{self.next_ts}.1"
        self._sessions.setdefault(resolved, ts)
        return ts

    async def edit_last(self, topic: str, text: str, *, question: bool = False):
        if self.editable_ts is not None:
            self.edits.append((self.resolve_topic(topic), text))
        return self.editable_ts

    def quiet_until(self):
        return self.quiet

    def hold(self, topic, text, *, question=False, replaces_last=False):
        if self.raises is not None:
            raise self.raises
        resolved = self.resolve_topic(topic)
        self.held.append(
            {"topic": resolved, "text": text, "question": question, "replaces_last": replaces_last}
        )
        return {"topic": resolved, "replaced_held": False, "until": self.quiet}

    def open_questions(self):
        return self.open

    def resolve_topic(self, topic: str) -> str:
        # Delegates to the real implementation rather than reimplementing the
        # matching rules a second time, which would drift from SlackOut's own
        # the moment either changed.
        from ai_brain.slack_io import _is_new_variant_of, normalize_topic

        if topic in self._sessions:
            return topic
        normalized = normalize_topic(topic)
        exact = [t for t in self._sessions if normalize_topic(t) == normalized]
        if exact:
            return min(exact, key=len)
        variant_of = [
            t for t in self._sessions if _is_new_variant_of(normalized, normalize_topic(t))
        ]
        if variant_of:
            return min(variant_of, key=len)
        return topic

    def recent_topics(self, limit: int = 15) -> list[str]:
        return list(reversed(self._sessions))[:limit]


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_slack_tools(reg)
    return reg


@pytest.fixture
def slack_out():
    return FakeSlackOut()


@pytest.fixture
def make_ctx(brain_dir, slack_out):
    def _make(extras: dict | None = None) -> ToolContext:
        merged = {"slack_out": slack_out}
        merged.update(extras or {})
        return ToolContext(
            loop="brain",
            memory=brain_dir,
            memories={"brain": brain_dir},
            settings=load_settings({}),
            wake=lambda _loop: None,
            extras=merged,
        )

    return _make


async def call(registry, ctx, **args):
    return json.loads(
        await registry.dispatch(ctx, ToolCall(id="1", name="slack_post", args=args))
    )


# --- slack_post basics ------------------------------------------------------


async def test_slack_post_without_slack_out_errors(registry, make_ctx):
    ctx = make_ctx({"slack_out": None})
    out = await call(registry, ctx, topic="pool", text="hi")
    assert "slack disabled" in out["error"]


async def test_slack_post_returns_the_ts(registry, make_ctx):
    out = await call(registry, make_ctx(), topic="pool", text="hi")
    assert out["ok"] is True
    assert out["ts"] == "1.1"


async def test_slack_post_surfaces_a_rate_cap_as_an_error(registry, make_ctx, slack_out):
    slack_out.raises = SlackRateCapped("slack post cap reached (20/h)")
    out = await call(registry, make_ctx(), topic="pool", text="hi")
    assert "cap reached" in out["error"]


# --- topic resolution in the result -----------------------------------------


async def test_slack_post_result_names_the_resolved_topic(registry, make_ctx):
    ctx = make_ctx()
    await call(registry, ctx, topic="pool-pump", text="first")

    out = await call(registry, ctx, topic="pool-pump-bug", text="second")

    assert out["topic"] == "pool-pump"


async def test_slack_post_result_lists_active_topics(registry, make_ctx):
    ctx = make_ctx()
    await call(registry, ctx, topic="pool", text="a")
    await call(registry, ctx, topic="energy", text="b")

    out = await call(registry, ctx, topic="house-ops", text="c")

    assert set(out["active_topics"]) >= {"pool", "energy", "house-ops"}


# --- owed_replies -----------------------------------------------------------


def test_owed_replies_reads_the_topic_line():
    note = Note(path=None, sender="filip", body="topic: pool\nwhat's the temp?", created=None)
    assert owed_replies([note]) == {"pool"}


def test_owed_replies_falls_back_to_any_topic_without_a_topic_line():
    note = Note(path=None, sender="filip", body="hej", created=None)
    assert owed_replies([note]) == {ANY_TOPIC}


async def test_slack_post_discards_the_asked_for_topic_not_the_resolved_one(registry, make_ctx):
    """owed_replies is keyed on Filip's own `topic:` line -- a reply that gets
    routed by resolve_topic into a different existing thread must still tick
    off what he actually asked about, not wherever it landed."""
    ctx = make_ctx()
    await call(registry, ctx, topic="pool-pump", text="setup")

    owed = {"pool-pump-bug"}
    ctx.extras["owed_replies"] = owed
    await call(registry, ctx, topic="pool-pump-bug", text="fixed it")

    assert owed == set()


# --- quiet hours ------------------------------------------------------------

MORNING = datetime(2026, 11, 9, 7, 0, tzinfo=ZoneInfo("Europe/Stockholm"))


async def test_a_post_during_quiet_hours_is_held_and_says_until_when(registry, make_ctx, slack_out):
    slack_out.quiet = MORNING

    out = await call(registry, make_ctx(), topic="pool", text="pH drifting")

    assert out["held"] is True
    assert out["held_until"] == "07:00"
    assert "07:00" in out["note"]
    assert slack_out.posts == []
    assert slack_out.held == [
        {"topic": "pool", "text": "pH drifting", "question": False, "replaces_last": False}
    ]


async def test_an_urgent_post_bypasses_quiet_hours(registry, make_ctx, slack_out):
    slack_out.quiet = MORNING

    out = await call(registry, make_ctx(), topic="pool", text="leak!", urgent=True)

    assert out["action"] == "posted"
    assert slack_out.posts == [("pool", "leak!")]
    assert slack_out.held == []


async def test_a_reply_filip_is_waiting_on_bypasses_quiet_hours(registry, make_ctx, slack_out):
    slack_out.quiet = MORNING
    owed = {"pool"}

    await call(registry, make_ctx({"owed_replies": owed}), topic="pool", text="21 C")

    assert slack_out.posts == [("pool", "21 C")]
    assert owed == set()


async def test_a_bare_dm_waiting_lets_any_topic_through_quiet_hours(registry, make_ctx, slack_out):
    slack_out.quiet = MORNING

    await call(registry, make_ctx({"owed_replies": {ANY_TOPIC}}), topic="energy", text="4 kW")

    assert slack_out.posts == [("energy", "4 kW")]


async def test_an_unrelated_topic_is_still_held_while_another_is_owed(
    registry, make_ctx, slack_out
):
    slack_out.quiet = MORNING

    out = await call(registry, make_ctx({"owed_replies": {"pool"}}), topic="energy", text="x")

    assert out["held"] is True
    assert slack_out.posts == []


async def test_a_held_post_still_hits_the_rate_cap(registry, make_ctx, slack_out):
    slack_out.quiet = MORNING
    slack_out.raises = SlackRateCapped("slack post cap reached (20/h)")

    out = await call(registry, make_ctx(), topic="pool", text="hi")

    assert "cap reached" in out["error"]


# --- replaces_last ----------------------------------------------------------


async def test_replaces_last_edits_when_possible(registry, make_ctx, slack_out):
    slack_out.editable_ts = "7.1"

    out = await call(registry, make_ctx(), topic="pool", text="actually 22 C", replaces_last=True)

    assert out["action"] == "updated"
    assert out["ts"] == "7.1"
    assert slack_out.edits == [("pool", "actually 22 C")]
    assert slack_out.posts == []


async def test_replaces_last_falls_back_to_a_post_and_says_so(registry, make_ctx, slack_out):
    out = await call(registry, make_ctx(), topic="pool", text="actually 22 C", replaces_last=True)

    assert out["action"] == "posted"
    assert "new message" in out["note"]
    assert slack_out.posts == [("pool", "actually 22 C")]


async def test_a_plain_post_reports_posted(registry, make_ctx):
    out = await call(registry, make_ctx(), topic="pool", text="hi")
    assert out["action"] == "posted"
    assert "note" not in out


# --- questions --------------------------------------------------------------


async def test_question_is_passed_through_to_the_post(registry, make_ctx, slack_out):
    await call(registry, make_ctx(), topic="pool", text="Run the pump tonight?", question=True)
    assert slack_out.questions == ["pool"]


async def test_slack_open_questions_lists_what_slack_out_reports(registry, make_ctx, slack_out):
    slack_out.open = [
        {"topic": "pool", "asked_at": "2026-11-01T10:00:00+00:00", "age_days": 7.0, "text": "?"}
    ]

    out = json.loads(
        await registry.dispatch(make_ctx(), ToolCall(id="1", name="slack_open_questions", args={}))
    )

    assert out["open_questions"] == slack_out.open


def test_slack_open_questions_says_silence_is_not_a_no(registry):
    spec = registry._tools["slack_open_questions"].spec
    assert "NOT a no" in spec.description
    assert "5 days" in spec.description


def test_slack_post_documents_the_new_params(registry):
    props = registry._tools["slack_post"].spec.parameters["properties"]
    assert {"urgent", "replaces_last", "question"} <= set(props)
    assert "topic" in registry._tools["slack_post"].spec.parameters["required"]
