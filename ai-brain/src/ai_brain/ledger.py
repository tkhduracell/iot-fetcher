"""Quota ledger: the single accountant for every provider key.

Every agent loop asks the same ledger before it spends, so per-minute and
per-day budgets hold across the whole process. Experts starve first: they may
only spend while both remaining daily fractions are above
``EXPERT_FLOOR``, leaving the brain the last 40% of the day.

A paid key (anthropic:) is budgeted in dollars instead, by a USD *pool* that
every key drawing on the same prepaid credit shares. The pool's period is the
plan's billing cycle, not the calendar month or the Pacific day: credits land
on the billing day and expire unused on the next one. Each day gets an
allowance of what is left divided by the days left, frozen at the day's first
look -- so an expensive day shrinks the days after it, and a quiet one lets
them spend more, and the month ends close to spent rather than either early
or with credit left to expire. The expert floor holds the brain's share of
the allowance by capping what *experts* spend (at most 60% of the day), not
by counting the brain's own spend against them: on a pool the brain is the
big spender, and a floor on the total would let a busy brain morning shut
every expert out of the paid models for the rest of the day.

State is JSON on disk (``{"day": ..., "buckets": {...}, "pools": {...}}``),
rewritten through a temp file plus ``os.replace`` after every mutation, so a
crash mid-write never leaves a torn ledger.
"""

from __future__ import annotations

import calendar
import json
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
# How long an out-of-credit key is parked before it is tried again. Not "until
# the period resets": credits arrive *after* the plan payment clears, which
# may be hours past the billing day's midnight, and a key parked for a month
# by one early retry would sit out the whole cycle. A refused call is free, so
# trying again a few times a day costs nothing.
EXHAUSTED_RETRY_S = 6 * 3600.0

Priority = Literal["brain", "expert"]


@dataclass(frozen=True)
class Limits:
    rpm: int
    tpm: int
    rpd: int
    # Which priorities may spend on this key; None means everyone. This is how
    # a model is reserved for the brain (or kept away from it) -- see
    # BRAIN_MODELS / EXPERT_MODELS in config.py.
    loops: frozenset[str] | None = None
    # The USD pool this key spends from, if it is paid in dollars. See Pool.
    usd_pool: str | None = None

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


@dataclass(frozen=True)
class Pool:
    """A dollar budget per billing cycle, shared by every key naming it."""

    monthly_usd: float
    # Day of month the credit refreshes. A month without that day (31 in
    # April) uses its last day.
    billing_day: int = 1


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, by: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + by
    return index // 12, index % 12 + 1


def period_start(today: date, billing_day: int) -> date:
    """The billing cycle ``today`` falls in starts on this date."""
    this = _clamped(today.year, today.month, billing_day)
    if today >= this:
        return this
    return _clamped(*_shift_month(today.year, today.month, -1), billing_day)


def next_period_start(start: date, billing_day: int) -> date:
    return _clamped(*_shift_month(start.year, start.month, 1), billing_day)


def _midnight(day: date) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=PACIFIC).timestamp()


class _PoolState:
    def __init__(self, pool: Pool):
        self.pool = pool
        self.period_start = ""
        self.usd_period = 0.0
        self.usd_day = 0.0
        # The experts' share of usd_day, which the expert floor caps.
        self.usd_day_expert = 0.0
        # The day's allowance, frozen at its first look; None until then.
        self.day_allowance: float | None = None
        self.exhausted_until: float | None = None

    def to_json(self) -> dict:
        return {
            "period_start": self.period_start,
            "usd_period": self.usd_period,
            "usd_day": self.usd_day,
            "usd_day_expert": self.usd_day_expert,
            "day_allowance": self.day_allowance,
            "exhausted_until": self.exhausted_until,
        }

    def load_json(self, raw: dict) -> None:
        self.period_start = str(raw.get("period_start", ""))
        self.usd_period = float(raw.get("usd_period", 0.0))
        self.usd_day = float(raw.get("usd_day", 0.0))
        self.usd_day_expert = float(raw.get("usd_day_expert", 0.0))
        allowance = raw.get("day_allowance")
        self.day_allowance = None if allowance is None else float(allowance)
        self.exhausted_until = raw.get("exhausted_until")

    def reset_day(self) -> None:
        self.usd_day = 0.0
        self.usd_day_expert = 0.0
        self.day_allowance = None


