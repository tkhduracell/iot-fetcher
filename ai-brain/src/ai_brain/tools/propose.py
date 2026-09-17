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

from ai_brain.approvals import KINDS, PENDING
from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok

BRAIN_ONLY = frozenset({"brain"})

# How many of the most recent terminal (executed/rejected/failed/expired)
# proposals list_proposals shows alongside every pending one. Unbounded would
# mean an outbox that has been running for weeks turns one call into the
# whole history; this is enough to notice "I already asked about this
# yesterday" without that.
RECENT_TERMINAL_LIMIT = 20


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
    except RuntimeError as exc:
        # Slack was unreachable, so there is no message for a human to react
        # to and nothing was left pending. Saying so plainly is what lets the
        # model try again on a later cycle instead of assuming it asked.
        return err(f"{exc}; nothing was proposed, try again on a later cycle")
    return ok({"id": proposal.id, "status": proposal.status})


async def _list_proposals(ctx: ToolContext, args: dict) -> str:
    approvals = ctx.extras.get("approvals")
    if approvals is None:
        return err("approvals are not configured in this process")

    all_proposals = approvals.all()
    pending = [p for p in all_proposals if p.status == PENDING]
    terminal = [p for p in all_proposals if p.status != PENDING][-RECENT_TERMINAL_LIMIT:]

    def _brief(p) -> dict:
        return {
            "id": p.id,
            "kind": p.kind,
            "payload": p.payload,
            "reason": p.reason,
            "status": p.status,
            "created": p.created,
        }

    return ok(
        {
            "pending": [_brief(p) for p in pending],
            "recent": [_brief(p) for p in terminal],
        }
    )


def register_propose_tool(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="propose",
                description=(
                    "Ask a human to approve one physical action. Nothing happens until they "
                    "react in Slack, so this returns 'pending' -- never report the action as "
                    "done. The outcome arrives as an inbox note on a later cycle. "
                    "Check list_proposals first if the thing you are about to ask for sounds "
                    "like something you may have already asked -- a pending proposal is still "
                    "waiting on a human, and asking again just duplicates the Slack message; a "
                    "recently rejected one probably should not be re-asked either. "
                    "Kinds: sonos_say (payload {\"text\": ...}, speaks aloud, refused between "
                    "22:00 and 07:00), ha_todo_add (payload {\"item\": ...}) and ha_service "
                    "(payload {\"service\": \"light.turn_off\", \"entity_id\": \"light.kitchen\", "
                    "optional \"data\": {\"brightness_pct\": 40}}) for lights, switches, scenes, "
                    "scripts, covers, fans, climate and media players -- look the entity up with "
                    "ha_state first, and expect anything outside that allowlist to be refused."
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
                            "description": (
                                "Short subject naming this proposal, e.g. 'roborock' or "
                                "'pool-pump'. Never 'chat' -- that thread is reserved for "
                                "Filip's own open conversation with you and is repointed "
                                "every time he starts a new one."
                            ),
                        },
                    },
                    "required": ["kind", "payload", "reason", "topic"],
                },
            ),
            fn=_propose,
            loops=BRAIN_ONLY,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="list_proposals",
                description=(
                    "See every proposal you currently have pending, plus your most recent "
                    "resolved ones (executed, rejected, failed, blocked, expired). Call this "
                    "before propose when what you are about to ask for might be something "
                    "you already asked -- reworded or not, the point is the physical thing "
                    "being requested, not the exact wording. Asking again while the same "
                    "request is still pending just posts a second, redundant Slack message a "
                    "human has to react to."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            fn=_list_proposals,
            loops=BRAIN_ONLY,
        )
    )
