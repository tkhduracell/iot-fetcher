"""Semantic search over the user's Google Drive, via the gdrive-rag service.

The chunk text that comes back is whatever a document happens to say, which
makes it the most obvious injection surface in the system -- so it is wrapped
as external text. Filenames and paths are equally authored by the user, but
they are short and structural, and the wrapper is reserved for the body.

Results are sized for small models: ``top_k`` is clamped, whitespace in chunk
text is collapsed, each chunk is cut at ``MAX_CHUNK_CHARS``, chunks from the
same file are grouped so name/folder/link appear once, and chunks past
``MAX_RESULT_CHARS`` total are dropped (``truncated: true``) -- rather than
letting the loop's blunt cut land mid-JSON.
"""

from __future__ import annotations

import re

from ai_brain.config import Settings
from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok, wrap_external
from ai_brain.tools.http import decode_json, request

DRIVE_LOOPS = frozenset({"brain", "researcher"})

SOURCE = "google-drive"

DEFAULT_TOP_K = 5
MAX_TOP_K = 8
MAX_CHUNK_CHARS = 700
MAX_RESULT_CHARS = 6_000


def _clip(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_CHUNK_CHARS:
        return text
    return text[:MAX_CHUNK_CHARS].rsplit(" ", 1)[0] + " …"


def _gdrive_configured(settings: Settings) -> bool:
    return bool(settings.gdrive_rag_url)


async def _drive_search(ctx: ToolContext, args: dict) -> str:
    query = str(args["query"])
    top_k = max(1, min(MAX_TOP_K, int(args.get("top_k") or DEFAULT_TOP_K)))
    url = f"{ctx.settings.gdrive_rag_url.rstrip('/')}/query"

    response, problem = await request(
        ctx, "POST", url, label="drive_search", json={"query": query, "top_k": top_k}
    )
    if problem is not None:
        return err(problem)

    body, problem = decode_json(response, "drive_search")
    if problem is not None:
        return err(problem)

    files: dict[tuple, dict] = {}
    used = 0
    truncated = False
    for hit in body if isinstance(body, list) else []:
        if not isinstance(hit, dict):
            continue
        text = _clip(str(hit.get("text") or ""))
        if used + len(text) > MAX_RESULT_CHARS:
            truncated = True
            continue
        used += len(text)
        key = (hit.get("file_name"), hit.get("folder_path"))
        entry = files.setdefault(
            key,
            {
                "file_name": hit.get("file_name"),
                "folder_path": hit.get("folder_path"),
                "web_view_link": hit.get("web_view_link"),
                "similarity": None,
                "chunks": [],
            },
        )
        similarity = hit.get("similarity")
        if isinstance(similarity, (int, float)):
            similarity = round(float(similarity), 2)
            if entry["similarity"] is None or similarity > entry["similarity"]:
                entry["similarity"] = similarity
        entry["chunks"].append(wrap_external(SOURCE, text))
    return ok({"files": list(files.values()), "truncated": truncated})


def register_drive_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="drive_search",
                description=(
                    "Semantic search over the user's Google Drive documents. Returns the "
                    "best-matching chunks grouped by file (name, folder, link). Ask in natural "
                    "language -- this matches on meaning, not on exact words. top_k 1-8, "
                    "default 5; chunks are excerpts, so search again with a sharper query "
                    "rather than raising top_k."
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
            available=_gdrive_configured,
        )
    )
