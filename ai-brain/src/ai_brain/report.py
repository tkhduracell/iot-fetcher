"""What the cycle guards are actually doing, read from the session logs.

The loop is full of guards added against some model's bad day: round caps, the
wrap-up nudge, the junk-fact refusal, the rotating angles. Any of them may be
pulling its weight or may be dead weight on a stronger model, and nothing could
say which. Whether to remove one is a decision for later, on this report's data.
It decides nothing itself.

    make report
    sudo docker exec ai-brain /app/.venv/bin/python -m ai_brain.report --days 7

Reads ``MEMORY_ROOT`` (or ``--memory``). Read-only.

A cycle's rounds go to the model that answered them. Everything said about a
cycle as a whole -- how it ended, whether the nudge worked -- goes to the model
that answered its last round, since that is who it was up to by then.

"How often each angle was taken" cannot be read off directly, because a model
never says. An angle's offered count is exact. Its taken count is a proxy, the
tool that angle is hard to follow without (``ANGLE_SIGNALS``), and reads "-" for
the angles no tool gives away. Both are counted against the angle text as logged,
so they can only overstate: a cycle that calls ``review_expert`` for another
reason still counts. A cycle that never got a reply had no chance to follow
anything, so it counts as neither.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_brain.sessions import SESSIONS_DIRNAME, cycle_logs, read_events, result_error

# Angle (by the start of its text) -> a tool call that shows it was followed.
# None: following it leaves no tool signature of its own. A name after a colon
# means that tool with that ``name`` argument.
ANGLE_SIGNALS: dict[str, frozenset[str] | None] = {
    "Review one expert": frozenset({"review_expert"}),
    "Hunt contradictions": frozenset({"review_expert", "read_expert"}),
    "Check ground truth": frozenset({"review_expert"}),
    "Find re-litigation": frozenset({"review_expert"}),
    "Judge method": frozenset({"review_expert"}),
    "Tend your own memory": frozenset({"delete_fact", "write_fact"}),
    "Understand the machine": frozenset({"code_overview", "code_read", "code_grep", "code_list"}),
    "Judge yourself": frozenset({"write_fact:self_review"}),
    "Find the thing that looks wrong": None,
    "Build a baseline": frozenset({"write_fact"}),
    "Pick up an open thread": None,
    "Look at something": None,
    "Send the brain": frozenset({"send_note"}),
}


@dataclass
class CycleSummary:
    agent: str
    status: str = "unfinished"
    last_model: str = ""
    rounds_by_model: Counter[str] = field(default_factory=Counter)
    rounds: int = 0
    cap: int = 0
    nudged: bool = False
    ended_after_nudge: bool = False
    ended: bool = False
    junk_refusals: Counter[str] = field(default_factory=Counter)
    # Calls whose prompt lost at least one tool result, by the model it was for.
    # Not whichever model finished the cycle: that may be one that never trims.
    trims_by_model: Counter[str] = field(default_factory=Counter)
    resumes: int = 0
    angle: str = ""
    # Tool names called, plus ``tool:name`` for a call with a ``name`` argument.
    tools: set[str] = field(default_factory=set)


def summarize(agent: str, events: Iterable[dict]) -> CycleSummary:
    """One session's events as what the report wants to know about it."""
    out = CycleSummary(agent=agent)
    model = ""
    for event in events:
        kind = event.get("type")
        if kind == "opening":
            out.angle = str(event.get("angle") or "")
        elif kind == "reply":
            model = str(event.get("chain_key") or event.get("model") or "")
            out.last_model = model
            out.rounds_by_model[model] += 1
            out.rounds += 1
            for call in event.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                name = str(call.get("name") or "")
                out.tools.add(name)
                if "name" in args:
                    out.tools.add(f"{name}:{args['name']}")
        elif kind == "tool_result":
            name = str(event.get("name") or "")
            if name not in ("write_fact", "end_cycle"):
                continue
            failed = result_error(str(event.get("result") or ""))
            if name == "write_fact" and failed and failed.startswith("refused:"):
                out.junk_refusals[model] += 1
            if name == "end_cycle" and not event.get("synthetic") and failed is None:
                out.ended = True
                out.ended_after_nudge = out.nudged
        elif kind == "message" and event.get("kind") == "nudge":
            out.nudged = True
        elif kind == "context_fit" and event.get("trimmed"):
            out.trims_by_model[str(event.get("chain_key") or "")] += 1
        elif kind == "resume":
            out.resumes += 1
        elif kind == "cycle_end":
            out.status = str(event.get("status") or "unknown")
            out.cap = int(event.get("cap") or 0)
    return out


def _signal_for(angle: str) -> tuple[str, frozenset[str] | None]:
    for prefix, signal in ANGLE_SIGNALS.items():
        if angle.startswith(prefix):
            return prefix, signal
    return angle[:40], None


