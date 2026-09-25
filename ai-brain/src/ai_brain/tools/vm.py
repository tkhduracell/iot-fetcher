"""VictoriaMetrics reads: run PromQL, and find out what metrics exist.

Results are shaped for small models with short context windows. VM's raw
``[[ts, "12.3456789"], ...]`` pairs cost ~25 chars a point, so a week-long
range over a few series blew past the loop's tool-result cap and arrived cut
mid-JSON. Instead:

* the model picks one of a fixed set of ``WINDOWS`` (2m..7d), each with a
  step that keeps it within ``MAX_POINTS`` -- the whole window, coarsely;
* each series is ``start``/``step`` plus a flat list of rounded numbers
  (``null`` for gaps), with min/max/avg/last precomputed so the model does no
  arithmetic over the list;
* labels shared by every series are hoisted into ``common_labels``;
* if the result still exceeds ``MAX_RESULT_CHARS`` the per-point values are
  dropped and only the stats kept (``values_dropped: true``).

``truncated: true`` still means series were dropped past ``MAX_SERIES`` -- a
cue to narrow the query (aggregate with ``sum by``/``topk``) rather than trust
a partial picture.
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ai_brain.llm import ToolSpec
from ai_brain.regex_safety import check_pattern
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok
from ai_brain.tools.http import decode_json, request

VM_LOOPS = frozenset({"brain", "energy", "health"})

# Fixed windows, each 10-28 buckets. Points multiply by series (one per label
# combination), so a tight per-series count is what keeps 20 series small.
# A small model picks a label reliably; free-form minutes/steps it got wrong.
WINDOWS: dict[str, tuple[int, int]] = {
    "now": (0, 0),
    "2m": (120, 10),  # 12 points
    "5m": (300, 30),  # 10
    "1h": (3600, 300),  # 12
    "6h": (6 * 3600, 1800),  # 12
    "1d": (86400, 3600),  # 24
    "7d": (7 * 86400, 6 * 3600),  # 28
}
MAX_POINTS = 28
# The house's clock. Models mis-convert UTC by hand (DST especially), so
# every time the model sees is already local, with the offset spelled out.
LOCAL_TZ = ZoneInfo("Europe/Stockholm")
STEP_LABELS = {10: "10s", 30: "30s", 300: "5m", 1800: "30m", 3600: "1h", 6 * 3600: "6h"}
MAX_SERIES = 20
MAX_METRICS = 200
# Well under loop.MAX_TOOL_RESULT_CHARS so the loop's blunt cut never fires.
MAX_RESULT_CHARS = 6_000


def _auth(ctx: ToolContext) -> dict[str, str]:
    token = ctx.settings.influx_token
    return {"Authorization": f"Bearer {token}"} if token else {}


def _num(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _round(value: float) -> float | int:
    """Four significant digits -- plenty for reasoning, a third of the chars."""
    if value == 0:
        return 0
    digits = max(0, 3 - int(math.floor(math.log10(abs(value)))))
    rounded = round(value, digits)
    return int(rounded) if rounded == int(rounded) and abs(rounded) < 1e15 else rounded


def _stats(values: list[float]) -> dict:
    if not values:
        return {}
    return {
        "min": _round(min(values)),
        "max": _round(max(values)),
        "avg": _round(sum(values) / len(values)),
        "last": _round(values[-1]),
    }


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=LOCAL_TZ)


def _times(start: int, step: int, n: int) -> list[str]:
    """Bucket labels aligned with each series' ``values``: ``HH:MM``, with the
    weekday prefixed when the window spans more than a day."""
    fmt = "%a %H:%M" if step * n > 86400 else "%H:%M:%S" if step < 60 else "%H:%M"
    return [_local(start + i * step).strftime(fmt) for i in range(n)]


def _hoist_labels(entries: list[dict]) -> tuple[dict, list[dict]]:
    labels = [
        e.get("metric") if isinstance(e.get("metric"), dict) else {} for e in entries
    ]
    if len(labels) < 2:
        return {}, labels
    common = {
        k: v for k, v in labels[0].items() if all(lab.get(k) == v for lab in labels[1:])
    }
    return common, [{k: v for k, v in lab.items() if k not in common} for lab in labels]


def _shape(
    result: list, start: int | None, step: int | None, window: str = "now"
) -> dict:
    entries = [e for e in result if isinstance(e, dict)]
    truncated = len(entries) > MAX_SERIES
    entries = entries[:MAX_SERIES]
    common, labels = _hoist_labels(entries)

    series = []
    for entry, own in zip(entries, labels):
        if start is None or step is None:
            single = entry.get("value")
            value = (
                _num(single[1])
                if isinstance(single, list) and len(single) == 2
                else None
            )
            series.append(
                {"labels": own, "value": None if value is None else _round(value)}
            )
            continue
        slots: list[float | None] = [None] * MAX_POINTS
        present: list[float] = []
        for point in entry.get("values") or []:
            if not (isinstance(point, list) and len(point) == 2):
                continue
            value = _num(point[1])
            idx = round((float(point[0]) - start) / step)
            if value is None or not 0 <= idx < MAX_POINTS:
                continue
            slots[idx] = value
        # trim trailing slots past the end of the window
        n = max((i + 1 for i, v in enumerate(slots) if v is not None), default=0)
        present = [v for v in slots[:n] if v is not None]
        series.append(
            {
                "labels": own,
                **_stats(present),
                "values": [None if v is None else _round(v) for v in slots[:n]],
            }
        )

    shaped: dict = {"series": series, "truncated": truncated}
    if start is None:
        shaped["at"] = _local(time.time()).isoformat(timespec="seconds")
    if common:
        shaped["common_labels"] = common
    if start is not None:
        n = max((len(x.get("values", [])) for x in series), default=0)
        shaped["window"] = window
        shaped["step"] = STEP_LABELS.get(step, f"{step}s")
        shaped["from"] = _local(start).isoformat(timespec="minutes")
        shaped["to"] = _local(start + WINDOWS[window][0] - step).isoformat(
            timespec="minutes"
        )
        shaped["times"] = _times(start, step, n)
        if len(json.dumps(shaped)) > MAX_RESULT_CHARS:
            for s in series:
                s.pop("values", None)
            shaped.pop("times")
            shaped["values_dropped"] = True
    return shaped


async def _vm_query(ctx: ToolContext, args: dict) -> str:
    promql = str(args["promql"])
    window = str(args.get("window") or "now")
    if window not in WINDOWS:
        return err(f"vm_query: window must be one of {', '.join(WINDOWS)}")
    base = ctx.settings.vm_url.rstrip("/")

    start: int | None = None
    step_seconds: int | None = None
    if window != "now":
        span, step_seconds = WINDOWS[window]
        end = int(time.time())
        end -= end % step_seconds  # aligned buckets: same window, same timestamps
        start = end - span + step_seconds
        url = f"{base}/api/v1/query_range"
        params = {
            "query": promql,
            "start": str(start),
            "end": str(end),
            "step": str(step_seconds),
        }
    else:
        url = f"{base}/api/v1/query"
        params = {"query": promql}

    response, problem = await request(
        ctx, "GET", url, label="vm_query", params=params, headers=_auth(ctx)
    )
    if problem is not None:
        return err(problem)

    body, problem = decode_json(response, "vm_query")
    if problem is not None:
        return err(problem)
    if not isinstance(body, dict):
        return err("vm_query: backend returned a non-object body")
    if body.get("status") != "success":
        return err(f"vm_query: {body.get('error') or 'query failed'}")

    data = body.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    return ok(
        _shape(
            result if isinstance(result, list) else [],
            start,
            step_seconds,
            window,
        )
    )


async def _vm_metrics(ctx: ToolContext, args: dict) -> str:
    pattern = str(args["pattern"])
    # A long or catastrophically-backtracking regex is a cheap way to make
    # re spend a long time on 200 names -- see regex_safety.py.
    problem = check_pattern(pattern)
    if problem is not None:
        return err(f"vm_metrics: {problem}")
    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        return err(f"vm_metrics: invalid regex {pattern!r}: {exc}")

    url = f"{ctx.settings.vm_url.rstrip('/')}/api/v1/label/__name__/values"
    response, problem = await request(
        ctx, "GET", url, label="vm_metrics", headers=_auth(ctx)
    )
    if problem is not None:
        return err(problem)

    body, problem = decode_json(response, "vm_metrics")
    if problem is not None:
        return err(problem)

    if not isinstance(body, dict):
        return err("vm_metrics: backend returned a non-object body")
    data = body.get("data")
    candidates = [
        name
        for name in (data if isinstance(data, list) else [])
        if isinstance(name, str)
    ]

    names = [name for name in candidates if matcher.search(name)]
    return ok({"metrics": names[:MAX_METRICS], "truncated": len(names) > MAX_METRICS})


def register_vm_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="vm_query",
                description=(
                    "Run a PromQL query against VictoriaMetrics. window='now' (default) gives "
                    "the current value; 2m/5m/1h/6h/1d/7d give the whole window in <=28 "
                    "buckets (10s/30s/5m/30m/1h/6h) with min/max/avg/last per series. Max 20 "
                    "series -- aggregate with sum by/avg by/topk instead of raw "
                    "high-cardinality metrics."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "promql": {"type": "string"},
                        "window": {"type": "string", "enum": list(WINDOWS)},
                    },
                    "required": ["promql"],
                },
            ),
            fn=_vm_query,
            loops=VM_LOOPS,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="vm_metrics",
                description=(
                    "List metric names matching a regular expression. Use it to find the exact "
                    "name of a metric before querying it -- a query on a name that does not "
                    "exist returns empty rather than an error."
                ),
                parameters={
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
            ),
            fn=_vm_metrics,
            loops=VM_LOOPS,
        )
    )
