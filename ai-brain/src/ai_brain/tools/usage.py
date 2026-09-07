"""Self-insight: let an agent see its own token budget.

Every provider key is throttled by the shared ``Ledger`` already, but until
now a loop that got throttled had no way to find out why -- it just saw a
call blocked and moved on. This tool lets the model ask directly, so it can
reason about its own quota rather than only feel its effects.
"""

from __future__ import annotations

from ai_brain.ledger import Ledger
from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok


async def _usage_status(ctx: ToolContext, _args: dict) -> str:
    ledger = ctx.extras.get("ledger")
    if not isinstance(ledger, Ledger):
        return err("usage_status: ledger unavailable")
    return ok(ledger.usage())


def register_usage_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="usage_status",
                description=(
                    "See today's token and request usage against budget for every provider "
                    "key: how much of the daily request and token allowance is spent, what "
                    "fraction remains, and whether a key is currently rate-limited or disabled."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            fn=_usage_status,
            loops=None,
        )
    )
