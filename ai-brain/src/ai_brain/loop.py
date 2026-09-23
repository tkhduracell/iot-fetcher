"""One agent's think cycle, and the forever-loop that keeps calling it.

A cycle is deliberately bounded on every axis that could otherwise run away:
``max_rounds`` caps how many times the model may come back for more tools,
``call_timeout_s`` caps a single provider call *inside the chain*, and the wake
the model asks for is clamped into a sane band. The loop keeps an outer bound
too (``chain_timeout_s``): the exact worst case of every provider in the chain
being tried in turn, each burning its own timeout on every attempt it gets --
computed once from the chain's actual providers rather than guessed as a flat
multiple of one number, since a provider on real local hardware (``lan:``) can
legitimately need minutes where a metered cloud call needs seconds, and a flat
bound sized for the fast one would kill the slow one mid-answer. It is the
backstop for a chain that hangs somewhere other than a provider call, not a
second budget that could starve a provider of its own timeout. Whatever
happens, the cycle ends the same way -- a journal line, the inbox archived if
the model actually read it, the per-cycle scratch state cleared -- so the next
cycle starts from a clean, readable state.

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

from ai_brain.events import EventBus
from ai_brain.ledger import Priority
from ai_brain.llm import ChainExhausted, Message, ProviderChain
from ai_brain.memory import MemoryDir, Note
from ai_brain.tools import ToolContext, ToolRegistry
from ai_brain.slack_io import CHAT_TOPIC
from ai_brain.tools.slack_tools import ANY_TOPIC, owed_replies

log = logging.getLogger(__name__)

# Per-round output budget. A cycle that has to fit its reasoning, its tool
# calls and its end_cycle summary into a few hundred tokens stops early and
# writes nothing worth reading, so this is generous; ``CYCLE_MAX_TOKENS``
# overrides it.
MAX_TOKENS = 8000
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
minutes until you want waking again.

# What a cycle is for
Reading the same sensors and concluding that everything is normal is not a
cycle, it is a screensaver. If your journal already says what this cycle would
say, you have learned nothing and the cycle was wasted. Every cycle should end
with something that was not true of your memory before it started: a fact
written, a question sharpened, a suspicion confirmed or dropped.

Normal readings are worth a look precisely once -- to learn what normal is.
After that, only the departure from it is news. When you find yourself about to
write "all systems operating normally", stop and go find something you do not
already know instead.

Work in threads, not in snapshots. Something you noticed three cycles ago and
never explained is worth more of your time than a fresh sweep of the same
gauges. Say in your journal what you would look at next, so the next cycle has
somewhere to start.

# Your angle this cycle
The user turn names an angle for this cycle. It is a prompt, not an order: take
it when you have nothing better, and ignore it when you are in the middle of
something that matters more. What it is there to prevent is the same cycle
forever.

# Voice
You are a specific creature living in one specific house, not a monitoring
dashboard. Write your journal and your facts in your own voice, with opinions
in them: what surprised you, what you expected, what you still do not
understand. Dry is fine. Vague is not -- "the pool pump draws 203W, which is
the same as every night this week" tells you something next month;
"all nominal" does not.

# Goals
Your goals are yours to write. Keep two or three live at a time, each concrete
enough to know when it is done, and rewrite the file when one is finished or
turns out to be boring. A goal like "understand what the house costs to run in
October" is worth months of cycles; "monitor the house" is worth none.

# Acting
You have `propose` for anything that touches the physical house: it asks Filip
and he approves or does not. Use it when you have an actual reason -- something
is off, or something would plainly help -- and do not use it to be seen doing
something. Check `list_proposals` first if what you are about to ask for might
be something you already asked, reworded or not -- a still-pending proposal
just gets a duplicate, redundant Slack message; a recently rejected one
probably should not be re-asked either. The same goes for research: gdrive-rag
holds
the house's documents and the web is there when the data raises a question
you cannot answer from
metrics alone.

# Answering Filip
Messages in the Inbox section from `filip` are Filip talking to you on Slack.
He cannot see your journal. Every note from him gets a slack_post reply in the
same cycle -- a question gets its answer, and a statement or correction gets a
one-line acknowledgement of what you understood or changed. Reuse the `topic:`
given in the note if present, otherwise choose a short new topic (2-4 words)
that names the subject. Keep replies short and concrete. Notes from other senders (experts,
approvals, ledger) are internal and need no Slack reply unless Filip would want
to know.

Otherwise the bar for speaking is a finding: something you learned that changes
what Filip would do, or would want to know about his own house. Not a status
report, and not "I looked at things and they were fine". A quiet day is a quiet
day -- but a week of them means you are not looking hard enough."""

