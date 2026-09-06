"""Home Assistant state reads.

HA has no server-side search over ``/api/states``, so this fetches the whole
list and filters locally on entity id or friendly name. Friendly names are
typed by a human into a UI, which makes them external text: they are wrapped
before they reach the model.
"""

from __future__ import annotations

from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok, wrap_external
from ai_brain.tools.http import decode_json, request

HA_LOOPS = frozenset({"brain", "house-ops"})

MAX_ENTITIES = 50
SOURCE = "home-assistant"


async def _ha_state(ctx: ToolContext, args: dict) -> str:
    needle = str(args["query"]).lower()
    url = f"{ctx.settings.ha_url.rstrip('/')}/api/states"
    headers = {"Authorization": f"Bearer {ctx.settings.ha_token}"} if ctx.settings.ha_token else {}

    response, problem = await request(ctx, "GET", url, label="ha_state", headers=headers)
    if problem is not None:
        return err(problem)

    body, problem = decode_json(response, "ha_state")
    if problem is not None:
        return err(problem)

    matches = []
    for state in body if isinstance(body, list) else []:
        attributes = state.get("attributes") or {}
        friendly = attributes.get("friendly_name") or ""
        entity_id = state.get("entity_id") or ""
        if needle not in entity_id.lower() and needle not in str(friendly).lower():
            continue
        matches.append(
            {
                "entity_id": entity_id,
                "state": state.get("state"),
                "friendly_name": wrap_external(SOURCE, str(friendly)) if friendly else None,
                "last_changed": state.get("last_changed"),
                "unit_of_measurement": attributes.get("unit_of_measurement"),
            }
        )

    return ok({"entities": matches[:MAX_ENTITIES], "truncated": len(matches) > MAX_ENTITIES})


def register_ha_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="ha_state",
                description=(
                    "Look up Home Assistant entities whose entity id or friendly name contains "
                    "the query (case-insensitive), with their current state. Capped at 50 "
                    "matches, so search for something specific like 'pool' rather than 'sensor'."
                ),
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            fn=_ha_state,
            loops=HA_LOOPS,
        )
    )
