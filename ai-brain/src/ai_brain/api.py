"""A read-only window into a running brain.

Everything the brain does already leaves a trace somewhere -- a journal line, a
proposal file, a ledger bucket -- but all of it is markdown and JSON on a
docker volume, which means the only way to see what the agents are thinking is
to shell into the box. This serves the same facts over HTTP so a page can.

Three rules shape the module, and each of them is a rule because the endpoint
has no authentication at all:

* **GET only.** Nothing here mutates anything. The routes are registered as
  ``router.add_get``, so anything else is aiohttp's own 405 rather than a
  handler that has to remember not to act.
* **The settings block is an allowlist.** ``Settings`` holds five API keys and
  two Slack tokens. ``asdict(settings)`` would put every one of them on the
  LAN, and it would do so again, silently, the day someone adds a sixth key --
  so the fields are named one at a time, and a test asserts no token literal
  appears in the body.
* **Nothing may take the brain down.** ``start_api`` returns ``None`` rather
  than raising when the port is taken, and the error middleware answers a
  bare ``{"error": "internal error"}`` rather than a traceback that would name
  paths and, in the worst case, quote a secret out of an exception message.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aiohttp import web

from ai_brain.memory import safe_name

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ai_brain.loop import AgentLoop, CycleTrace
    from ai_brain.memory import MemoryDir
    from ai_brain.supervisor import System

log = logging.getLogger(__name__)

DEFAULT_JOURNAL_DAYS = 3
MIN_JOURNAL_DAYS = 1
MAX_JOURNAL_DAYS = 30

# A brain that has been running for months would otherwise render every
# proposal it ever made into one response.
MAX_PROPOSALS = 100


def _json(payload: Any, status: int = 200) -> web.Response:
    """Every response in one place, so no route can forget ``no-store``.

    A cached ``/api/status`` is worse than no status at all: the page would
    show a paused brain as running, or a dead loop as healthy, with nothing on
    screen to say the answer is minutes old.
    """
    return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})


@web.middleware
async def _errors(request: web.Request, handler: Callable) -> web.StreamResponse:
    """Turn anything unexpected into a plain 500.

    aiohttp's default 500 body is a traceback. On an unauthenticated endpoint
    that is a filesystem layout and, if an exception message ever quotes a
    config value, a credential -- so the traceback goes to the log, where it
    belongs, and the caller gets four words.
    """
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("[api] %s %s failed", request.method, request.rel_url)
        return _json({"error": "internal error"}, status=500)


# -- rendering ---------------------------------------------------------


def _trace_json(trace: CycleTrace | None) -> dict | None:
    if trace is None:
        return None
    return {
        "started_at": trace.started_at,
        "finished_at": trace.finished_at,
        "in_progress": trace.in_progress,
        "status": trace.status,
        "model": trace.model,
        "summary": trace.summary,
        "rounds": [
            {
                "at": round_.at,
                "text": round_.text,
                "tool_calls": round_.tool_calls,
                "tool_results": round_.tool_results,
            }
            for round_ in trace.rounds
        ],
    }


def _agent_summary(name: str, loop: AgentLoop, memory: MemoryDir) -> dict:
    last = loop.last_cycle
    # A loop that has never finished a cycle has ``last_cycle_at == 0.0``,
    # which as an epoch timestamp reads as 1970 -- so it is null, not a date.
    last_at = loop.last_cycle_at or None
    next_wake_at = None
    if last is not None and last_at is not None:
        next_wake_at = last_at + last.next_wake_s
    return {
        "name": name,
        "priority": loop.priority,
        "heartbeat_s": loop.heartbeat_s,
        "last_cycle": (
            None
            if last is None
            else {
                "status": last.status,
                "model": last.model,
                "rounds": last.rounds,
                "next_wake_s": last.next_wake_s,
            }
        ),
        "last_cycle_at": last_at,
        "next_wake_at": next_wake_at,
        "cycle_counts": dict(loop.cycle_counts),
        "in_progress": loop.trace is not None and loop.trace.in_progress,
        "needs_compaction": memory.needs_compaction(),
        "facts": len(memory.list_facts()),
        "unread_notes": len(memory.unread_notes()),
    }


def _ledger_json(system: System) -> dict:
    # One snapshot for every key: it rolls the day and serialises every bucket,
    # so calling it inside the loop would redo that work per provider.
    snapshot = system.ledger.snapshot()
    keys = []
    for key in system.ledger.keys():  # noqa: SIM118 - Ledger.keys() is a method
        bucket = snapshot["buckets"].get(key, {})
        requests_left, tokens_left = system.ledger.remaining_fraction(key)
        keys.append(
            {
                "key": key,
                "requests_day": bucket.get("requests_day", 0),
                "tokens_day": bucket.get("tokens_day", 0),
                "requests_remaining": requests_left,
                "tokens_remaining": tokens_left,
                "consecutive_429": bucket.get("consecutive_429", 0),
                "blocked_until": bucket.get("blocked_until"),
                "disabled_until": bucket.get("disabled_until"),
                # The list itself is (timestamp, tokens) pairs of every call in
                # the trailing minute; its length is the only part a reader
                # wants, and the whole list would grow the body for nothing.
                "recent_requests": len(bucket.get("recent") or []),
            }
        )
    return {"day": snapshot["day"], "keys": keys}


def _settings_json(system: System) -> dict:
    """An explicit allowlist. Never ``asdict`` -- see the module docstring."""
    settings = system.settings
    return {
        "llm_chain": list(settings.llm_chain),
        "experts": list(settings.experts),
        "brain_heartbeat_s": settings.brain_heartbeat_s,
        "expert_heartbeat_s": settings.expert_heartbeat_s,
        "call_timeout_s": settings.call_timeout_s,
        "dry_run": settings.dry_run,
        "rpm": settings.rpm,
        "tpm": settings.tpm,
        "rpd": settings.rpd,
        "memory_root": str(settings.memory_root),
    }


# -- the app -----------------------------------------------------------


def build_app(
    system: System,
    started_at: float,
    clock: Callable[[], float] = time.time,
) -> web.Application:
    app = web.Application(middlewares=[_errors])

    def pause_file():
        return system.settings.memory_root / "PAUSE"

    def uptime() -> int:
        return int(clock() - started_at)

    def agent(request: web.Request) -> tuple[str, AgentLoop, MemoryDir]:
        """Resolve ``{name}`` or refuse. Every agent route starts here."""
        name = request.match_info["name"]
        loop = system.loops.get(name)
        if loop is None:
            raise web.HTTPNotFound(
                text=f'{{"error": "unknown agent: {name}"}}',
                content_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        return name, loop, system.memories[name]

    def agent_names() -> list[str]:
        """Brain first, then the experts alphabetically."""
        return ["brain"] + sorted(n for n in system.loops if n != "brain")

    async def healthz(_request: web.Request) -> web.Response:
        return _json({"ok": True, "uptime_s": uptime(), "loops": agent_names()})

    async def status(_request: web.Request) -> web.Response:
        out = system.slack_out
        proposals = system.approvals.all()
        return _json(
            {
                "uptime_s": uptime(),
                "now": clock(),
                "paused": pause_file().exists(),
                "pause_file": str(pause_file()),
                "slack": {
                    "configured": bool(
                        system.settings.slack_bot_token and system.settings.slack_app_token
                    ),
                    "connected": out is not None,
                    "queued": out.queued_count() if out is not None else 0,
                    "sessions": len(out.sessions()) if out is not None else 0,
                },
                "ledger": _ledger_json(system),
                "proposals": {
                    "pending": sum(1 for p in proposals if p.status == "pending"),
                    "total": len(proposals),
                },
                "settings": _settings_json(system),
            }
        )

    async def agents(_request: web.Request) -> web.Response:
        return _json(
            {
                "agents": [
                    _agent_summary(name, system.loops[name], system.memories[name])
                    for name in agent_names()
                ]
            }
        )

    async def agent_detail(request: web.Request) -> web.Response:
        name, loop, memory = agent(request)
        body = _agent_summary(name, loop, memory)
        body.update(
            {
                "identity": memory.persona_text(),
                "goals": memory.goals_text(),
                "is_brain": memory.is_brain,
                "fact_names": memory.list_facts(),
                "notes": [
                    {
                        "sender": note.sender,
                        "body": note.body,
                        "created": note.created.isoformat(),
                        # A basename, never the path: the path says where the
                        # volume is mounted, which is nobody's business here.
                        "file": note.path.name,
                    }
                    for note in memory.unread_notes()
                ],
                "journal_days": memory.journal_days(),
                "trace": _trace_json(loop.trace),
            }
        )
        return _json(body)

    async def agent_journal(request: web.Request) -> web.Response:
        name, _loop, memory = agent(request)
        raw = request.query.get("days")
        if raw is None:
            days = DEFAULT_JOURNAL_DAYS
        else:
            try:
                days = int(raw)
            except ValueError:
                return _json({"error": f"days must be an integer, got {raw!r}"}, status=400)
        days = max(MIN_JOURNAL_DAYS, min(MAX_JOURNAL_DAYS, days))

        # Read the dated files directly rather than counting back from today:
        # a brain that was down for a week has no file for those days, and
        # asking for "the last 3 days" should show the last 3 it wrote.
        entries = []
        for date in reversed(memory.journal_days()[-days:]):
            text = (memory.journal_dir / f"{date}.md").read_text(encoding="utf-8")
            entries.append({"date": date, "lines": text.splitlines()})
        return _json({"agent": name, "days": days, "entries": entries})

    async def agent_trace(request: web.Request) -> web.Response:
        name, loop, _memory = agent(request)
        return _json({"agent": name, "trace": _trace_json(loop.trace)})

    async def agent_fact(request: web.Request) -> web.Response:
        name, _loop, memory = agent(request)
        raw = request.match_info["fact"]
        try:
            # The same validator the tools use. A fact name is a name, never a
            # path, so ".." and "/" are rejected before anything touches disk.
            fact = safe_name(raw)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        body = memory.read_fact(fact)
        if body is None:
            return _json({"error": f"unknown fact: {fact}"}, status=404)
        return _json({"agent": name, "name": fact, "body": body})

    async def proposals(_request: web.Request) -> web.Response:
        everything = system.approvals.all()
        newest_first = list(reversed(everything))[:MAX_PROPOSALS]
        return _json(
            {
                "proposals": [
                    {
                        "id": p.id,
                        "kind": p.kind,
                        "payload": p.payload,
                        "reason": p.reason,
                        "topic": p.topic,
                        "created": p.created,
                        "status": p.status,
                        "result": p.result,
                    }
                    for p in newest_first
                ],
                "pending": sum(1 for p in everything if p.status == "pending"),
                "total": len(everything),
            }
        )

    async def slack_sessions(_request: web.Request) -> web.Response:
        out = system.slack_out
        if out is None:
            # Slack being off is a state to render, not an error: the page
            # should say "Slack disabled", not "the API is broken".
            return _json({"configured": False, "queued": 0, "sessions": []})
        return _json(
            {
                "configured": True,
                "queued": out.queued_count(),
                "sessions": [
                    {
                        "topic": topic,
                        "thread_ts": session.get("thread_ts", ""),
                        "channel": session.get("channel", ""),
                        "status": session.get("status", ""),
                    }
                    for topic, session in sorted(out.sessions().items())
                ],
            }
        )

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/agents", agents)
    app.router.add_get("/api/agents/{name}", agent_detail)
    app.router.add_get("/api/agents/{name}/journal", agent_journal)
    app.router.add_get("/api/agents/{name}/trace", agent_trace)
    app.router.add_get("/api/agents/{name}/facts/{fact}", agent_fact)
    app.router.add_get("/api/proposals", proposals)
    app.router.add_get("/api/slack/sessions", slack_sessions)
    return app


# -- lifecycle ---------------------------------------------------------


async def start_api(
    system: System,
    port: int,
    started_at: float,
    # The container's own network namespace; the port reaches the LAN only
    # when docker-compose.local.yml publishes it.
    host: str = "0.0.0.0",
) -> web.AppRunner | None:
    """Serve the API, or explain why it is not serving. Never raises.

    The brain's job is to think; this is a window onto it. A port already in
    use, or a disabled port, must leave the loops running -- so both return
    ``None`` and the caller has nothing to stop.
    """
    if port <= 0:
        log.info("HTTP API disabled (HTTP_PORT=%d)", port)
        return None
    runner = web.AppRunner(build_app(system, started_at))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except OSError as exc:
        log.error("HTTP API could not bind %s:%d (%s); continuing without it", host, port, exc)
        await runner.cleanup()
        return None
    log.info("HTTP API listening on %s:%d", host, port)
    return runner


async def stop_api(runner: web.AppRunner | None) -> None:
    if runner is None:
        return
    await runner.cleanup()
