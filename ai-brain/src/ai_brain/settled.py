"""Settled topics: what the brain has decided, and the evidence that keeps it decided.

Two failures from the week of 2026-11-01 shaped this:

* **Re-reports woke Opus.** energy re-reported the Sigen night grid charge --
  settled on 11-01 -- six more times that week, and every report was a
  ``send_note`` that woke the brain for a cycle whose whole outcome was "this
  is settled, stop". A third of the brain's cycles were one round of that.
  The settled list lived only as prose in ``goals.md``, which the harness
  cannot read, so nothing but an Opus cycle could say no.
* **A settled belief nobody tested.** "Nightly restarts normal" sat in the
  settled list while no nightly stack restart exists (cron only restarts three
  containers at 02:00), and it shaped what the brain told Filip about the
  11-08 outage. A claim that is never re-checked stays true forever.

So a settled topic is a record, not a sentence: a ``topic`` slug, the
``claim``, and optionally ``evidence`` -- a PromQL expression that returns a
non-zero value for as long as the claim holds. The harness uses it twice:

* ``send_note`` to the brain on a settled topic is not delivered unless the
  sender marks it ``urgent`` (the pattern broke). The sender is told why, so
  it learns the list instead of the brain repeating it.
* A weekly check re-runs every ``evidence`` query. One that comes back empty
  or zero reopens the topic and tells the brain. A topic settled with no
  evidence is an *assumption*: it expires after ``ASSUMPTION_MAX_AGE_S`` and
  the brain is asked to re-settle it with evidence or let it go.

Stored as one JSON file next to the brain's memory, written atomically.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

CHECK_EVERY_S = 7 * 86400
ASSUMPTION_MAX_AGE_S = 30 * 86400
MAX_TOPICS = 60
MAX_CLAIM_CHARS = 300
MAX_EVIDENCE_CHARS = 500
SETTLED_CHECK_SENDER = "settled-check"

# The answer an evidence query gave: True holds, False failed, None unknown
# (VM unreachable, a bad query) -- unknown is never a reason to reopen.
EvidenceFn = Callable[[str], Awaitable[tuple[bool | None, str]]]


def topic_slug(topic: str) -> str:
    """A topic as the one comparable form: lowercase words joined by ``-``."""
    return re.sub(r"[^a-z0-9]+", "-", str(topic).lower()).strip("-")[:64].strip("-")


@dataclass
class Settled:
    topic: str
    claim: str
    evidence: str = ""
    since: float = 0.0
    checked_at: float = 0.0
    # "holds" | "unchecked" | "assumption"
    status: str = "unchecked"
    last_result: str = ""
    # How many notes on this topic were held back rather than delivered.
    suppressed: int = 0

    @property
    def is_assumption(self) -> bool:
        return not self.evidence


class SettledRegistry:
    def __init__(self, root: Path, clock: Callable[[], datetime]) -> None:
        self.path = root / "settled.json"
        self.clock = clock

    # -- storage -------------------------------------------------------

    def _read(self) -> dict[str, Settled]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out: dict[str, Settled] = {}
        if not isinstance(raw, dict):
            return out
        for topic, record in raw.items():
            if not isinstance(record, dict):
                continue
            try:
                entry = Settled(**{k: v for k, v in record.items() if k in Settled.__annotations__})
            except TypeError:
                continue
            out[topic] = entry
        return out

    def _write(self, entries: dict[str, Settled]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps({t: asdict(e) for t, e in entries.items()}, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def _now(self) -> float:
        return self.clock().timestamp()

    # -- the brain's edits ----------------------------------------------

    def settle(self, topic: str, claim: str, evidence: str = "") -> Settled:
        slug = topic_slug(topic)
        if not slug:
            raise ValueError("topic must contain letters or digits")
        claim = " ".join(str(claim).split())[:MAX_CLAIM_CHARS]
        if not claim:
            raise ValueError("claim must say what is settled")
        evidence = str(evidence or "").strip()[:MAX_EVIDENCE_CHARS]
        entries = self._read()
        if slug not in entries and len(entries) >= MAX_TOPICS:
            raise ValueError(
                f"already {MAX_TOPICS} settled topics; unsettle one that no longer matters first"
            )
        now = self._now()
        entry = Settled(
            topic=slug,
            claim=claim,
            evidence=evidence,
            since=now,
            status="unchecked" if evidence else "assumption",
        )
        entries[slug] = entry
        self._write(entries)
        return entry

    def unsettle(self, topic: str) -> Settled | None:
        entries = self._read()
        entry = entries.pop(topic_slug(topic), None)
        if entry is not None:
            self._write(entries)
        return entry

    def all(self) -> list[Settled]:
        return sorted(self._read().values(), key=lambda e: e.topic)

    # -- the note gate ----------------------------------------------------

    def match(self, topic: str) -> Settled | None:
        """The settled entry ``topic`` is about, if any.

        Exact slug first; then a more specific variant, so
        ``sigen-night-grid-charge-11-07`` is still the settled
        ``sigen-night-grid-charge`` -- an expert dating its topic is the
        commonest way a re-report would slip past an exact match. Never the
        other way round: a broad ``sigen`` note may be about something else
        entirely, and a wrong hold silences a real report.
        """
        slug = topic_slug(topic)
        if not slug:
            return None
        entries = self._read()
        if slug in entries:
            return entries[slug]
        for key, entry in entries.items():
            if slug.startswith(key + "-"):
                return entry
        return None

    def count_suppressed(self, topic: str) -> None:
        entries = self._read()
        entry = entries.get(topic)
        if entry is not None:
            entry.suppressed += 1
            self._write(entries)

    # -- the weekly check -------------------------------------------------

    async def check(self, evidence: EvidenceFn) -> list[str]:
        """Re-test what is due. Returns one line per topic that was reopened.

        A failed check removes the topic: it is no longer settled, so the next
        note on it reaches the brain again, which is the point. An unknown
        result (VM down) leaves the topic as it was and retries next pass.
        """
        now = self._now()
        entries = self._read()
        reopened: list[str] = []
        changed = False
        for slug, entry in list(entries.items()):
            if entry.is_assumption:
                if now - entry.since >= ASSUMPTION_MAX_AGE_S:
                    del entries[slug]
                    changed = True
                    reopened.append(
                        f"- {slug}: assumption expired after "
                        f"{ASSUMPTION_MAX_AGE_S // 86400} days with no evidence "
                        f"(claim: {entry.claim})"
                    )
                continue
            if entry.checked_at and now - entry.checked_at < CHECK_EVERY_S:
                continue
            holds, detail = await evidence(entry.evidence)
            if holds is None:
                log.info("[settled] %s: evidence check inconclusive (%s)", slug, detail)
                continue
            changed = True
            if holds:
                entry.checked_at = now
                entry.status = "holds"
                entry.last_result = detail[:200]
            else:
                del entries[slug]
                reopened.append(
                    f"- {slug}: evidence no longer holds ({detail[:200]}). "
                    f"Claim was: {entry.claim}. Evidence: {entry.evidence}"
                )
        if changed:
            self._write(entries)
        return reopened


def reopened_note(lines: list[str]) -> str:
    return (
        "topic: settled-check\n"
        "Weekly check of settled topics. These are open again -- notes on them "
        "will reach you, and they need a fresh look or new evidence before you "
        "settle them again:\n" + "\n".join(lines)
    )


def vm_evidence(
    request_fn: Callable[..., Awaitable[tuple[object, str | None]]],
    vm_url: str,
    token: str,
) -> EvidenceFn:
    """Evidence = an instant PromQL query that returns a non-zero value.

    Holds when at least one series comes back with a value other than 0.
    An empty result is a failure, not an unknown: "the thing that proved this
    is gone" is exactly what the check is for.
    """

    async def run(promql: str) -> tuple[bool | None, str]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response, problem = await request_fn(
            "GET",
            f"{vm_url.rstrip('/')}/api/v1/query",
            label="settled_check",
            params={"query": promql},
            headers=headers,
        )
        if problem is not None or response is None:
            return None, problem or "no response"
        try:
            body = response.json()  # type: ignore[attr-defined]
        except ValueError:
            return None, "non-JSON body"
        if not isinstance(body, dict) or body.get("status") != "success":
            return None, str(body.get("error") if isinstance(body, dict) else "bad body")[:200]
        result = (body.get("data") or {}).get("result") or []
        values: list[float] = []
        for series in result:
            try:
                values.append(float(series["value"][1]))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        if not values:
            return False, "query returned no series"
        nonzero = [v for v in values if v != 0]
        if not nonzero:
            return False, f"all {len(values)} series are 0"
        return True, f"{len(nonzero)} series non-zero, first={nonzero[0]:g}"

    return run
