"""One agent's think cycle, and the forever-loop that keeps calling it.

A cycle is deliberately bounded on every axis that could otherwise run away:
``max_rounds`` caps how many times the model may come back for more tools,
``call_timeout_s`` caps a single provider call *inside the chain*, and the wake
the model asks for is clamped into a sane band. The loop keeps an outer bound
too, but a generous multiple of the per-call one (``CHAIN_TIMEOUT_FACTOR``):
it is the backstop for a chain that hangs somewhere other than a provider
call, not a second budget that could starve the fallback provider of its own
timeout. Whatever happens, the cycle ends the same way --
a journal line, the inbox archived, the per-cycle scratch state cleared -- so
the next cycle starts from a clean, readable state.

Failure is expected rather than exceptional here. A drained quota is not a
crash but a reason to sleep until the ledger says otherwise; a timeout, a bad
payload or a tool that blew up all become a status and a journal line. Only
``run_forever`` guards against the truly unforeseen, and it does so by
refusing to die: a cycle that raises is logged and followed by a heartbeat's
sleep, because a brain that stops thinking is worse than one that thinks badly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ai_brain.ledger import Priority
from ai_brain.llm import ChainExhausted, Message, ProviderChain
from ai_brain.memory import MemoryDir, Note
from ai_brain.tools import ToolContext, ToolRegistry

log = logging.getLogger(__name__)

MAX_TOKENS = 4000
MIN_BACKOFF_S = 60
MAX_WAKE_S = 12 * 3600

# Tools are expected to bound their own output, but a tool that forgets would
# otherwise push an unbounded string into the conversation -- and the
# conversation is resent in full on every round. This is the loop's backstop,
# not the tools' budget: deliberately generous, and only ever a last resort.
MAX_TOOL_RESULT_CHARS = 16_000

# The chain gives each provider ``call_timeout_s`` and may try two providers
# twice each, so the loop's own bound has to leave room for all of them.
CHAIN_TIMEOUT_FACTOR = 4

# The live trace is read over HTTP by a human watching a cycle think, not by a
# model, so it is bounded far harder than the conversation itself: enough to
# see what happened, never enough for one runaway round to make the whole
# trace unreadable -- or to hold a second copy of a 16k tool result in memory.
TRACE_TEXT_CHARS = 2000
TRACE_PREVIEW_CHARS = 500

Status = Literal["ok", "no_budget", "error", "timeout", "paused", "cancelled", "max_rounds"]

CYCLE_INSTRUCTIONS = """\
# This cycle
You are running one think cycle. Use tools to look at the world and to record
what you learn. End by calling end_cycle with a short summary and the number of
minutes until you want waking again."""

COMPACTION_INSTRUCTIONS = """\
Your memory is large: merge related facts, delete outdated ones by overwriting,
and prune."""


@dataclass
class CycleResult:
    status: Status
    model: str
    rounds: int
    next_wake_s: int


@dataclass
class RoundTrace:
    """One model reply and whatever the tools it asked for answered."""

    at: float
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)


@dataclass
class CycleTrace:
    """What the current (or last) cycle is doing, in memory only.

    The journal is the history -- one line per cycle, on disk, forever. This is
    the opposite: everything a cycle thought, kept only until the next cycle
    replaces it. It exists so a reader can watch a cycle happen rather than
    read its epitaph, which is why it is created before the first thing that
    could end the cycle and closed in ``_finish`` however the cycle went.
    """

    started_at: float
    finished_at: float | None = None
    status: Status | None = None
    model: str = ""
    summary: str = ""
    rounds: list[RoundTrace] = field(default_factory=list)

    @property
    def in_progress(self) -> bool:
        return self.finished_at is None


def _trunc(text: str, limit: int) -> str:
    """Cut to ``limit``, saying how much was dropped rather than trailing off."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[+{len(text) - limit}]"


def _safe_args(args: Any) -> dict:
    """Render tool arguments as short strings, whatever the model sent.

    The trace is serialised straight to JSON by the API, and a model can put
    anything in an argument value -- so nothing here may assume a type, and
    nothing may carry an unbounded string through.
    """
    if not isinstance(args, dict):
        return {"_": _trunc(str(args), TRACE_PREVIEW_CHARS)}
    return {
        str(key): _trunc(value if isinstance(value, str) else str(value), TRACE_PREVIEW_CHARS)
        for key, value in args.items()
    }