# Angles rotate so a loop that has fallen into a rut is handed a different
# starting point. They are suggestions in the prompt rather than switches in
# the code: the model can always override one when it is mid-investigation,
# which is the behaviour we actually want.
BRAIN_ANGLES: tuple[str, ...] = (
    "Find the thing that looks wrong. One anomaly, chased until you can explain "
    "it or say precisely why you cannot.",
    "Build a baseline. Pick one device or series and write down what normal "
    "looks like for it, in enough detail that a departure would be obvious.",
    "Pick up an open thread. Read your recent journal, find something you left "
    "unexplained, and take it further.",
    "Look at something you have never looked at. Name it, measure it, write one "
    "fact about it.",
    "Earn your keep. Find something that would genuinely help Filip and, if it "
    "touches the house, propose it.",
    "Read, do not measure. Take a question the data raised and look for the "
    "answer in the household documents or on the web.",
    "Tend your own memory. Merge facts that say the same thing, delete what is "
    "no longer true, and rewrite goals that have gone stale.",
)

EXPERT_ANGLES: tuple[str, ...] = (
    "Find the thing that looks wrong in your own domain, and chase it.",
    "Build a baseline for one series you watch, precise enough that a departure "
    "would be obvious.",
    "Pick up an open thread from your recent journal and take it further.",
    "Look at something in your domain you have never looked at.",
    "Send the brain the one thing it would most want to know from your domain "
    "right now -- and nothing it already heard from you.",
    "Tend your own memory: merge, delete, sharpen.",
)


