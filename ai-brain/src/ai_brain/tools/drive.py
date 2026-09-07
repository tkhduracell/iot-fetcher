"""Semantic search over the user's Google Drive, via the gdrive-rag service.

The chunk text that comes back is whatever a document happens to say, which
makes it the most obvious injection surface in the system -- so it is wrapped
as external text. Filenames and paths are equally authored by the user, but
they are short and structural, and the wrapper is reserved for the body.
"""

from __future__ import annotations

from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok, wrap_external
from ai_brain.tools.http import decode_json, request

DRIVE_LOOPS = frozenset({"brain", "researcher"})

SOURCE = "google-drive"


async def _drive_search(ctx: ToolContext, args: dict) -> str:
    query = str(args["query"])
    top_k = int(args.get("top_k") or 5)
    url = f"{ctx.settings.gdrive_rag_url.rstrip('/')}/query"

    response, problem = await request(
        ctx, "POST", url, label="drive_search", json={"query": query, "top_k": top_k}
    )
    if problem is not None:
        return err(problem)

    body, problem = decode_json(response, "drive_search")
    if problem is not None:
        return err(problem)

    hits = [
        {
            "file_name": hit.get("file_name"),
            "folder_path": hit.get("folder_path"),
            "text": wrap_external(SOURCE, str(hit.get("text") or "")),
            "web_view_link": hit.get("web_view_link"),
            "similarity": hit.get("similarity"),
        }
        for hit in (body if isinstance(body, list) else [])
        if isinstance(hit, dict)
    ]
    return ok({"hits": hits})


def register_drive_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="drive_search",
                description=(
                    "Semantic search over the user's Google Drive documents. Returns the "
                    "best-matching chunks with their file name, folder and link. Ask in natural "
                    "language -- this matches on meaning, not on exact words."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            ),
            fn=_drive_search,
            loops=DRIVE_LOOPS,
        )
    )
