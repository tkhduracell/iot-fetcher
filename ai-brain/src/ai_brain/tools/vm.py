"""VictoriaMetrics reads: run PromQL, and find out what metrics exist.

Both results are capped hard. A range query over a week at a fine step is a
megabyte of numbers the model cannot use and would pay for twice, so a series
keeps its most recent 200 points -- the recent end is the interesting one --
and a query keeps 20 series. Whenever anything was dropped the result says
``truncated: true`` so the model knows to narrow its query rather than trust a
partial picture.
"""

from __future__ import annotations

import re
import time

from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok
from ai_brain.tools.http import decode_json, request

VM_LOOPS = frozenset({"brain", "energy", "health"})

MAX_POINTS = 200
MAX_SERIES = 20
MAX_METRICS = 200
MAX_PATTERN_CHARS = 128


def _auth(ctx: ToolContext) -> dict[str, str]:
    token = ctx.settings.influx_token
    return {"Authorization": f"Bearer {token}"} if token else {}


def _series_from(result: list) -> tuple[list[dict], bool]:
    truncated = len(result) > MAX_SERIES
    series = []
    for entry in result[:MAX_SERIES]:
        # instant queries return a single "value", range queries a "values" list
        values = entry.get("values")
        if values is None:
            single = entry.get("value")
            values = [single] if single is not None else []
        if len(values) > MAX_POINTS:
            values = values[-MAX_POINTS:]
            truncated = True
        series.append({"metric": entry.get("metric", {}), "values": values})
    return series, truncated


async def _vm_query(ctx: ToolContext, args: dict) -> str:
    promql = str(args["promql"])
    range_minutes = int(args.get("range_minutes") or 0)
    step_seconds = int(args.get("step_seconds") or 300)
    base = ctx.settings.vm_url.rstrip("/")

    if range_minutes > 0:
        end = int(time.time())
        url = f"{base}/api/v1/query_range"
        params = {
            "query": promql,
            "start": str(end - range_minutes * 60),
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
    if body.get("status") != "success":
        return err(f"vm_query: {body.get('error') or 'query failed'}")

    series, truncated = _series_from(body.get("data", {}).get("result") or [])
    return ok({"series": series, "truncated": truncated})


async def _vm_metrics(ctx: ToolContext, args: dict) -> str:
    pattern = str(args["pattern"])
    # A long regex is a cheap way to make re spend a long time on 200 names.
    if len(pattern) > MAX_PATTERN_CHARS:
        return err(f"vm_metrics: pattern too long (max {MAX_PATTERN_CHARS})")
    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        return err(f"vm_metrics: invalid regex {pattern!r}: {exc}")

    url = f"{ctx.settings.vm_url.rstrip('/')}/api/v1/label/__name__/values"
    response, problem = await request(ctx, "GET", url, label="vm_metrics", headers=_auth(ctx))
    if problem is not None:
        return err(problem)

    body, problem = decode_json(response, "vm_metrics")
    if problem is not None:
        return err(problem)

    names = [name for name in (body.get("data") or []) if matcher.search(name)]
    return ok({"metrics": names[:MAX_METRICS], "truncated": len(names) > MAX_METRICS})


def register_vm_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="vm_query",
                description=(
                    "Run a PromQL query against VictoriaMetrics. Omit range_minutes for the "
                    "current value; set it to look back over that many minutes at step_seconds "
                    "resolution. Results are capped at 20 series and the newest 200 points each."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "promql": {"type": "string"},
                        "range_minutes": {"type": "integer"},
                        "step_seconds": {"type": "integer"},
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