def agent_session_dirs(memory_root: Path) -> dict[str, Path]:
    dirs = {"brain": memory_root / "brain" / SESSIONS_DIRNAME}
    experts = memory_root / "experts"
    if experts.exists():
        for path in sorted(experts.iterdir()):
            if path.is_dir():
                dirs[path.name] = path / SESSIONS_DIRNAME
    return dirs


def load_cycles(
    memory_root: Path, *, days: int | None = None, now: float | None = None
) -> list[CycleSummary]:
    since = None
    if days is not None:
        at = datetime.fromtimestamp(now, UTC) if now is not None else datetime.now(UTC)
        since = at - timedelta(days=days)
    cycles = []
    for agent, directory in agent_session_dirs(memory_root).items():
        for path in cycle_logs(directory, since=since):
            events = read_events(path)
            if events:
                cycles.append(summarize(agent, events))
    return cycles


def build_report(cycles: list[CycleSummary]) -> dict:
    """The numbers, keyed for ``--json`` and for the tables below."""
    finished_on: dict[str, list[CycleSummary]] = {}
    for cycle in cycles:
        finished_on.setdefault(cycle.last_model or "(no reply)", []).append(cycle)
    answered = Counter()
    junk = Counter()
    trims = Counter()
    for cycle in cycles:
        answered.update(cycle.rounds_by_model)
        junk.update(cycle.junk_refusals)
        trims.update(cycle.trims_by_model)

    models = {}
    for model in sorted(set(finished_on) | set(answered) | set(trims)):
        ended_here = finished_on.get(model, [])
        rounds = [c.rounds for c in ended_here]
        nudged = [c for c in ended_here if c.nudged]
        models[model] = {
            "cycles": len(ended_here),
            "rounds_answered": answered[model],
            "rounds_per_cycle": {
                "median": statistics.median(rounds) if rounds else 0,
                "mean": round(statistics.fmean(rounds), 1) if rounds else 0,
                "max": max(rounds, default=0),
            },
            "at_cap": sum(1 for c in ended_here if c.cap and c.rounds >= c.cap),
            "nudge_fired": len(nudged),
            "end_cycle_after_nudge": sum(1 for c in nudged if c.ended_after_nudge),
            "junk_fact_refusals": junk[model],
            "context_trims": trims[model],
            "resumes": sum(c.resumes for c in ended_here),
            "status": dict(Counter(c.status for c in ended_here)),
        }

    angles: dict[str, dict] = {}
    for cycle in cycles:
        if not cycle.angle or not cycle.rounds:
            continue
        label, signal = _signal_for(cycle.angle)
        row = angles.setdefault(label, {"offered": 0, "taken": 0 if signal else None})
        row["offered"] += 1
        if signal and cycle.tools & signal:
            row["taken"] += 1
    return {"cycles": len(cycles), "models": models, "angles": angles}


def render(report: dict) -> str:
    lines = [f"{report['cycles']} cycle(s)", ""]
    headers = [
        "model",
        "cycles",
        "rounds",
        "med",
        "mean",
        "max",
        "at cap",
        "nudged",
        "ended after",
        "junk refused",
        "trims",
        "resumes",
    ]
    rows = [
        [
            model,
            row["cycles"],
            row["rounds_answered"],
            row["rounds_per_cycle"]["median"],
            row["rounds_per_cycle"]["mean"],
            row["rounds_per_cycle"]["max"],
            row["at_cap"],
            row["nudge_fired"],
            row["end_cycle_after_nudge"],
            row["junk_fact_refusals"],
            row["context_trims"],
            row["resumes"],
        ]
        for model, row in report["models"].items()
    ]
    lines += _table(headers, rows)
    lines.append("")
    lines.append("status by final model:")
    for model, row in report["models"].items():
        if row["status"]:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(row["status"].items()))
            lines.append(f"  {model}: {parts}")
    lines.append("")
    lines += _table(
        ["angle", "offered", "taken (proxy)"],
        [
            [label, row["offered"], "-" if row["taken"] is None else row["taken"]]
            for label, row in sorted(report["angles"].items())
        ],
    )
    return "\n".join(lines)


def _table(headers: list[str], rows: list[list]) -> list[str]:
    cells = [headers] + [[str(c) for c in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    return ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in cells]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-model numbers from the session logs.")
    parser.add_argument("--memory", type=Path, default=Path(os.environ.get("MEMORY_ROOT", "/memory")))
    parser.add_argument("--days", type=int, default=None, help="only cycles this recent")
    parser.add_argument("--json", action="store_true", help="the numbers as JSON")
    args = parser.parse_args(argv)
    report = build_report(load_cycles(args.memory, days=args.days))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
