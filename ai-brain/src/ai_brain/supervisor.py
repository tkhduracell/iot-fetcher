"""Wiring, and the process that keeps the wiring alive.

``build`` is pure assembly: it reads the constitution, lays out one memory
directory per agent, hands every loop the same ledger, registry and HTTP
client, and hands nothing a network connection. Everything it returns is
inspectable, which is why the tests can drive it without a Slack token or a
provider key.

``run`` adds the parts that can only exist in a real process: signal handlers,
the periodic housekeeping, and a supervisor that treats a dead loop as a
temporary condition rather than a reason to exit. The brain must outlive its
own bugs, so a loop that raises is logged, slept on, and started again.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ai_brain.approvals import Approvals
from ai_brain.config import Settings, load_settings
from ai_brain.executors import Executors
from ai_brain.ledger import Ledger
from ai_brain.llm import ProviderChain, limits_from_settings
from ai_brain.loop import AgentLoop
from ai_brain.memory import MemoryDir
from ai_brain.metrics import MetricsWriter, render
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.tools.backend import register_backend_tools
from ai_brain.tools.memory_tools import register_memory_tools
from ai_brain.tools.propose import register_propose_tool
from ai_brain.tools.slack_tools import register_slack_tools

log = logging.getLogger(__name__)

KNOWN_EXPERTS = frozenset({"energy", "health", "house-ops", "researcher"})

HTTP_TIMEOUT_S = 20
METRICS_EVERY_S = 60
EXPIRE_EVERY_S = 600
DAY_ROLL_EVERY_S = 60
NEW_DAY_NOTE = "new day, budget restored"
LEDGER_SENDER = "ledger"
FLUSH_EVERY_S = 300
RESTART_DELAY_S = 30


@dataclass
class System:
    loops: dict[str, AgentLoop]
    ledger: Ledger
    registry: ToolRegistry
    approvals: Approvals
    memories: dict[str, MemoryDir]
    constitution: str
    http: httpx.AsyncClient
    executors: Executors
    settings: Settings
    wake: Callable[[str], None]
    slack_out: object | None = None


def _read_constitution(settings: Settings) -> str:
    """Filip's file, copied from the seed exactly once -- on first boot."""
    path = settings.memory_root / "constitution.md"
    if not path.exists():
        seed = settings.seed_root / "constitution.md"
        if not seed.exists():
            log.error("no constitution at %s and no seed at %s; refusing to start", path, seed)
            raise SystemExit(2)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed, path)
        log.info("seeded constitution from %s", seed)
    return path.read_text(encoding="utf-8")


def _check_chain(settings: Settings) -> None:
    """Refuse to start on a chain that could only ever produce empty answers.

    An empty chain builds cleanly and then fails every cycle with
    ``ChainExhausted``: the container stays up, the journal fills with
    ``[no_budget]`` and ``ai_brain_ledger_remaining`` emits no series at all --
    so the Grafana panel is blank, which is indistinguishable from healthy. The
    same is true of a gemini entry with no key. Both are config mistakes, and
    a config mistake must be loud, like a missing constitution.
    """
    if not settings.llm_chain:
        log.error("LLM_CHAIN is empty; refusing to start with no providers")
        raise SystemExit(2)
    if any(entry.startswith("gemini:") for entry in settings.llm_chain) and (
        not settings.gemini_api_key
    ):
        log.error(
            "LLM_CHAIN has gemini entries (%s) but GEMINI_API_KEY is empty; refusing to start",
            ", ".join(settings.llm_chain),
        )
        raise SystemExit(2)


def _expert_names(settings: Settings) -> list[str]:
    names = []
    for name in settings.experts:
        if name in KNOWN_EXPERTS:
            names.append(name)
        else:
            log.warning("unknown expert %r in EXPERTS; skipping", name)
    return names


