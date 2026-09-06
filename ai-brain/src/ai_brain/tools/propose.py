"""The brain's only way to ask for something to happen in the physical house.

It does not act. It files a request that a human approves in Slack, and comes
straight back with ``status: pending`` so the model knows the speaker has not
spoken yet and should not say it has. The outcome arrives later as an inbox
note, on a future cycle.

Experts do not get this tool: the registry's allowlist refuses them, so an
expert that wants the house to do something has to send the brain a note and
let the brain decide whether it is worth a human's attention.
"""

from __future__ import annotations

from ai_brain.approvals import KINDS
from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

BRAIN_ONLY = frozenset({"brain"})


async def _propose(ctx: ToolContext, args: dict) -> str:
    approvals = ctx.extras.get("approvals")
    if approvals is None:
        return err("approvals are not configured in this process")

    payload = args["payload"]
    if not isinstance(payload, dict):
        return err(f"payload must be an object, got {type(payload).__name__}")

    try:
        proposal = await approvals.propose(
            str(args["kind"]),
            payload,
            str(args["reason"]),
            str(args["topic"]),
        )
    except ValueError as exc:
        return err(str(exc))
    return ok({"id": proposal.id, "status": proposal.status})


def register_propose_tool(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="propose",
                description=(
                    "Ask a human to approve one physical action. Nothing happens until they "
                    "react in Slack, so this returns 'pending' -- never report the action as "
                    "done. The outcome arrives as an inbox note on a later cycle. "
                    "Kinds: sonos_say (payload {\"text\": ...}, speaks aloud, refused between "
                    "22:00 and 07:00) and ha_todo_add (payload {\"item\": ...})."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": sorted(KINDS)},
                        "payload": {"type": "object"},
                        "reason": {
                            "type": "string",
                            "description": "Why you are asking, in one line, for the human.",
                        },
                        "topic": {
                            "type": "string",
                            "description": "Slack channel or thread to ask in.",
                        },
                    },
                    "required": ["kind", "payload", "reason", "topic"],
                },
            ),
            fn=_propose,
            loops=BRAIN_ONLY,
        )
    )
