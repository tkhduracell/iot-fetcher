"""The gate between an agent wanting to act and something actually happening.

The brain never calls an executor. It calls ``propose``, which writes a
pending proposal to ``outbox/<id>.json`` and posts it to Slack; a human either
reacts with a check mark or does not. Everything that matters lives in that
file rather than in memory, so a restart mid-approval loses nothing and a
second process reads the same truth.

Two properties are worth stating because the tests pin them:

* **Exactly once.** ``on_reaction`` only acts on a proposal whose stored status
  is still ``pending``. Slack delivers duplicates and a human can double-tap,
  and neither may make the speaker talk twice. Because an executor is awaited,
  two deliveries can be in flight at once, so the transition out of ``pending``
  is persisted as ``executing`` *before* the await, under a per-proposal lock:
  the second caller finds a non-pending file and stops. A process that dies
  mid-execution leaves the proposal ``executing`` forever, which is the safe
  side of the trade -- it is neither retried nor re-approved by a later
  reaction, and it never shows up as pending again. ``recover_stale`` closes
  those out at startup as ``failed``, so the agent at least learns the
  outcome is unknown instead of never hearing back.
* **Nothing silent.** Every terminal outcome -- executed, failed, rejected,
  blocked, expired -- drops a note into the brain's inbox, so the agent reads
  what became of its request on its next cycle instead of assuming.
* **A stored pending proposal always has a reactable message.** ``SlackOut.post``
  returns ``"queued"`` when Slack was unreachable and the text went to the retry
  outbox instead. Storing that as the proposal's ``slack_ts`` would be the worst
  of both worlds: the queue later delivers the message with a real ``ts`` that
  nothing writes back, so Filip's checkmark matches no proposal and the request
  sits pending for 24h and expires -- silently, on the one gate the whole design
  rests on. A queued post is therefore a *failed* proposal: it is written
  terminal, the note explains why, and ``propose`` raises so the model gets an
  error and can simply propose again on a later cycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_brain.executors import Executors, QuietHours
from ai_brain.memory import MemoryDir

log = logging.getLogger(__name__)

# What may be proposed at all, and which payload key each kind needs. An agent
# cannot widen this from inside a cycle -- adding a capability is a code change.
# Every kind names at least one required key, which is what lets the payload
# check below reject a non-dict payload without a separate type branch.
KINDS: dict[str, tuple[str, ...]] = {
    "sonos_say": ("text",),
    "ha_todo_add": ("item",),
}

# The only status ``pending()`` reports and the only one a reaction may act on.
# Everything else -- including the transient ``executing`` -- is terminal as far
# as the gate is concerned.
PENDING = "pending"
EXECUTING = "executing"
STATUSES = frozenset(
    {
        PENDING,
        EXECUTING,
        "executed",
        "failed",
        "rejected",
        "blocked_quiet_hours",
        "expired",
    }
)

# What ``SlackOut.post`` returns when the post failed and was queued instead.
QUEUED = "queued"
UNREACHABLE = "slack unavailable, not proposed"
NOT_CONFIGURED = "slack not configured, not proposed"
RESTARTED = "process restarted mid-execution; outcome unknown"

APPROVE_EMOJI = "white_check_mark"
REJECT_EMOJI = "x"
EXPIRE_AFTER = timedelta(hours=24)
NOTE_SENDER = "approvals"


@dataclass
class Proposal:
    id: str
    kind: str
    payload: dict
    reason: str
    topic: str
    created: str
    status: str
    slack_ts: str = ""
    result: str = ""


class Approvals:
    def __init__(
        self,
        brain: MemoryDir,
        executors: Executors,
        clock: Callable[[], datetime],
        on_message: Callable[[str, str], Awaitable[str]] | None = None,
    ) -> None:
        self.brain = brain
        self.executors = executors
        self.clock = clock
        self.on_message = on_message
        # One lock per proposal id, created on demand. Without it two coroutines
        # can both read ``pending`` from disk in the same tick before either
        # writes ``executing``, and both would execute.
        self._locks: dict[str, asyncio.Lock] = {}

    # -- proposing -----------------------------------------------------

    async def propose(self, kind: str, payload: dict, reason: str, topic: str) -> Proposal:
        required = KINDS.get(kind)
        if required is None:
            raise ValueError(f"unknown kind: {kind} (known: {', '.join(sorted(KINDS))})")
        # A non-dict payload is a validation failure like any other, not a
        # TypeError: the caller is a language model filling in a JSON schema,
        # and every such mistake has to come back as one kind of error the
        # propose tool can turn into err(...).
        keys = payload.keys() if isinstance(payload, dict) else ()
        for key in required:
            if key not in keys:
                raise ValueError(f"{kind} payload is missing {key!r}")

        now = self.clock()
        proposal = Proposal(
            id=f"{now:%Y%m%dT%H%M%S}-{kind}-{secrets.token_hex(2)}",
            kind=kind,
            payload=dict(payload),
            reason=reason,
            topic=topic,
            created=_iso(now),
            status=PENDING,
        )
        # No Slack at all is the same situation as a Slack that would not take
        # the message: there is no ✅ for anyone to press, so storing this as
        # pending would leave a request that can only ever expire, 24h later,
        # in silence.
        if self.on_message is None:
            self._finish(proposal, "failed", NOT_CONFIGURED)
            raise RuntimeError("slack not configured")

        try:
            posted = await self.on_message(
                topic,
                f"Proposal {proposal.id} ({kind}): {reason}\n\n"
                f"```{json.dumps(payload)}```\n"
                "React ✅ to approve, ❌ to reject.",
            )
        except Exception as exc:
            # Anything the poster raises -- the hourly cap, a transport error,
            # a client that is not connected -- means nothing was posted. The
            # exception type is deliberately not inspected: approvals must not
            # import the Slack layer to name its errors.
            log.warning("[approvals] posting %s failed: %s", proposal.id, exc)
            self._finish(proposal, "failed", f"slack unavailable: {exc}")
            raise RuntimeError("slack unavailable") from exc

        # "queued" means Slack was down and the text went to the retry
        # outbox; an empty return means a client that posted nothing at
        # all. Neither leaves a message anyone can react to, so neither may
        # become a pending proposal.
        if not posted or posted == QUEUED:
            self._finish(proposal, "failed", UNREACHABLE)
            raise RuntimeError("slack unavailable")
        proposal.slack_ts = posted
        self._store(proposal)
        return proposal

    # -- reacting ------------------------------------------------------

    async def on_reaction(self, slack_ts: str, emoji: str) -> Proposal | None:
        if emoji not in (APPROVE_EMOJI, REJECT_EMOJI):
            return None
        # Look up unlocked to learn *which* proposal this is, then redo the
        # check while holding that proposal's lock -- the first read is a hint,
        # the second is the decision.
        found = self._find_pending(slack_ts)
        if found is None:
            return None

        async with self._lock_for(found.id):
            proposal = self._find_pending(slack_ts)
            if proposal is None:
                return None
            # ``expire`` only runs every ten minutes, so a proposal can still be
            # pending well past its deadline. A reaction on a 25-hour-old
            # request must not run it: the world it was reasoned about is gone.
            if self.clock() - _parse(proposal.created) > EXPIRE_AFTER:
                return self._finish(proposal, "expired", "no reaction within 24h")
            if emoji == REJECT_EMOJI:
                return self._finish(proposal, "rejected", "")
            # Claim it on disk before awaiting anything, so a concurrent
            # delivery that reaches _find_pending after the lock is released
            # sees a non-pending proposal.
            proposal.status = EXECUTING
            self._store(proposal)

        try:
            result = await self.executors.run(proposal.kind, proposal.payload)
        except QuietHours as exc:
            return self._finish(
                proposal, "blocked_quiet_hours", f"not run during quiet hours: {exc}"
            )
        except Exception as exc:
            log.warning("[approvals] %s failed", proposal.id, exc_info=True)
            return self._finish(proposal, "failed", f"{type(exc).__name__}: {exc}")
        return self._finish(proposal, "executed", result)

    # -- expiry --------------------------------------------------------

    async def expire(self) -> list[Proposal]:
        cutoff = self.clock() - EXPIRE_AFTER
        expired = []
        for proposal in self.pending():
            if _parse(proposal.created) < cutoff:
                expired.append(self._finish(proposal, "expired", "no reaction within 24h"))
        return expired

    # -- startup -------------------------------------------------------

    def recover_stale(self) -> list[Proposal]:
        """Close out proposals left ``executing`` by a process that died.

        ``executing`` is written before the executor is awaited, so a restart
        mid-execution leaves that status on disk forever: the proposal is not
        pending, so nothing expires it, and no note ever says what became of
        it. The outcome genuinely is unknown -- the Sonos may well have spoken
        -- so say exactly that rather than retrying it.
        """
        stale = [p for p in self.all() if p.status == EXECUTING]
        for proposal in stale:
            log.warning("[approvals] %s was executing at startup; marking failed", proposal.id)
            self._finish(proposal, "failed", RESTARTED)
        return stale

    # -- reading -------------------------------------------------------

    def all(self) -> list[Proposal]:
        """Every proposal on disk, oldest id first. Unreadable files are skipped.

        The ids are timestamped, so sorting the filenames sorts by age -- which
        is what a reader wants and what ``pending`` has always relied on.
        """
        directory = self.brain.outbox_dir
        if not directory.exists():
            return []
        proposals = []
        for path in sorted(directory.glob("*.json")):
            try:
                proposals.append(Proposal(**json.loads(path.read_text(encoding="utf-8"))))
            except (ValueError, TypeError):
                log.warning("[approvals] ignoring unreadable proposal %s", path.name)
        return proposals

    def pending(self) -> list[Proposal]:
        return [p for p in self.all() if p.status == PENDING]

    # -- internals -----------------------------------------------------

    def _lock_for(self, proposal_id: str) -> asyncio.Lock:
        lock = self._locks.get(proposal_id)
        if lock is None:
            lock = self._locks[proposal_id] = asyncio.Lock()
        return lock

    def _path(self, proposal_id: str) -> Path:
        return self.brain.outbox_dir / f"{proposal_id}.json"

    def _find_pending(self, slack_ts: str) -> Proposal | None:
        if not slack_ts:
            return None
        for proposal in self.pending():
            if proposal.slack_ts == slack_ts:
                return proposal
        return None

    def _store(self, proposal: Proposal) -> None:
        self.brain.outbox_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(proposal.id)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(proposal), indent=2), encoding="utf-8")
        tmp.replace(path)

    def _finish(self, proposal: Proposal, status: str, result: str) -> Proposal:
        # A typo'd status would silently make a proposal un-pending and
        # un-terminal at once; fail loudly at the one place status is set.
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        proposal.status = status
        proposal.result = result
        self._store(proposal)
        # Terminal: nothing will contend for this id again, and _locks would
        # otherwise grow by one entry per proposal for the life of the process.
        self._locks.pop(proposal.id, None)
        self.brain.drop_note(NOTE_SENDER, f"proposal {proposal.id} {status}: {result}")
        return proposal


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
