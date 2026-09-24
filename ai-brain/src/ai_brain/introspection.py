"""Pure rendering logic shared by the HTTP API and the brain's own tools.

``api.py`` renders these for a human over HTTP; ``tools/introspect.py`` renders
the same numbers for the model itself, inside ``system_status``. Splitting the
logic out here means both read one implementation of "what does a loop's cycle
history mean" and "what does a topic's proposal history mean" -- the buckets in
``api.py``'s module docstring apply here unchanged.

Every function takes the plain objects it needs (``loops``, ``approvals``, a
list of ``AgentLoop``) rather than a whole ``System``, so a tool can call them
with exactly what ``ToolContext.extras`` hands it, and a test can call them
with a handful of fakes instead of building a full supervisor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ai_brain.approvals import Approvals
    from ai_brain.loop import AgentLoop

# See api.py's module docstring for what each bucket means -- CYCLE_BUCKETS
# rolls loop.py's Status literal into four groups by what a cycle actually
# left behind, and BUCKETS is their fixed render order.
CYCLE_BUCKETS: dict[str, str] = {
    "ok": "real",
    "max_rounds": "repeat",
    "error": "note",
    "timeout": "note",
    "no_budget": "nothing",
    "paused": "nothing",
    "cancelled": "nothing",
}
BUCKETS = ("nothing", "note", "real", "repeat")

# Same subset-of-laps reasoning as api.py: `failed` and `expired` are neither
# a checkmark nor a cross, so they count towards laps but not approved/rejected.
APPROVED_STATUSES = frozenset({"executing", "executed", "blocked_quiet_hours"})
REJECTED_STATUSES = frozenset({"rejected"})


def usefulness_rows(loops: dict[str, AgentLoop]) -> dict:
    """Every loop's cycles, rolled into the four buckets above.

    Brain first, then the rest alphabetically -- the same order ``api.py``'s
    ``agent_names`` uses, so a reader of either sees loops in the same order.
    """
    rows = []
    totals = {"total": 0, **dict.fromkeys(BUCKETS, 0)}
    for name in ["brain"] + sorted(n for n in loops if n != "brain"):
        row = {"name": name, "total": 0, **dict.fromkeys(BUCKETS, 0)}
        for status, count in loops[name].cycle_counts.items():
            bucket = CYCLE_BUCKETS.get(status, "nothing")
            row[bucket] += count
            row["total"] += count
        for key in ("total", *BUCKETS):
            totals[key] += row[key]
        rows.append(row)
    return {"loops": rows, "totals": totals}


def proposal_loops(approvals: Approvals) -> dict:
    """Proposals grouped by topic -- the same subject, proposed again and again.

    See ``api.py``'s ``_loops_json`` docstring: a topic is how the agent names
    what a proposal is about, so this is where "four rejected ha_todo_add on
    one topic" becomes visible.
    """
    groups: dict[str, list] = {}
    for proposal in approvals.all():
        groups.setdefault(proposal.topic, []).append(proposal)

    loops = []
    for topic, proposals in groups.items():
        loops.append(
            {
                "topic": topic,
                "laps": len(proposals),
                "first_at": proposals[0].created,
                "last_at": proposals[-1].created,
                "pending": sum(1 for p in proposals if p.status == "pending"),
                "approved": sum(1 for p in proposals if p.status in APPROVED_STATUSES),
                "rejected": sum(1 for p in proposals if p.status in REJECTED_STATUSES),
                "kinds": sorted({p.kind for p in proposals}),
                "proposals": [
                    {
                        "id": p.id,
                        "kind": p.kind,
                        "created": p.created,
                        "status": p.status,
                        "result": p.result,
                    }
                    for p in proposals
                ],
            }
        )
    loops.sort(key=lambda loop: (-loop["laps"], loop["topic"]))
    return {"loops": loops}
