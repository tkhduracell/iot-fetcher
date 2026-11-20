"""The brain's voice. Experts have none -- they note the brain instead.

``slack_post`` writes into the plain thread named by ``topic``, creating it
on first use -- or, if ``topic`` normalizes to (or shares a '-'-boundary
prefix with) an existing session's topic, into that thread instead. See
``SlackOut.resolve_topic``: this is what keeps ``pool-pump-bug`` from opening
a second thread next to an existing ``pool-pump``. Every result names the
topic it actually posted under and the handful of other topics currently
active, so a model that is about to invent a new name sees what already
exists first.

Filip's notes are owed a reply. ``owed_replies`` names the topics a cycle's
inbox is waiting on (``ANY_TOPIC`` for a bare DM with no thread yet, which any
post answers); the loop parks that set in ``ctx.extras["owed_replies"]``,
``slack_post`` ticks topics off it, and ``end_cycle`` refuses -- once -- while
it is still non-empty. A note that is only a statement or a correction used to
read to the model as needing no answer, so it updated a fact, ended the cycle
quietly, and left Filip looking at a spinner that eventually turned red.

The outbox policy (see ``slack_io``'s module docstring for the week that
prompted it) is split: ``SlackOut`` knows *how* to hold, edit, broadcast and
track a question; this tool decides *whether* a post is held. Only
``slack_post`` is subject to quiet hours -- approval requests and the Airbnb
reminder call ``SlackOut.post`` directly and go out when they are raised.
A post bypasses quiet hours when it is ``urgent`` or when it answers a note
Filip is waiting on: he wrote at 01:00, so he is awake, and holding his reply
until morning is the same unanswered spinner ``owed_replies`` exists to stop.
"""

from __future__ import annotations

from ai_brain.config import Settings
from ai_brain.llm import ToolSpec
from ai_brain.memory import OWNER_SENDER, Note
from ai_brain.slack_io import SlackRateCapped, SlackOut
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

BRAIN_ONLY = frozenset({"brain"})
NOTE_SENDER = OWNER_SENDER
ANY_TOPIC = "*"


def _slack_configured(settings: Settings) -> bool:
    """Mirrors ``api.py``'s own "is Slack connected" check: both tokens set.

    This is the same thing ``_out`` checks at call time via ``ctx.extras``
    (``slack_out`` is only built when both are present -- see supervisor.py),
    but ``available`` only ever sees ``Settings``, not the running system, so
    it re-derives the same answer from the tokens the connection is built
    from rather than the connection object itself.
    """
    return bool(settings.slack_bot_token and settings.slack_app_token)


def owed_replies(notes: list[Note]) -> set[str]:
    """The topics Filip is waiting to hear back on, from this cycle's inbox.

    ``SlackIn.on_message`` prefixes a threaded note's body with a
    ``topic: <name>`` line; a bare top-level DM has none, so any post answers
    it (``ANY_TOPIC``).
    """
    owed: set[str] = set()
    for note in notes:
        if note.sender != NOTE_SENDER:
            continue
        first = note.body.lstrip().split("\n", 1)[0]
        if first.startswith("topic:"):
            owed.add(first.removeprefix("topic:").strip() or ANY_TOPIC)
        else:
            owed.add(ANY_TOPIC)
    return owed


def tick_off(owed: set[str], topic: str) -> None:
    """What a post on ``topic`` answers: that topic, and any bare DM."""
    owed.discard(topic)
    owed.discard(ANY_TOPIC)


def _out(ctx: ToolContext):
    return ctx.extras.get("slack_out")


def _owes(owed: set[str] | None, topic: str) -> bool:
    """Whether a post on ``topic`` answers a note Filip is waiting on."""
    return bool(owed) and (topic in owed or ANY_TOPIC in owed)


