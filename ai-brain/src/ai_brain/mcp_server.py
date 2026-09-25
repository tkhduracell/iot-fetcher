"""An MCP server over the agents' memory, for a human's coding agent.

The loops curate their own memory one cycle at a time; this is the other way
in -- Filip's Claude Code, connected over the LAN, reading and refactoring
facts, journals, gaps, personas, goals and the constitution in bulk.

It runs in the ai-brain process, on its own port (streamable HTTP via
uvicorn; the introspection API is aiohttp and stays read-only), so every
write goes through the same ``MemoryDir`` the loops use -- atomic
replaces, redaction on facts, the same name validation. Agents pick changes
up on their next cycle; nothing is paused.

Writes need ``MCP_TOKEN``. With it set, every request must carry
``Authorization: Bearer <token>`` and the write tools exist; without it the
server is read-only and unauthenticated, like the introspection API. Every
outside overwrite or delete of a fact, persona, goals or the constitution
keeps the previous body as a revision (see ``history``), so a refactor can
be undone.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ai_brain.memory import MemoryDir, safe_name

if TYPE_CHECKING:
    from ai_brain.supervisor import System

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
ai-brain's memory: one brain and several expert agents, each with its own
facts, journal, gaps, persona (the brain's is called its identity) and, for
the brain, goals. The constitution is shared by all of them.

Start with list_agents, then agent_overview for the agent you are working
on. Facts are the durable knowledge read back into every cycle -- keep them
few, sharp and non-overlapping. Edits land on disk immediately and the agent
sees them on its next cycle. Every overwrite or delete keeps a revision;
history shows them.
"""

MAX_JOURNAL_DAYS = 14