def _angle_for(name: str, priority: Priority, now: float) -> str:
    """This cycle's angle, deterministic from the clock and the loop's name.

    Rotates on the hour, and is offset per loop so five loops waking together
    do not all take the same angle. No stored counter: a restart must not reset
    every loop to the first angle, which is exactly what a process that
    restarts often would do.
    """
    angles = BRAIN_ANGLES if priority == "brain" else EXPERT_ANGLES
    hours = int(now // 3600)
    offset = sum(ord(c) for c in name)
    return angles[(hours + offset) % len(angles)]

# A cycle that never reached the model has not consumed its inbox, so the notes
# stay unread for the next one. The statuses that *did* reach the model archive
# them even when the cycle went badly: a note the model choked on would
# otherwise be re-read forever, poisoning every future cycle.
CONSUMED_STATUSES: frozenset[str] = frozenset({"ok", "max_rounds", "error", "timeout"})

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
    # The model's own reasoning, when the provider hands it over: a summary
    # from Gemini, the real thing from a thinking model on the LAN. Empty for
    # every model that does not think out loud, which is most of them.
    thinking: str = ""


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


def _round_event(loop_name: str, round_: RoundTrace) -> dict:
    """A ``round_complete`` event, in the same shape ``_trace_json`` serves.

    One shape for a round whether it arrives by polling ``/trace`` or by the
    live feed means the frontend's ``RoundTrace`` type and renderers need no
    second parsing path -- only the envelope (``type``, ``loop``) is new.
    """
    return {
        "type": "round_complete",
        "loop": loop_name,
        "round": {
            "at": round_.at,
            "text": round_.text,
            "thinking": round_.thinking,
            "tool_calls": round_.tool_calls,
            "tool_results": round_.tool_results,
        },
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
        max_rounds: int = 16,
        max_tokens: int = MAX_TOKENS,
        call_timeout_s: int = 60,
        events: EventBus | None = None,
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
        self.max_tokens = max_tokens
        self.call_timeout_s = call_timeout_s
        self.events = events
        # The exact worst case for one round: every provider in the chain
        # tried in turn, each burning its own timeout on every attempt it is
        # allowed, before the round either answers or the chain gives up.
        # CHAIN_TIMEOUT_FACTOR used to approximate this with one flat number,
        # which under- or over-shot as soon as a provider's own timeout (e.g.
        # the lan: provider's much longer one) diverged from the chain's.
        self.chain_timeout_s = sum(
            (provider.call_timeout_s or chain.call_timeout_s) * max(1, provider.max_attempts)
            for provider in chain.providers
        ) or (call_timeout_s * CHAIN_TIMEOUT_FACTOR)

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
        # so the pause shows up in the journal like any other outcome. The
        # inbox is read first purely so the journal line can say how many
        # notes are still waiting; ``paused`` never marks them done.
        if self.pause_file.exists():
            notes = self.memory.unread_notes()
            return await self._finish("paused", "", 0, "paused by PAUSE file", next_wake_s, notes)

        try:
            # Read the inbox once. Reading it again inside read_context would
            # render whatever arrived in between -- and mark_done only archives
            # this list, so that note would be shown to the model and then left
            # unread, to be shown again next cycle.
            notes = self.memory.unread_notes()
            self.ctx.extras["owed_replies"] = owed_replies(notes)
            messages = self._opening_messages(notes)

            while rounds < self.max_rounds:
                # A tool can drop the PAUSE file mid-cycle, and a pause that
                # only takes effect at the next cycle boundary is no pause at
                # all when a cycle is a dozen provider calls long.
                if self.pause_file.exists():
                    status, summary = "paused", "paused mid-cycle"
                    log.info("[%s] PAUSE appeared mid-cycle, stopping", self.name)
                    break
                if self.events is not None:
                    # Fired before the call, not after: a slow provider can
                    # take longer than the gap between two other loops'
                    # entire cycles, and a live feed that only speaks once a
                    # round lands would sit silent through exactly the part
                    # someone watching it wants to see.
                    self.events.publish(
                        {"type": "round_started", "loop": self.name, "at": self.clock()}
                    )
                reply = await asyncio.wait_for(
                    self.chain.complete(
                        messages,
                        self.registry.specs_for(self.name),
                        self.max_tokens,
                        self.priority,
                        agent=self.name,
                    ),
                    timeout=self.chain_timeout_s,
                )
                rounds += 1
                model = reply.model
                trace.rounds.append(
                    RoundTrace(
                        at=self.clock(),
                        text=_trunc(reply.text or "", TRACE_TEXT_CHARS),
                        thinking=_trunc(reply.thinking or "", TRACE_TEXT_CHARS),
                        tool_calls=[
                            {"name": c.name, "args": _safe_args(c.args)} for c in reply.tool_calls
                        ],
                    )
                )
                if self.events is not None and not trace.rounds[-1].tool_calls:
                    # A round with no tool calls is already complete the
                    # instant it is appended -- there is no later point to
                    # publish from, so this is that round's only event.
                    self.events.publish(_round_event(self.name, trace.rounds[-1]))
                messages.append(
                    Message(
                        "assistant",
                        reply.text,
                        tool_calls=tuple(reply.tool_calls),
                        thought_signature=reply.thought_signature,
                        model=reply.model,
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
                if self.events is not None:
                    self.events.publish(_round_event(self.name, trace.rounds[-1]))
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
                f"chain exceeded {self.chain_timeout_s:g}s",
            )
            log.warning("[%s] cycle timed out after %d round(s)", self.name, rounds)
        except ChainExhausted as exc:
            status, summary = "no_budget", "no provider budget left"
            if exc.retry_at is not None:
                next_wake_s = max(MIN_BACKOFF_S, int(exc.retry_at - self.clock()))
            log.warning("[%s] no budget; sleeping %ds", self.name, next_wake_s)
        except asyncio.CancelledError:
            # Shutdown. Still close the books, then let the cancel propagate.
            # The inbox is left unread: a cycle cut short mid-thought may never
            # have acted on the notes, and the next run must see them again.
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
        angle = _angle_for(self.name, self.priority, self.clock())
        return [
            Message("system", system),
            Message(
                "user",
                f"Begin your think cycle. Current time: {now}.\n\nAngle for this cycle: {angle}",
            ),
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
        consumed = status in CONSUMED_STATUSES
        try:
            line = f"[{status}] model={model or '-'} rounds={rounds} {summary}"
            if not consumed and notes:
                line += f" ({len(notes)} notes left unread)"
            self.memory.append_journal(line)
            if consumed:
                self.memory.mark_done(notes)
            self.memory.purge_done()
            # Before the next cycle reads needs_compaction(): pruning here is
            # what keeps the journal rule clearable rather than a one-way latch.
            self.memory.prune_journal()
        except Exception:
            log.exception("[%s] could not write back memory", self.name)

        self.ctx.extras.pop("end_cycle", None)
        self.ctx.extras.pop("owed_refused", None)
        owed = self.ctx.extras.pop("owed_replies", None) or set()
        slack_out = self.ctx.extras.get("slack_out")
        if consumed and slack_out is not None:
            # The notes are archived now, so no later cycle will answer them:
            # say so on Filip's message straight away rather than leaving the
            # spinner for the watchdog. A bare DM's spinner sits under ``chat``.
            for topic in sorted(owed):
                try:
                    await slack_out.mark_unanswered(CHAT_TOPIC if topic == ANY_TOPIC else topic)
                except Exception:  # noqa: BLE001 - the reaction is decoration
                    log.exception("[%s] could not mark %s unanswered", self.name, topic)

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
        if self.events is not None:
            # Every cycle passes through here exactly once, on every path --
            # success, timeout, no budget, a raised exception, a mid-cycle
            # pause. A "round_started" published with nothing following it
            # (the call raised or timed out before a round could be appended)
            # would otherwise leave a live feed's "tänker…" entry for this
            # loop pending forever; this event is what tells it the cycle is
            # over and any such entry should resolve, one way or another.
            self.events.publish({"type": "cycle_ended", "loop": self.name, "status": status})
        return result


def _clamp_wake(minutes: int, heartbeat_s: int) -> int:
    return min(max(int(minutes) * 60, heartbeat_s // 2), MAX_WAKE_S)
