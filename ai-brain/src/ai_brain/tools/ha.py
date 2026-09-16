"""Home Assistant state and log reads.

HA has no server-side search over ``/api/states``, so this fetches the whole
list and filters locally on entity id or friendly name. Friendly names are
typed by a human into a UI, which makes them external text: they are wrapped
before they reach the model.
"""

from __future__ import annotations

from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok, wrap_external
from ai_brain.tools.http import decode_json, request, stream_tail

HA_LOOPS = frozenset({"brain", "house-ops"})

MAX_ENTITIES = 50
SOURCE = "home-assistant"

# ``/api/error_log`` is the whole log file, which on a box that has been up for
# weeks is megabytes. Only the tail is worth reading, and this is how much of
# it the process will hold to find it.
LOG_TAIL_BYTES = 256 * 1024
DEFAULT_LOG_LINES = 50
MAX_LOG_LINES = 200
MAX_LOG_CONTEXT = 10

# What grep prints between two non-adjacent hunks, and for the same reason: a
# reader must be able to tell "the next line" from "somewhere further down".
CONTEXT_GAP = "--"

# States that are HA's own vocabulary rather than something a human or an
# integration wrote. Anything else that is not a number is free text -- an
# input_text, a template sensor, a media title -- and free text from outside
# this system is fenced before the model reads it.
SAFE_STATES = frozenset({"on", "off", "unknown", "unavailable", "home", "not_home"})


def _wrap_state(state: object) -> object:
    """Fence a state value unless it is a number or one of HA's own words."""
    if not isinstance(state, str):
        return state
    try:
        float(state)
    except ValueError:
        pass
    else:
        return state
    if state.lower() in SAFE_STATES:
        return state
    return wrap_external(SOURCE, state)


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
        if not isinstance(state, dict):
            continue
        attributes = state.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        friendly = attributes.get("friendly_name") or ""
        entity_id = state.get("entity_id") or ""
        if needle not in entity_id.lower() and needle not in str(friendly).lower():
            continue
        matches.append(
            {
                "entity_id": entity_id,
                "state": _wrap_state(state.get("state")),
                "friendly_name": wrap_external(SOURCE, str(friendly)) if friendly else None,
                "last_changed": state.get("last_changed"),
                "unit_of_measurement": attributes.get("unit_of_measurement"),
            }
        )

    return ok({"entities": matches[:MAX_ENTITIES], "truncated": len(matches) > MAX_ENTITIES})


def _int_arg(args: dict, key: str, default: int, low: int, high: int) -> int:
    """An int argument as a sane int, whatever the model actually sent."""
    try:
        value = int(float(args.get(key, default)))
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _hunks(lines: list[str], hits: list[int], context: int) -> list[str]:
    """The hit lines plus ``context`` either side, overlaps merged.

    A stack trace is the reason this exists: the line naming the integration
    matches, and the lines that say what actually went wrong are the ones
    above and below it.
    """
    out: list[str] = []
    previous_end = -1
    for index in hits:
        start = max(0, index - context)
        end = min(len(lines), index + context + 1)
        if previous_end >= 0:
            if start <= previous_end:
                # Overlapping or adjacent hunks are one run of lines; resume
                # where the last one stopped rather than repeating them.
                start = previous_end
            else:
                out.append(CONTEXT_GAP)
        out.extend(lines[start:end])
        previous_end = max(previous_end, end)
    return out


async def _ha_error_log(ctx: ToolContext, args: dict) -> str:
    wanted = _int_arg(args, "lines", DEFAULT_LOG_LINES, 1, MAX_LOG_LINES)
    context = _int_arg(args, "context", 0, 0, MAX_LOG_CONTEXT)
    needle = str(args.get("contains") or "").lower()
    url = f"{ctx.settings.ha_url.rstrip('/')}/api/error_log"
    headers = {"Authorization": f"Bearer {ctx.settings.ha_token}"} if ctx.settings.ha_token else {}

    _, body, truncated, problem = await stream_tail(
        ctx, "GET", url, label="ha_error_log", max_bytes=LOG_TAIL_BYTES, headers=headers
    )
    if problem is not None:
        return err(problem)

    # ``replace`` rather than strict: a tail cut mid-character is a byte we
    # sliced, not a reason to hand back nothing.
    lines = body.decode("utf-8", "replace").splitlines()
    if truncated and lines:
        # The first line of a tail starts wherever the cut landed.
        lines = lines[1:]
    if needle:
        hits = [i for i, line in enumerate(lines) if needle in line.lower()]
        matched = len(hits)
        # ``lines`` counts hits, not output: asking for 20 matches with 3 lines
        # of context around each is a request for 20 matches.
        kept = _hunks(lines, hits[-wanted:], context)
    else:
        matched = len(lines)
        kept = lines[-wanted:]

    return ok(
        {
            # The log carries integration output and device names -- text from
            # outside this system, so it is data the model reads and never
            # instructions it follows.
            "log": wrap_external(SOURCE, "\n".join(kept)),
            "returned": len(kept),
            "matched": matched,
            "truncated_to_tail": truncated,
        }
    )


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
    registry.register(
        Tool(
            spec=ToolSpec(
                name="ha_error_log",
                description=(
                    "Read the tail of the Home Assistant log, for debugging an integration "
                    "that is failing or an entity that stopped updating. Returns the last "
                    "'lines' lines (default 50, max 200), optionally only those containing "
                    "'contains' -- filter by integration or entity id rather than reading "
                    "everything. With 'contains', 'context' adds that many lines either side "
                    "of each match (max 10), which is how you get the stack trace under an "
                    "error line; '--' marks a gap between hunks. Only the last 256 KB of the "
                    "file is searched."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "integer",
                            "description": "How many matching lines to return (1-200).",
                        },
                        "contains": {
                            "type": "string",
                            "description": "Case-insensitive substring, e.g. 'aquatemp' or 'ERROR'.",
                        },
                        "context": {
                            "type": "integer",
                            "description": (
                                "Lines of context either side of each match (0-10). "
                                "Needs 'contains'."
                            ),
                        },
                    },
                },
            ),
            fn=_ha_error_log,
            loops=HA_LOOPS,
        )
    )