def _iso(ts: float | None) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def build_mcp(system: System, token: str) -> MCPServer:
    writable = bool(token)
    mcp = MCPServer(name="ai-brain", instructions=INSTRUCTIONS)

    def memory(agent: str) -> MemoryDir:
        found = system.memories.get(agent)
        if found is None:
            raise ToolError(f"unknown agent {agent!r}; one of {sorted(system.memories)}")
        return found

    def checked(name: str) -> str:
        try:
            return safe_name(name)
        except ValueError as exc:
            raise ToolError(f"{exc}; names are lowercase [a-z0-9_.-], max 64") from None

    constitution_path = system.settings.memory_root / "constitution.md"

    # -- read ----------------------------------------------------------

    @mcp.tool()
    def list_agents() -> list[dict[str, Any]]:
        """Every agent, with its fact and open-gap counts."""
        return [
            {
                "name": name,
                "emoji": mem.persona_meta().get("emoji", ""),
                "is_brain": mem.is_brain,
                "facts": len(mem.list_facts()),
                "open_gaps": len(mem.gaps()),
            }
            for name, mem in sorted(system.memories.items())
        ]

    @mcp.tool()
    def agent_overview(agent: str) -> dict[str, Any]:
        """An agent's persona body, goals, every fact's title and freshness, and open gaps."""
        mem = memory(agent)
        return {
            "persona": mem.persona_text(),
            "goals": mem.goals_text() if mem.is_brain else None,
            "facts": [
                {
                    "name": f.name,
                    "title": f.title,
                    "written_at": _iso(f.written_at),
                    "writes": f.writes,
                }
                for f in mem.fact_stats()
            ],
            "open_gaps": [{"id": g.id, "question": g.question, "why": g.why} for g in mem.gaps()],
            "journal_days": mem.journal_days(),
        }

    @mcp.tool()
    def read_fact(agent: str, name: str) -> dict[str, str]:
        """One fact's title and full body."""
        fact = memory(agent).read_fact(checked(name))
        if fact is None:
            raise ToolError(f"{agent} has no fact {name!r}")
        return {"name": fact.name, "title": fact.title, "body": fact.body}

    @mcp.tool()
    def read_facts(agent: str) -> list[dict[str, str]]:
        """Every fact of an agent in full -- for a refactor that needs the whole set."""
        mem = memory(agent)
        out = []
        for name in mem.list_facts():
            fact = mem.read_fact(name)
            if fact is not None:
                out.append({"name": fact.name, "title": fact.title, "body": fact.body})
        return out

    @mcp.tool()
    def read_journal(agent: str, days: int = 2) -> str:
        """The agent's journal for the last ``days`` days (max 14), one line per cycle."""
        return memory(agent).journal_text(days=max(1, min(days, MAX_JOURNAL_DAYS)))

    @mcp.tool()
    def list_gaps(agent: str, include_closed: bool = False) -> list[dict[str, Any]]:
        """The agent's known unknowns; closed ones carry their answer."""
        return [
            {
                "id": g.id,
                "question": g.question,
                "why": g.why,
                "opened_at": _iso(g.opened_at),
                "closed_at": _iso(g.closed_at),
                "answer": g.answer,
            }
            for g in memory(agent).gaps(include_closed=include_closed)
        ]

    @mcp.tool()
    def read_persona(agent: str) -> str:
        """The agent's whole persona file (its "soul"), frontmatter included --
        the exact text write_persona replaces."""
        mem = memory(agent)
        return mem.persona_path.read_text(encoding="utf-8") if mem.persona_path.exists() else ""

    @mcp.tool()
    def read_constitution() -> str:
        """The constitution every agent reads at the top of its prompt."""
        return constitution_path.read_text(encoding="utf-8")

    @mcp.tool()
    def history(agent: str, kind: str, name: str = "", limit: int = 5) -> list[dict[str, Any]]:
        """Previous bodies, newest first. kind: persona | goals | fact (needs name) |
        constitution (agent ignored)."""
        mem = memory(agent) if kind != "constitution" else system.memories["brain"]
        if kind == "persona":
            revisions = mem.identity_history(limit)
        elif kind == "goals":
            revisions = mem.goals_history(limit)
        elif kind == "fact":
            revisions = mem.fact_history(checked(name), limit)
        elif kind == "constitution":
            revisions = mem._history("constitution", limit)
        else:
            raise ToolError("kind must be persona, goals, fact or constitution")
        return [{"at": _iso(r.at), "body": r.body} for r in revisions]

    if not writable:
        return mcp

    # -- write ---------------------------------------------------------

    @mcp.tool()
    def write_fact(agent: str, name: str, title: str, body: str) -> str:
        """Create or replace a fact. name: lowercase [a-z0-9_.-]. The previous body is kept."""
        mem = memory(agent)
        mem.keep_fact_revision(checked(name))
        mem.write_fact(name, title, body)
        return f"wrote {agent}/{name}"

    @mcp.tool()
    def delete_fact(agent: str, name: str) -> str:
        """Delete a fact. Its body is kept as a revision (history kind=fact)."""
        mem = memory(agent)
        mem.keep_fact_revision(checked(name))
        if not mem.delete_fact(name):
            raise ToolError(f"{agent} has no fact {name!r}")
        return f"deleted {agent}/{name}"

    @mcp.tool()
    def rename_fact(agent: str, name: str, new_name: str, new_title: str = "") -> str:
        """Rename a fact, keeping its body (and its title unless new_title is given)."""
        mem = memory(agent)
        fact = mem.read_fact(checked(name))
        if fact is None:
            raise ToolError(f"{agent} has no fact {name!r}")
        checked(new_name)
        if mem.read_fact(new_name) is not None:
            raise ToolError(f"{agent} already has a fact {new_name!r}")
        mem.write_fact(new_name, new_title or fact.title, fact.body)
        mem.keep_fact_revision(name)
        mem.delete_fact(name)
        return f"renamed {agent}/{name} -> {new_name}"

    @mcp.tool()
    def write_persona(agent: str, body: str) -> str:
        """Replace an agent's persona (the brain's identity) -- its "soul".
        Keep any leading ---/key: value/--- block (emoji etc.)."""
        memory(agent).rewrite_persona(body)
        return f"rewrote {agent} persona"

    @mcp.tool()
    def write_goals(body: str) -> str:
        """Replace the brain's goals."""
        system.memories["brain"].rewrite_goals(body)
        return "rewrote brain goals"

    @mcp.tool()
    def write_constitution(body: str) -> str:
        """Replace the constitution. Takes effect on every agent's next cycle."""
        brain = system.memories["brain"]
        brain._keep_revision("constitution", constitution_path, body)
        tmp = constitution_path.with_name(constitution_path.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(constitution_path)
        # The loops hold the text, read once at startup -- swap it in place
        # so the edit lands next cycle rather than next restart.
        system.constitution = body
        for loop in system.loops.values():
            loop.constitution = body
        return "rewrote constitution"

    @mcp.tool()
    def close_gap(agent: str, gap_id: str, answer: str) -> str:
        """Close an open gap with its answer."""
        if not memory(agent).close_gap(gap_id, answer):
            raise ToolError(f"{agent} has no open gap {gap_id!r}")
        return f"closed {agent}/{gap_id}"

    @mcp.tool()
    def send_note(agent: str, body: str) -> str:
        """Drop a note in an agent's inbox; it reads it on its next cycle and wakes now."""
        memory(agent).drop_note("filip", body)
        system.wake(agent)
        return f"note sent to {agent}"

    return mcp


def bearer_guard(app: Any, token: str) -> Callable[..., Awaitable[None]]:
    """Plain ASGI wrapper: every HTTP request needs the bearer token."""
    expected = f"Bearer {token}".encode()

    async def guarded(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(got, expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    return guarded


def build_app(system: System, token: str) -> Any:
    app = build_mcp(system, token).streamable_http_app(host="0.0.0.0")
    return bearer_guard(app, token) if token else app


async def serve(system: System, port: int, token: str) -> None:
    """Run until cancelled. A failure to bind logs and returns rather than
    taking the loops down with it -- the MCP is a side door, not the house."""
    import uvicorn

    class _Server(uvicorn.Server):
        # uvicorn would install its own SIGTERM/SIGINT handlers and swallow
        # the supervisor's shutdown; this server is stopped by cancellation.
        @contextlib.contextmanager
        def capture_signals(self):  # type: ignore[override]
            yield

    config = uvicorn.Config(
        build_app(system, token), host="0.0.0.0", port=port, log_level="warning", lifespan="on"
    )
    server = _Server(config)
    log.info("MCP on :%d (%s)", port, "read-write, token" if token else "read-only")
    try:
        await server.serve()
    except (OSError, SystemExit) as exc:
        log.error("MCP server stopped: %s", exc)


