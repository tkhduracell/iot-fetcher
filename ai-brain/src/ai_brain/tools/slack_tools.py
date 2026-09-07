"""The brain's voice. Experts have none -- they note the brain instead.

``slack_post`` writes into the agent session named by ``topic``, creating it on
first use. Every topic the brain touches in a cycle is recorded in
``ctx.extras["cycle_topics"]`` so the loop can flip those sessions from
``processing`` to ``active`` once the cycle ends: the dot means "the agent is
thinking about this right now", which only the loop knows when to clear.
"""

from __future__ import annotations

from ai_brain.llm import ToolSpec
from ai_brain.slack_io import SlackRateCapped
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

BRAIN_ONLY = frozenset({"brain"})


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
    ctx.extras.setdefault("cycle_topics", []).append(topic)
    return ok({"ts": ts})


async def _slack_close(ctx: ToolContext, args: dict) -> str:
    out = _out(ctx)
    if out is None:
        return err("slack disabled")
    await out.close(str(args["topic"]))
    return ok({"closed": args["topic"]})


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
                    "new one for a new subject. Capped per hour -- speak when it matters."
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
    registry.register(
        Tool(
            spec=ToolSpec(
                name="slack_close",
                description=(
                    "Close a Slack topic when it is finished, so it stops showing as live. "
                    "Posting to the same topic later reopens the conversation."
                ),
                parameters={
                    "type": "object",
                    "properties": {"topic": {"type": "string"}},
                    "required": ["topic"],
                },
            ),
            fn=_slack_close,
            loops=BRAIN_ONLY,
        )
    )
