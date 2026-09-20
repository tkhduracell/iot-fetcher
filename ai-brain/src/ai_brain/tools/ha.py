"""Home Assistant state, via its own MCP server, and log reads over REST.

HA's MCP Server integration (``/mcp_server/sse``) exposes the same tool
surface Assist itself gets: a single read tool, ``GetLiveContext``, plus two
dozen action tools (turn on/off, set volume, start a vacuum, ...). Only the
read tool is used here -- every action this system can take still goes
through ``propose`` and a human's approval in Slack, same as before MCP;
wiring the model straight into HA's own action tools would skip that gate
entirely, and nothing about moving the *read* path to MCP changes the case
for it.

``GetLiveContext`` only covers whatever is exposed to the ``conversation``
assistant in HA's UI (Settings > Voice assistants > Expose) -- narrower than
the old ``/api/states`` sweep, but that narrowing is the point: an entity
nobody chose to expose to an assistant does not become fair game for this one
either. There is no MCP equivalent of the error log -- it was never part of
Assist's own tool surface, on this transport or the last one -- so
``ha_error_log`` stays a direct REST call.

The result is one block of text HA formats itself, mixing our own domain/area
vocabulary with things a human typed -- a media title, a device name -- so
the whole block is fenced before the model reads it, the same call
``ha_error_log`` makes for its own free-text log lines.
"""

from __future__ import annotations

import json

from mcp import ClientSession
from mcp.client.sse import sse_client

from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok, wrap_external
from ai_brain.tools.http import stream_tail

HA_LOOPS = frozenset({"brain", "house-ops"})

SOURCE = "home-assistant"
CONTEXT_TOOL = "homeassistant__GetLiveContext"
MCP_TIMEOUT_S = 20

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


async def _ha_context(ctx: ToolContext, args: dict) -> str:
    call_args = {}
    if args.get("name"):
        call_args["name"] = str(args["name"])
    if args.get("domain"):
        call_args["domain"] = args["domain"]
    if args.get("area"):
        call_args["area"] = str(args["area"])

    url = f"{ctx.settings.ha_url.rstrip('/')}/mcp_server/sse"
    headers = (
        {"Authorization": f"Bearer {ctx.settings.ha_token}"}
        if ctx.settings.ha_token
        else {}
    )

    try:
        async with sse_client(url, headers=headers, timeout=MCP_TIMEOUT_S) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(CONTEXT_TOOL, call_args)
    except (
        Exception
    ) as exc:  # network, auth, or protocol failure -- all the same to the model
        return err(f"ha_context: {type(exc).__name__}: {exc}")

    if result.is_error:
        text = "; ".join(c.text for c in result.content if hasattr(c, "text"))
        return err(f"ha_context: {text or 'HA MCP tool returned an error'}")

    text = "\n".join(_unwrap(c.text) for c in result.content if hasattr(c, "text"))
    return ok({"context": wrap_external(SOURCE, text)})


def _unwrap(text: str) -> str:
    """HA's own tools wrap their text content as ``{"success": ..., "result": ...}``.

    That is HA's envelope, not MCP's -- the protocol only promises a string,
    and what HA puts in it is its own choice. Unwrapping here means the model
    reads ``GetLiveContext``'s actual answer instead of a JSON string with the
    answer buried inside a ``result`` key; anything that is not this exact
    shape is passed through as-is, since a future HA version is free to change
    it and a tool result the model can still read is better than a crash.
    """
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
        return parsed["result"]
    return text


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
    headers = (
        {"Authorization": f"Bearer {ctx.settings.ha_token}"}
        if ctx.settings.ha_token
        else {}
    )

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
                name="ha_context",
                description=(
                    "Read the current state of Home Assistant devices, sensors and areas -- "
                    "whatever is exposed to the Assist voice assistant, which is most of the "
                    "house but not necessarily everything. Omit every filter for the whole "
                    "house at once, or narrow with 'name' (matches entity or alias, "
                    "case-insensitive), 'domain' (e.g. 'light', 'climate', 'sensor' -- a "
                    "string or a list) and/or 'area'. Prefer filtering by domain when you want "
                    "every device of one kind."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "domain": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ]
                        },
                        "area": {"type": "string"},
                    },
                },
            ),
            fn=_ha_context,
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