def build(
    settings: Settings,
    chain_factory: Callable[[Settings, Ledger], ProviderChain] | None = None,
    http: httpx.AsyncClient | None = None,
    clock: Callable[[], float] = time.time,
) -> System:
    _check_chain(settings)
    constitution = _read_constitution(settings)

    def dt_clock() -> datetime:
        return datetime.fromtimestamp(clock(), tz=UTC)

    memories: dict[str, MemoryDir] = {
        "brain": MemoryDir(settings.memory_root / "brain", "brain", True, dt_clock)
    }
    for name in _expert_names(settings):
        memories[name] = MemoryDir(settings.memory_root / "experts" / name, name, False, dt_clock)
    for memory in memories.values():
        memory.ensure()
        memory.seed_from(settings.seed_root)

    ledger = Ledger(limits_from_settings(settings), settings.memory_root / "_ledger.json", clock)
    factory = chain_factory or ProviderChain.from_settings
    chain = factory(settings, ledger)

    http = http or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)

    registry = ToolRegistry()
    register_memory_tools(registry)
    register_backend_tools(registry)
    register_slack_tools(registry)
    register_propose_tool(registry)

    executors = Executors(settings, http, dt_clock)
    # ``on_message`` stays None until Slack is up; ``start_slack`` sets it.
    approvals = Approvals(memories["brain"], executors, dt_clock)

    loops: dict[str, AgentLoop] = {}

    def wake(name: str) -> None:
        loop = loops.get(name)
        if loop is not None:
            loop.wake.set()

    for name, memory in memories.items():
        is_brain = name == "brain"
        ctx = ToolContext(
            loop=name,
            memory=memory,
            memories=memories,
            settings=settings,
            wake=wake,
            extras={"http": http, "approvals": approvals},
        )
        loops[name] = AgentLoop(
            name=name,
            memory=memory,
            chain=chain,
            registry=registry,
            ctx=ctx,
            heartbeat_s=settings.brain_heartbeat_s if is_brain else settings.expert_heartbeat_s,
            priority="brain" if is_brain else "expert",
            constitution=constitution,
            clock=clock,
            pause_file=settings.memory_root / "PAUSE",
            call_timeout_s=settings.call_timeout_s,
        )

    return System(
        loops=loops,
        ledger=ledger,
        registry=registry,
        approvals=approvals,
        memories=memories,
        constitution=constitution,
        http=http,
        executors=executors,
        settings=settings,
        wake=wake,
    )


def day_roll_watcher(system: System) -> Callable[[], Awaitable[None]]:
    """Announce the daily budget reset to the brain, once per Pacific day.

    The ledger rolls its day lazily, whenever something next happens to ask it,
    which is enough for the counters: a starved loop already sleeps until
    ``next_available_at`` and wakes at the right moment. What is missing is the
    *signal* -- this is an agent designed to reason about its own resource
    limits from what it reads in its inbox, and nothing told it the budget came
    back. So watch the day and drop a note when it turns.
    """
    seen = {"day": system.ledger.day}

    async def watch() -> None:
        today = system.ledger.day
        if today == seen["day"]:
            return
        seen["day"] = today
        system.memories["brain"].drop_note(LEDGER_SENDER, NEW_DAY_NOTE)
        system.wake("brain")
        log.info("quota day rolled to %s; brain notified", today)

    return watch


async def _supervise(name: str, loop) -> None:
    """Keep one agent loop running. A crash is a pause, never an exit."""
    while True:
        try:
            await loop.run_forever()
        except asyncio.CancelledError:
            raise
        except Exception:  # a dead loop must not stay dead
            log.exception("[%s] loop died; restarting in %ds", name, RESTART_DELAY_S)
        await asyncio.sleep(RESTART_DELAY_S)


async def _every(seconds: float, work: Callable) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await work()
        except asyncio.CancelledError:
            raise
        except Exception:  # housekeeping failures are never fatal
            log.exception("periodic task failed")


async def run(settings: Settings) -> None:
    system = build(settings)
    handler = None

    if settings.slack_bot_token and settings.slack_app_token:
        from ai_brain.slack_io import start_slack

        out, handler = await start_slack(
            settings, system.memories["brain"], system.approvals, system.wake
        )
        system.slack_out = out
        for agent in system.loops.values():
            agent.ctx.extras["slack_out"] = out
        await handler.connect_async()
        log.info("Slack connected")
    else:
        log.info("Slack disabled: no SLACK_BOT_TOKEN/SLACK_APP_TOKEN")

    writer = MetricsWriter(settings.vm_url, settings.influx_token, system.http)

    async def publish_metrics() -> None:
        await writer.write(render(system.loops, system.ledger, time.time()))

    async def expire_proposals() -> None:
        await system.approvals.expire()

    watch_day_roll = day_roll_watcher(system)

    tasks = [asyncio.create_task(_supervise(name, agent)) for name, agent in system.loops.items()]
    tasks.append(asyncio.create_task(_every(METRICS_EVERY_S, publish_metrics)))
    tasks.append(asyncio.create_task(_every(EXPIRE_EVERY_S, expire_proposals)))
    tasks.append(asyncio.create_task(_every(DAY_ROLL_EVERY_S, watch_day_roll)))
    if system.slack_out is not None:
        tasks.append(asyncio.create_task(_every(FLUSH_EVERY_S, system.slack_out.flush_queue)))

    stop = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            event_loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass

    log.info("ai-brain up: loops=%s", ", ".join(sorted(system.loops)))
    await stop.wait()

    log.info("shutting down")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if handler is not None:
        await handler.close_async()
    await system.http.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(load_settings()))
