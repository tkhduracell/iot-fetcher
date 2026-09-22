"""The brain's voice. Experts have none -- they note the brain instead.

``slack_post`` writes into the plain thread named by ``topic``, creating it
on first use.

Filip's notes are owed a reply. ``owed_replies`` names the topics a cycle's
inbox is waiting on (``ANY_TOPIC`` for a bare DM with no thread yet, which any
post answers); the loop parks that set in ``ctx.extras["owed_replies"]``,
``slack_post`` ticks topics off it, and ``end_cycle`` refuses -- once -- while
it is still non-empty. A note that is only a statement or a correction used to
read to the model as needing no answer, so it updated a fact, ended the cycle
quietly, and left Filip looking at a spinner that eventually turned red.
"""

from __future__ import annotations

from ai_brain.llm import ToolSpec
from ai_brain.memory import Note
from ai_brain.slack_io import SlackRateCapped
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

BRAIN_ONLY = frozenset({"brain"})
NOTE_SENDER = "filip"
ANY_TOPIC = "*"


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


def _out(ctx: ToolContext):
    return ctx.extras.get("slack_out")


async def _slack_post(ctx: ToolContext, args: dict) -> str:
    out = _out(ctx)
    if out is None:
        return err("slack disabled")
    topic = str(args["topic"])
    try:
        ts = await out.post(topic, str(args["text"]))
    except SlackRateCapped as exc:
        return err(str(exc))
    owed = ctx.extras.get("owed_replies")
    if owed:
        owed.discard(topic)
        owed.discard(ANY_TOPIC)
    return ok({"ts": ts})


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
                    "new one for a new subject. The 'chat' topic is the exception: it is his "
                    "own open conversation with you, so use it only to reply to a note whose "
                    "`topic:` line already says chat -- never to start something new, since "
                    "it gets repointed to whatever thread he last opened, not the one this "
                    "reply landed in. Capped per hour -- speak when it matters."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Short subject, e.g. 'pool' or 'energy-prices'.",
                        },
                        "text": {"type": "string"},
                    },
                    "required": ["topic", "text"],
                },
            ),
            fn=_slack_post,
            loops=BRAIN_ONLY,
        )
    )
