"""One agent's think cycle, and the forever-loop that keeps calling it.

A cycle is deliberately bounded on every axis that could otherwise run away:
``max_rounds`` caps how many times the model may come back for more tools,
``call_timeout_s`` caps a single provider call, and the wake the model asks for
is clamped into a sane band. Whatever happens, the cycle ends the same way --
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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

Status = Literal["ok", "no_budget", "error", "timeout", "paused", "cancelled"]

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

    # -- one cycle -----------------------------------------------------

    async def run_cycle(self) -> CycleResult:
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
            notes = self.memory.unread_notes()
            messages = self._opening_messages()
            self.ctx.extras["cycle_topics"] = []

            while rounds < self.max_rounds:
                reply = await asyncio.wait_for(
                    self.chain.complete(
                        messages,
                        self.registry.specs_for(self.name),
                        MAX_TOKENS,
                        self.priority,
                    ),
                    timeout=self.call_timeout_s,
                )
                rounds += 1
                model = reply.model
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
                    messages.append(Message("tool", result, tool_call_id=call.id, name=call.name))
                if self.ctx.extras.get("end_cycle"):
                    break

            ended = self.ctx.extras.get("end_cycle")
            if ended:
                minutes, summary = ended
                next_wake_s = _clamp_wake(minutes, self.heartbeat_s)
        except TimeoutError:
            status, summary = "timeout", f"provider call exceeded {self.call_timeout_s}s"
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
        """Sleep, but wake early when someone rings the bell."""
        try:
            await asyncio.wait_for(self.wake.wait(), timeout=seconds)
        except TimeoutError:
            pass
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

    def _opening_messages(self) -> list[Message]:
        system = self.memory.read_context(self.constitution) + "\n\n" + CYCLE_INSTRUCTIONS
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
        return result


def _clamp_wake(minutes: int, heartbeat_s: int) -> int:
    return min(max(int(minutes) * 60, heartbeat_s // 2), MAX_WAKE_S)
