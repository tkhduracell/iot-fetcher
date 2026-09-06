"""The gate between an agent wanting to act and something actually happening.

The brain never calls an executor. It calls ``propose``, which writes a
pending proposal to ``outbox/<id>.json`` and posts it to Slack; a human either
reacts with a check mark or does not. Everything that matters lives in that
file rather than in memory, so a restart mid-approval loses nothing and a
second process reads the same truth.

Two properties are worth stating because the tests pin them:

* **Exactly once.** ``on_reaction`` only acts on a proposal whose stored status
  is still ``pending``. Slack delivers duplicates and a human can double-tap,
  and neither may make the speaker talk twice.
* **Nothing silent.** Every terminal outcome -- executed, failed, rejected,
  blocked, expired -- drops a note into the brain's inbox, so the agent reads
  what became of its request on its next cycle instead of assuming.
"""

from __future__ import annotations

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
            status="pending",
        )
        if self.on_message is not None:
            proposal.slack_ts = await self.on_message(
                topic,
                f"Proposal {proposal.id} ({kind}): {reason}\n\n"
                f"```{json.dumps(payload)}```\n"
                "React ✅ to approve, ❌ to reject.",
            )
        self._store(proposal)
        return proposal

    # -- reacting ------------------------------------------------------

    async def on_reaction(self, slack_ts: str, emoji: str) -> Proposal | None:
        if emoji not in (APPROVE_EMOJI, REJECT_EMOJI):
            return None
        proposal = self._find_pending(slack_ts)
        if proposal is None:
            return None

        if emoji == REJECT_EMOJI:
            return self._finish(proposal, "rejected", "")

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

    # -- reading -------------------------------------------------------

    def pending(self) -> list[Proposal]:
        return [p for p in self._all() if p.status == "pending"]

    # -- internals -----------------------------------------------------

    def _path(self, proposal_id: str) -> Path:
        return self.brain.outbox_dir / f"{proposal_id}.json"

    def _all(self) -> list[Proposal]:
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
        proposal.status = status
        proposal.result = result
        self._store(proposal)
        self.brain.drop_note(NOTE_SENDER, f"proposal {proposal.id} {status}: {result}")
        return proposal


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