class AgentLoop:
    def __init__(
        self,
        name: str,
        memory: MemoryDir,
        chain: ProviderChain,
        registry: ToolRegistry,
        ctx: ToolContext,
        heartbeat_s: int,
        priority: Priority,
        constitution: str,
        clock: Callable[[], float],
        pause_file: Path,
        max_rounds: int = 8,
        call_timeout_s: int = 60,
    ) -> None:
        self.name = name
        self.memory = memory
        self.chain = chain
        self.registry = registry
        self.ctx = ctx
        self.heartbeat_s = heartbeat_s
        self.priority = priority
        self.constitution = constitution
        self.clock = clock
        self.pause_file = Path(pause_file)
        self.max_rounds = max_rounds
        self.call_timeout_s = call_timeout_s

        self.wake = asyncio.Event()
        self.last_cycle: CycleResult | None = None
        self.last_cycle_at: float = 0.0
        self.cycle_counts: dict[str, int] = {}
        self.trace: CycleTrace | None = None

    # -- one cycle -----------------------------------------------------

    async def run_cycle(self) -> CycleResult:
        # First statement, deliberately: a cycle that turns out to be paused
        # is still a cycle someone may be watching, and a reader who polls
        # between the pause check and the trace would otherwise be shown the
        # *previous* cycle as if it were this one.
        self.trace = trace = CycleTrace(started_at=self.clock())
        notes: list[Note] = []
        status: Status = "ok"
        model = ""
        rounds = 0
        summary = ""
        next_wake_s = self.heartbeat_s

        # Paused: no provider call at all, but the cycle still closes its books
        # so the pause shows up in the journal like any other outcome.
        if self.pause_file.exists():
            return await self._finish("paused", "", 0, "paused by PAUSE file", next_wake_s, notes)

        try:
            # Read the inbox once. Reading it again inside read_context would
            # render whatever arrived in between -- and mark_done only archives
            # this list, so that note would be shown to the model and then left
            # unread, to be shown again next cycle.
            notes = self.memory.unread_notes()
            messages = self._opening_messages(notes)
            self.ctx.extras["cycle_topics"] = []

            while rounds < self.max_rounds:
                # A tool can drop the PAUSE file mid-cycle, and a pause that
                # only takes effect at the next cycle boundary is no pause at
                # all when a cycle is eight provider calls long.
                if self.pause_file.exists():
                    status, summary = "paused", "paused mid-cycle"
                    log.info("[%s] PAUSE appeared mid-cycle, stopping", self.name)
                    break
                reply = await asyncio.wait_for(
                    self.chain.complete(
                        messages,
                        self.registry.specs_for(self.name),
                        MAX_TOKENS,
                        self.priority,
                    ),
                    timeout=self.call_timeout_s * CHAIN_TIMEOUT_FACTOR,
                )
                rounds += 1
                model = reply.model
                trace.rounds.append(
                    RoundTrace(
                        at=self.clock(),
                        text=_trunc(reply.text or "", TRACE_TEXT_CHARS),
                        tool_calls=[
                            {"name": c.name, "args": _safe_args(c.args)} for c in reply.tool_calls
                        ],
                    )
                )
                messages.append(
                    Message(
                        "assistant",
                        reply.text,
                        tool_calls=tuple(reply.tool_calls),
                        thought_signature=reply.thought_signature,
                    )
                )
                if not reply.tool_calls:
                    break
                for call in reply.tool_calls:
                    result = await self.registry.dispatch(self.ctx, call)
                    result = self._cap_tool_result(call.name, result)
                    trace.rounds[-1].tool_results.append(
                        {
                            "name": call.name,
                            "result_preview": _trunc(result, TRACE_PREVIEW_CHARS),
                        }
                    )
                    messages.append(Message("tool", result, tool_call_id=call.id, name=call.name))
                if self.ctx.extras.get("end_cycle"):
                    break

            ended = self.ctx.extras.get("end_cycle")
            if ended:
                minutes, summary = ended
                next_wake_s = _clamp_wake(minutes, self.heartbeat_s)
            elif status == "ok" and rounds >= self.max_rounds:
                # The model ran out of rounds without calling end_cycle. That
                # is not a normal cycle: nothing summarised the work and
                # nothing chose a wake, so reporting it as ``ok`` hides a loop
                # that may be going in circles every heartbeat.
                status = "max_rounds"
                summary = f"hit max_rounds ({self.max_rounds}) without end_cycle"
                log.warning("[%s] hit max_rounds without end_cycle", self.name)
        except TimeoutError:
            status, summary = (
                "timeout",
                f"chain exceeded {self.call_timeout_s * CHAIN_TIMEOUT_FACTOR}s",
            )
            log.warning("[%s] cycle timed out after %d round(s)", self.name, rounds)
        except ChainExhausted as exc:
            status, summary = "no_budget", "no provider budget left"
            if exc.retry_at is not None:
                next_wake_s = max(MIN_BACKOFF_S, int(exc.retry_at - self.clock()))
            log.warning("[%s] no budget; sleeping %ds", self.name, next_wake_s)
        except asyncio.CancelledError:
            # Shutdown. Still close the books, then let the cancel propagate:
            # the inbox must not keep notes this cycle already consumed.
            status, summary = "cancelled", "cancelled mid-cycle"
            raise
        except Exception as exc:  # a bad cycle must not take the process with it
            status, summary = "error", f"{type(exc).__name__}: {exc}"
            log.exception("[%s] cycle failed", self.name)
        finally:
            result = await self._finish(status, model, rounds, summary, next_wake_s, notes)

        return result

    # -- forever -------------------------------------------------------

    async def run_forever(self) -> None:
        while True:
            try:
                result = await self.run_cycle()
                await self._sleep(result.next_wake_s)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[%s] loop body failed; retrying after heartbeat", self.name)
                await self._sleep(self.heartbeat_s)

    async def _sleep(self, seconds: float) -> None:
        """Sleep, but wake early when someone rings the bell.

        The bell is only cleared when this sleep actually consumed a ring.
        Clearing unconditionally -- as the old order did, after the wait --
        wipes a ring that landed in the window between the timeout firing and
        the clear: nothing rings twice, the note is already in the inbox, and
        the brain then sleeps a whole heartbeat on a message it was explicitly
        told about. A timed-out sleep consumed nothing, so it clears nothing,
        and the ring is honoured by the next sleep instead.
        """
        try:
            await asyncio.wait_for(self.wake.wait(), timeout=seconds)
        except TimeoutError:
            return
        self.wake.clear()

    # -- internals -----------------------------------------------------

    def _cap_tool_result(self, tool: str, result: str) -> str:
        """Keep one runaway tool result from swamping the conversation.

        Tools are supposed to trim themselves; this only catches the ones that
        did not. The marker is left in the text on purpose, so the model can
        see it was cut off rather than silently reasoning from half an answer.
        """
        if len(result) <= MAX_TOOL_RESULT_CHARS:
            return result
        dropped = len(result) - MAX_TOOL_RESULT_CHARS
        log.warning(
            "[%s] tool %s returned %d chars; truncated to %d",
            self.name,
            tool,
            len(result),
            MAX_TOOL_RESULT_CHARS,
        )
        return result[:MAX_TOOL_RESULT_CHARS] + f"\n…[truncated by loop: {dropped} more chars]"

    def _opening_messages(self, notes: list[Note]) -> list[Message]:
        context = self.memory.read_context(self.constitution, notes=notes)
        system = context + "\n\n" + CYCLE_INSTRUCTIONS
        if self.memory.needs_compaction():
            system += "\n\n" + COMPACTION_INSTRUCTIONS
        now = datetime.fromtimestamp(self.clock(), UTC).isoformat()
        return [
            Message("system", system),
            Message("user", f"Begin your think cycle. Current time: {now}."),
        ]

    async def _finish(
        self,
        status: Status,
        model: str,
        rounds: int,
        summary: str,
        next_wake_s: int,
        notes: list[Note],
    ) -> CycleResult:
        """Close the books on a cycle, however it went."""
        try:
            self.memory.append_journal(f"[{status}] model={model or '-'} rounds={rounds} {summary}")
            self.memory.mark_done(notes)
            self.memory.purge_done()
            # Before the next cycle reads needs_compaction(): pruning here is
            # what keeps the journal rule clearable rather than a one-way latch.
            self.memory.prune_journal()
        except Exception:
            log.exception("[%s] could not write back memory", self.name)

        self.ctx.extras.pop("end_cycle", None)
        slack_out = self.ctx.extras.get("slack_out")
        if slack_out is not None:
            for topic in self.ctx.extras.get("cycle_topics") or []:
                try:
                    await slack_out.set_status(topic, "active")
                except Exception:
                    log.exception("[%s] could not clear status for %s", self.name, topic)
        self.ctx.extras["cycle_topics"] = []

        result = CycleResult(
            status=status, model=model, rounds=rounds, next_wake_s=int(next_wake_s)
        )
        self.last_cycle = result
        self.last_cycle_at = self.clock()
        self.cycle_counts[status] = self.cycle_counts.get(status, 0) + 1
        if self.trace is not None:
            self.trace.status = status
            self.trace.model = model
            self.trace.summary = summary
            # Last, because ``finished_at`` is what tells a reader the rest of
            # the trace has stopped moving.
            self.trace.finished_at = self.last_cycle_at
        return result


def _clamp_wake(minutes: int, heartbeat_s: int) -> int:
    return min(max(int(minutes) * 60, heartbeat_s // 2), MAX_WAKE_S)
