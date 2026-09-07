"""Quota ledger: the single accountant for every provider key.

Every agent loop asks the same ledger before it spends, so per-minute and
per-day budgets hold across the whole process. Experts starve first: they may
only spend while both remaining daily fractions are above
``EXPERT_FLOOR``, leaving the brain the last 40% of the day.

State is JSON on disk (``{"day": ..., "buckets": {...}}``), rewritten through a
temp file plus ``os.replace`` after every mutation, so a crash mid-write never
leaves a torn ledger.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")

WINDOW_S = 60.0
EXPERT_FLOOR = 0.4
BACKOFF_BASE_S = 60.0
BACKOFF_MAX_S = 3600.0
EXHAUSTED_AFTER_429 = 3
NOT_FOUND_DISABLE_S = 86400.0

Priority = Literal["brain", "expert"]


@dataclass(frozen=True)
class Limits:
    rpm: int
    tpm: int
    rpd: int

    @property
    def daily_tokens(self) -> int:
        """Synthetic daily token budget.

        The free tier has no daily token cap, but experts must starve before
        the brain does, which needs a denominator. ``tpm`` sustained for a
        tenth of the day is a deliberately conservative one.
        """
        return self.tpm * 60 * 24 // 10


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_at: float | None
    reason: str


def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, PACIFIC).date().isoformat()


def _next_midnight(ts: float) -> float:
    local = datetime.fromtimestamp(ts, PACIFIC)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=PACIFIC).timestamp()


class _Bucket:
    """Per-key counters: rolling minute windows plus daily totals."""

    def __init__(self, limits: Limits, requests_day: int = 0, tokens_day: int = 0):
        self.limits = limits
        self.requests_day = requests_day
        self.tokens_day = tokens_day
        self.consecutive_429 = 0
        self.blocked_until: float | None = None
        self.disabled_until: float | None = None
        # (timestamp, tokens) of each request inside the trailing minute
        self.recent: deque[tuple[float, int]] = deque()

    def prune(self, now: float) -> None:
        cutoff = now - WINDOW_S
        while self.recent and self.recent[0][0] <= cutoff:
            self.recent.popleft()

    def window_oldest(self) -> float:
        return self.recent[0][0]

    def window_tokens(self) -> int:
        return sum(tokens for _, tokens in self.recent)

    def reset_day(self, requests_day: int = 0, tokens_day: int = 0) -> None:
        self.requests_day = requests_day
        self.tokens_day = tokens_day
        self.recent.clear()
        # The 429 streak and its block are daily state: three strikes park the
        # key until the next Pacific midnight, so once that midnight arrives
        # both must go. Leaving the streak at 3 would let a single fresh 429
        # re-exhaust the key for another whole day. ``disabled_until`` is
        # deliberately untouched -- the 404 disable is a flat 24h and is not
        # tied to the quota day.
        self.consecutive_429 = 0
        self.blocked_until = None

    def to_json(self) -> dict:
        return {
            "requests_day": self.requests_day,
            "tokens_day": self.tokens_day,
            "consecutive_429": self.consecutive_429,
            "blocked_until": self.blocked_until,
            "disabled_until": self.disabled_until,
            "recent": [[ts, tokens] for ts, tokens in self.recent],
        }

    def load_json(self, raw: dict) -> None:
        self.requests_day = int(raw.get("requests_day", 0))
        self.tokens_day = int(raw.get("tokens_day", 0))
        self.consecutive_429 = int(raw.get("consecutive_429", 0))
        self.blocked_until = raw.get("blocked_until")
        self.disabled_until = raw.get("disabled_until")
        self.recent = deque((float(ts), int(tok)) for ts, tok in raw.get("recent", []))


class Ledger:
    def __init__(
        self,
        limits: dict[str, Limits],
        path: Path,
        clock: Callable[[], float] = time.time,
    ):
        self._limits = dict(limits)
        self._path = Path(path)
        self._clock = clock
        self._buckets = {key: _Bucket(lim) for key, lim in self._limits.items()}
        self._day = _day_of(self._clock())
        self._load()

    # ---------- persistence ----------

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            day = raw["day"]
            buckets = raw["buckets"]
            if not isinstance(day, str) or not isinstance(buckets, dict):
                raise ValueError("malformed ledger")
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError):
            # Corrupt: we cannot know what was already spent today, so assume
            # half the day is gone rather than handing out a fresh budget.
            for bucket in self._buckets.values():
                bucket.reset_day(
                    requests_day=bucket.limits.rpd // 2,
                    tokens_day=bucket.limits.daily_tokens // 2,
                )
            self._save()
            return

        if day != self._day:
            # Valid but stale: the day genuinely rolled over, so zero counters.
            return

        for key, bucket in self._buckets.items():
            entry = buckets.get(key)
            if isinstance(entry, dict):
                try:
                    bucket.load_json(entry)
                except (ValueError, TypeError):
                    bucket.reset_day()

    def _save(self) -> None:
        body = json.dumps(
            {
                "day": self._day,
                "buckets": {key: b.to_json() for key, b in self._buckets.items()},
            }
        )
        tmp = self._path.with_name(self._path.name + ".tmp")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, self._path)

    # ---------- day handling ----------

    def _roll_day(self, now: float) -> None:
        day = _day_of(now)
        if day == self._day:
            return
        self._day = day
        for bucket in self._buckets.values():
            bucket.reset_day()
        # The roll is a real state change: without persisting it, a restart
        # reloads yesterday's day and daily counts from disk and the budget
        # stays spent until something else happens to write.
        self._save()

    # ---------- queries ----------

    def keys(self) -> list[str]:
        return list(self._limits)

    def can_spend(self, key: str, priority: Priority, est_tokens: int = 4000) -> Decision:
        now = self._clock()
        self._roll_day(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            return Decision(False, None, "disabled")

        if bucket.disabled_until is not None and now < bucket.disabled_until:
            return Decision(False, bucket.disabled_until, "disabled")

        if bucket.blocked_until is not None and now < bucket.blocked_until:
            reason = "exhausted" if bucket.consecutive_429 >= EXHAUSTED_AFTER_429 else "cooldown"
            return Decision(False, bucket.blocked_until, reason)

        limits = bucket.limits
        if bucket.requests_day >= limits.rpd or bucket.tokens_day >= limits.daily_tokens:
            return Decision(False, _next_midnight(now), "rpd")

        if priority == "expert":
            req_left, tok_left = self._fractions(bucket)
            if not (req_left > EXPERT_FLOOR and tok_left > EXPERT_FLOOR):
                return Decision(False, _next_midnight(now), "priority")

        bucket.prune(now)
        if len(bucket.recent) >= limits.rpm:
            return Decision(False, bucket.window_oldest() + WINDOW_S, "rpm")
        # tpm throttles against what the trailing minute already consumed.
        # An estimate larger than the whole per-minute budget can never fit in
        # any window, so it is not a reason to wait -- only already-spent
        # tokens are. Draining the window is the only thing waiting fixes.
        spent = bucket.window_tokens()
        if spent > 0 and est_tokens <= limits.tpm and spent + est_tokens > limits.tpm:
            return Decision(False, bucket.window_oldest() + WINDOW_S, "tpm")

        return Decision(True, None, "ok")

    def remaining_fraction(self, key: str) -> tuple[float, float]:
        self._roll_day(self._clock())
        bucket = self._buckets.get(key)
        if bucket is None:
            return (0.0, 0.0)
        return self._fractions(bucket)

    @staticmethod
    def _fractions(bucket: _Bucket) -> tuple[float, float]:
        limits = bucket.limits
        req = 1.0 - bucket.requests_day / limits.rpd if limits.rpd else 0.0
        tok = 1.0 - bucket.tokens_day / limits.daily_tokens if limits.daily_tokens else 0.0
        return (max(0.0, req), max(0.0, tok))

    def next_available_at(self, priority: Priority) -> float | None:
        """Earliest time any key could serve this priority, or None if never."""
        now = self._clock()
        best: float | None = None
        for key in self._limits:
            decision = self.can_spend(key, priority)
            candidate = now if decision.allowed else decision.retry_at
            if candidate is None:
                continue
            if best is None or candidate < best:
                best = candidate
        return best

    @property
    def day(self) -> str:
        """The Pacific quota day these counters belong to."""
        self._roll_day(self._clock())
        return self._day

    def snapshot(self) -> dict:
        self._roll_day(self._clock())
        return {
            "day": self._day,
            "buckets": {key: b.to_json() for key, b in self._buckets.items()},
        }

    # ---------- mutations ----------

    def record(self, key: str, prompt_tokens: int, completion_tokens: int) -> None:
        now = self._clock()
        self._roll_day(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            return
        tokens = int(prompt_tokens) + int(completion_tokens)
        bucket.prune(now)
        bucket.recent.append((now, tokens))
        bucket.requests_day += 1
        bucket.tokens_day += tokens
        self._save()

    def record_429(self, key: str, retry_after_s: float | None) -> None:
        now = self._clock()
        self._roll_day(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            return
        bucket.consecutive_429 += 1
        if bucket.consecutive_429 >= EXHAUSTED_AFTER_429:
            # Three in a row means the key is done for the day, whatever the
            # server suggested; wait for the daily reset.
            bucket.blocked_until = _next_midnight(now)
        elif retry_after_s is not None:
            bucket.blocked_until = now + float(retry_after_s)
        else:
            step = BACKOFF_BASE_S * (2 ** (bucket.consecutive_429 - 1))
            bucket.blocked_until = now + min(step, BACKOFF_MAX_S)
        self._save()

    def record_not_found(self, key: str) -> None:
        now = self._clock()
        self._roll_day(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            return
        bucket.disabled_until = now + NOT_FOUND_DISABLE_S
        self._save()

    def record_ok(self, key: str) -> None:
        self._roll_day(self._clock())
        bucket = self._buckets.get(key)
        if bucket is None:
            return
        bucket.consecutive_429 = 0
        bucket.blocked_until = None
        self._save()
