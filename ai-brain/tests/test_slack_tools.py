import json

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
        self._sessions: dict[str, str] = {}  # topic -> thread_ts, insertion order
        self.raises: Exception | None = None
        self.next_ts = 0

    async def post(self, topic: str, text: str) -> str:
        if self.raises is not None:
            raise self.raises
        resolved = self.resolve_topic(topic)
        self.posts.append((resolved, text))
        self.next_ts += 1
        ts = f"{self.next_ts}.1"
        self._sessions.setdefault(resolved, ts)
        return ts

    def resolve_topic(self, topic: str) -> str:
        if topic in self._sessions:
            return topic
        from ai_brain.slack_io import _shares_dash_prefix, normalize_topic

        normalized = normalize_topic(topic)
        for existing in self._sessions:
            if normalize_topic(existing) == normalized or _shares_dash_prefix(
                normalized, normalize_topic(existing)
            ):
                return existing
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