def _date_of(ts: float) -> date:
    return datetime.fromtimestamp(ts, PACIFIC).date()


def _day_of(ts: float) -> str:
    return _date_of(ts).isoformat()


def _next_midnight(ts: float) -> float:
    return _midnight(_date_of(ts) + timedelta(days=1))


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
        pools: dict[str, Pool] | None = None,
    ):
        self._limits = dict(limits)
        self._path = Path(path)
        self._clock = clock
        self._buckets = {key: _Bucket(lim) for key, lim in self._limits.items()}
        self._pools = {name: _PoolState(pool) for name, pool in (pools or {}).items()}
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
            # Same for the month's dollars: half of it, gone.
            for state in self._pools.values():
                state.usd_period = state.pool.monthly_usd / 2
            self._save()
            return

        # Pools first: their spend belongs to the billing cycle, not the day,
        # so a stale day must not throw the month's spend away with it.
        pools = raw.get("pools")
        if isinstance(pools, dict):
            for name, state in self._pools.items():
                entry = pools.get(name)
                if isinstance(entry, dict):
                    try:
                        state.load_json(entry)
                    except (ValueError, TypeError):
                        state.usd_period = state.pool.monthly_usd / 2
                    if day != self._day:
                        state.reset_day()

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

    def _state(self) -> dict:
        state: dict = {
            "day": self._day,
            "buckets": {key: b.to_json() for key, b in self._buckets.items()},
        }
        if self._pools:
            state["pools"] = {name: p.to_json() for name, p in self._pools.items()}
        return state

    def _save(self) -> None:
        body = json.dumps(self._state())
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
        for state in self._pools.values():
            state.reset_day()
        # The roll is a real state change: without persisting it, a restart
        # reloads yesterday's day and daily counts from disk and the budget
        # stays spent until something else happens to write.
        self._save()

    # ---------- pools ----------

    def _pool_for(self, bucket: _Bucket) -> _PoolState | None:
        name = bucket.limits.usd_pool
        return self._pools.get(name) if name else None

    def _sync_pool(self, state: _PoolState, now: float) -> tuple[float, date]:
        """Start a new billing cycle if one has begun, then return today's
        allowance and the next cycle's first day."""
        today = _date_of(now)
        billing_day = state.pool.billing_day
        start = period_start(today, billing_day)
        nxt = next_period_start(start, billing_day)
        changed = False
        if state.period_start != start.isoformat():
            # An empty period_start is a pool that has never been synced (a
            # fresh or corrupt ledger): adopt the current cycle without
            # zeroing what the load already decided was spent.
            if state.period_start:
                state.usd_period = 0.0
                state.exhausted_until = None
                state.reset_day()
            state.period_start = start.isoformat()
            changed = True
        if state.day_allowance is None:
            days_left = max(1, (nxt - today).days)
            left = max(0.0, state.pool.monthly_usd - state.usd_period)
            state.day_allowance = left / days_left
            changed = True
        if changed:
            self._save()
        return state.day_allowance, nxt

    def _pool_decision(
        self, state: _PoolState, priority: Priority, now: float
    ) -> Decision | None:
        """Why a pool forbids spending now, or None when it allows it."""
        allowance, nxt = self._sync_pool(state, now)
        if state.exhausted_until is not None and now < state.exhausted_until:
            return Decision(False, state.exhausted_until, "exhausted")
        if state.usd_period >= state.pool.monthly_usd:
            return Decision(False, _midnight(nxt), "usd_month")
        if state.usd_day >= allowance:
            return Decision(False, _next_midnight(now), "usd_day")
        if priority == "expert" and state.usd_day_expert >= allowance * (1.0 - EXPERT_FLOOR):
            return Decision(False, _next_midnight(now), "priority")
        return None

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
        if limits.loops is not None and priority not in limits.loops:
            return Decision(False, None, "reserved")

        pool = self._pool_for(bucket)
        if pool is not None:
            refused = self._pool_decision(pool, priority, now)
            if refused is not None:
                return refused

        if bucket.requests_day >= limits.rpd or bucket.tokens_day >= limits.daily_tokens:
            return Decision(False, _next_midnight(now), "rpd")

        # The floor holds the last 40% back for the brain, so it only means
        # something on a key the brain may spend. On an expert-only key
        # (EXPERT_MODELS without BRAIN_MODELS) it just strands that share.
        brain_may_use = limits.loops is None or "brain" in limits.loops
        if priority == "expert" and brain_may_use:
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

    def limits(self, key: str) -> Limits | None:
        """The configured budget for a key, or None when it has no bucket.

        The API needs the denominators to render "used of limit"; the
        fractions alone cannot say how big the day is.
        """
        return self._limits.get(key)

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
        return self._state()

    def pools(self) -> list[dict]:
        """Each USD pool's spend against its cycle and today's allowance."""
        now = self._clock()
        self._roll_day(now)
        out = []
        for name, state in self._pools.items():
            allowance, _ = self._sync_pool(state, now)
            out.append(
                {
                    "pool": name,
                    "period_start": state.period_start,
                    "usd_period": round(state.usd_period, 4),
                    "usd_limit": state.pool.monthly_usd,
                    "usd_day": round(state.usd_day, 4),
                    "usd_day_expert": round(state.usd_day_expert, 4),
                    "usd_day_allowance": round(allowance, 4),
                    "exhausted_until": state.exhausted_until,
                }
            )
        return out

    def usage(self) -> dict:
        """Every key's spend against its budget, for a human or the brain to read.

        Same shape the HTTP API renders as a bar (fraction) with a number beside
        it (the count and its denominator) -- so a caller can say either "20%
        left" or "200 of 1000", and a tool result gets both without a second
        query.
        """
        snapshot = self.snapshot()
        keys = []
        for key in self.keys():  # noqa: SIM118 - Ledger.keys() is a method
            bucket = snapshot["buckets"].get(key, {})
            requests_left, tokens_left = self.remaining_fraction(key)
            limits = self.limits(key)
            keys.append(
                {
                    "key": key,
                    "requests_day": bucket.get("requests_day", 0),
                    "tokens_day": bucket.get("tokens_day", 0),
                    "requests_limit": limits.rpd if limits is not None else 0,
                    "tokens_limit": limits.daily_tokens if limits is not None else 0,
                    "requests_remaining": requests_left,
                    "tokens_remaining": tokens_left,
                    "consecutive_429": bucket.get("consecutive_429", 0),
                    "blocked_until": bucket.get("blocked_until"),
                    "disabled_until": bucket.get("disabled_until"),
                }
            )
        out: dict = {"day": snapshot["day"], "keys": keys}
        if self._pools:
            out["pools"] = self.pools()
        return out

    # ---------- mutations ----------

    def record(
        self,
        key: str,
        prompt_tokens: int,
        completion_tokens: int,
        usd: float = 0.0,
        priority: Priority | None = None,
    ) -> None:
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
        pool = self._pool_for(bucket)
        if pool is not None and usd:
            self._sync_pool(pool, now)
            pool.usd_day += float(usd)
            pool.usd_period += float(usd)
            if priority == "expert":
                pool.usd_day_expert += float(usd)
        self._save()

    def record_exhausted(self, key: str) -> None:
        """The provider says the prepaid credit behind this key is gone.

        Parks the whole pool -- every key on it draws from the same balance --
        or, for a key with no pool, just that key.
        """
        now = self._clock()
        self._roll_day(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            return
        pool = self._pool_for(bucket)
        if pool is not None:
            self._sync_pool(pool, now)
            pool.exhausted_until = now + EXHAUSTED_RETRY_S
        else:
            bucket.disabled_until = now + EXHAUSTED_RETRY_S
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