async def _slack_post(ctx: ToolContext, args: dict) -> str:
    out = _out(ctx)
    if out is None:
        return err("slack disabled")
    topic = str(args["topic"])
    text = str(args["text"])
    urgent = bool(args.get("urgent"))
    question = bool(args.get("question"))
    replaces_last = bool(args.get("replaces_last"))
    owed = ctx.extras.get("owed_replies")
    try:
        if not urgent and not _owes(owed, topic):
            until = out.quiet_until()
            if until is not None:
                held = out.hold(topic, text, question=question, replaces_last=replaces_last)
                return ok(
                    {
                        "held": True,
                        "held_until": until.strftime("%H:%M"),
                        "replaced_held": held["replaced_held"],
                        "topic": held["topic"],
                        "note": (
                            f"Quiet hours: held until {until:%H:%M} Stockholm time, then sent "
                            "in one morning digest. Set urgent=true only if it cannot wait."
                        ),
                        "active_topics": out.recent_topics(),
                    }
                )
        ts = None
        if replaces_last:
            ts = await out.edit_last(topic, text, question=question)
        action = "updated" if ts else "posted"
        if ts is None:
            ts = await out.post(topic, text, question=question)
            if ts == "queued":
                action = "queued"
    except SlackRateCapped as exc:
        return err(str(exc))
    # owed_replies is built from the inbox note's own `topic:` line, never
    # from a session -- discard against what was asked for, not wherever
    # resolve_topic (inside out.post) actually routed the reply.
    if owed:
        tick_off(owed, topic)
    # resolve_topic runs again here (post() already ran it once, internally)
    # purely to report back where the message actually landed -- cheap, since
    # it is just a sessions.json read, and the alternative is threading the
    # resolved topic out of post() for every caller that does not need it.
    resolved = out.resolve_topic(topic)
    result = {"ts": ts, "action": action, "topic": resolved, "active_topics": out.recent_topics()}
    if replaces_last and action != "updated":
        result["note"] = (
            "nothing recent to edit (no bot message under 24h old, or Filip has "
            "replied since), so this was sent as a new message"
        )
    return ok(result)


async def _slack_open_questions(ctx: ToolContext, args: dict) -> str:
    out: SlackOut | None = _out(ctx)
    if out is None:
        return err("slack disabled")
    return ok({"open_questions": out.open_questions()})


def register_slack_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="slack_post",
                description=(
                    "Say something to Filip in Slack. This is the only way Filip hears "
                    "from you -- your journal and facts are private, he never reads them, "
                    "so answering him there reaches nobody. Each topic is its own threaded "
                    "conversation, so reuse the same short topic for follow-ups and pick a "
                    "new one for a new subject -- a close variant of an existing topic "
                    "(e.g. 'pool-pump-bug' when 'pool-pump' already exists) is routed into "
                    "that existing thread automatically, but prefer typing the existing name "
                    "outright. The result names every currently active topic; check it before "
                    "picking a new one. The 'chat' topic is the exception: it is his "
                    "own open conversation with you, so use it only to reply to a note whose "
                    "`topic:` line already says chat -- never to start something new, since "
                    "it gets repointed to whatever thread he last opened, not the one this "
                    "reply landed in. Capped per hour -- speak when it matters.\n\n"
                    "Between 22:00 and 07:00 Stockholm time a post is held and sent in one "
                    "morning digest unless it is urgent or a reply to a note Filip sent; the "
                    "result says when. Fixing or refining something you just said? Set "
                    "replaces_last instead of posting again -- several messages for one "
                    "finding read as noise. Asking him something he can answer yes/no? Set "
                    "question: it adds Yes/No buttons and tracks the question until he "
                    "answers (see slack_open_questions)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Short subject, e.g. 'pool' or 'energy-prices'.",
                        },
                        "text": {"type": "string"},
                        "urgent": {
                            "type": "boolean",
                            "description": (
                                "Send now even during quiet hours (22-07). Only for things "
                                "that cannot wait until morning: a leak, an outage, a safety "
                                "issue. Default false."
                            ),
                        },
                        "replaces_last": {
                            "type": "boolean",
                            "description": (
                                "This corrects or supersedes your last message in this topic: "
                                "edit that message (marked as updated) instead of posting a "
                                "new one. Falls back to a new post if your last message is "
                                "over 24h old or Filip has replied since. Default false."
                            ),
                        },
                        "question": {
                            "type": "boolean",
                            "description": (
                                "This asks Filip a yes/no question: adds Yes/No buttons and "
                                "tracks it as open until he answers. Default false."
                            ),
                        },
                    },
                    "required": ["topic", "text"],
                },
            ),
            fn=_slack_post,
            loops=BRAIN_ONLY,
            available=_slack_configured,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="slack_open_questions",
                description=(
                    "List questions you asked Filip in Slack (slack_post with question=true) "
                    "that he has not answered yet: topic, when asked, age in days and an "
                    "excerpt. An unanswered question is NOT a no and NOT a sign the subject "
                    "is low priority -- he may simply not have seen it. Do not act on "
                    "silence as if it were an answer. Once a question has gone 5 days "
                    "without an answer you may ask again, once: combine every such question "
                    "into a single digest-style slack_post (one bullet each) rather than "
                    "re-posting each in its own thread."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            fn=_slack_open_questions,
            loops=BRAIN_ONLY,
            available=_slack_configured,
        )
    )
