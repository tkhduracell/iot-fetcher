from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from ai_brain.ledger import (
    EXHAUSTED_RETRY_S,
    Ledger,
    Limits,
    Pool,
    next_period_start,
    period_start,
)

PACIFIC = ZoneInfo("America/Los_Angeles")
OPUS = "anthropic:claude-opus-5-5"
HAIKU = "anthropic:claude-haiku-5-5"
LIMITS = {
    key: Limits(rpm=50, tpm=10**8, rpd=10**6, usd_pool="anthropic") for key in (OPUS, HAIKU)
}


def at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=PACIFIC).timestamp()


def make(tmp_path, t0, monthly=30.0, billing_day=10):
    state = {"t": t0}
    ledger = Ledger(
        LIMITS,
        tmp_path / "l.json",
        clock=lambda: state["t"],
        pools={"anthropic": Pool(monthly, billing_day)},
    )
    return ledger, state


def test_period_bounds_follow_the_billing_day():
    assert period_start(date(2026, 10, 17), 10) == date(2026, 10, 10)
    assert period_start(date(2026, 10, 9), 10) == date(2026, 9, 10)
    assert next_period_start(date(2026, 10, 10), 10) == date(2026, 11, 10)
    # A billing day the month does not have clamps to its last day.
    assert period_start(date(2026, 2, 28), 31) == date(2026, 2, 28)
    assert period_start(date(2026, 2, 27), 31) == date(2026, 1, 31)
    assert next_period_start(date(2026, 12, 31), 31) == date(2027, 1, 31)


def test_daily_allowance_is_what_is_left_over_days_left(tmp_path):
    # 2026-10-17 -> next period 2026-11-10: 24 days left.
    ledger, _ = make(tmp_path, at(2026, 10, 17), monthly=24.0)
    [pool] = ledger.pools()
    assert pool["usd_day_allowance"] == pytest.approx(1.0)
    assert pool["period_start"] == "2026-10-10"


def test_day_cap_blocks_until_midnight_then_reflows(tmp_path):
    ledger, st = make(tmp_path, at(2026, 10, 17), monthly=24.0)
    ledger.record(OPUS, 1000, 100, usd=1.5)
    d = ledger.can_spend(OPUS, "brain")
    assert not d.allowed and d.reason == "usd_day"
    assert d.retry_at == pytest.approx(at(2026, 10, 18, 0))
    # Next day: (24 - 1.5) over 23 days left.
    st["t"] = at(2026, 10, 18)
    assert ledger.can_spend(OPUS, "brain").allowed
    assert ledger.pools()[0]["usd_day_allowance"] == pytest.approx(22.5 / 23, abs=1e-4)


def test_quiet_days_raise_the_allowance(tmp_path):
    ledger, st = make(tmp_path, at(2026, 10, 10), monthly=31.0)
    first = ledger.pools()[0]["usd_day_allowance"]
    st["t"] = at(2026, 11, 1)
    later = ledger.pools()[0]["usd_day_allowance"]
    assert later > first


def test_experts_are_capped_by_their_own_spend(tmp_path):
    ledger, _ = make(tmp_path, at(2026, 10, 17), monthly=24.0)  # $1/day
    ledger.record(HAIKU, 1000, 100, usd=0.6, priority="expert")
    d = ledger.can_spend(HAIKU, "expert")
    assert not d.allowed and d.reason == "priority"
    # The brain keeps the rest of the day.
    assert ledger.can_spend(OPUS, "brain").allowed


def test_a_busy_brain_does_not_shut_experts_out(tmp_path):
    ledger, _ = make(tmp_path, at(2026, 10, 17), monthly=24.0)  # $1/day
    ledger.record(OPUS, 1000, 100, usd=0.8, priority="brain")
    assert ledger.can_spend(HAIKU, "expert").allowed
    # Until the day's allowance itself is gone.
    ledger.record(OPUS, 1000, 100, usd=0.2, priority="brain")
    assert ledger.can_spend(HAIKU, "expert").reason == "usd_day"


def test_expert_share_resets_with_the_day(tmp_path):
    ledger, st = make(tmp_path, at(2026, 10, 17), monthly=24.0)
    ledger.record(HAIKU, 10, 10, usd=0.6, priority="expert")
    st["t"] = at(2026, 10, 18)
    assert ledger.can_spend(HAIKU, "expert").allowed
    assert ledger.pools()[0]["usd_day_expert"] == 0.0


def test_keys_share_one_pool(tmp_path):
    ledger, _ = make(tmp_path, at(2026, 10, 17), monthly=24.0)
    ledger.record(HAIKU, 10, 10, usd=0.4)
    ledger.record(OPUS, 10, 10, usd=0.6)
    assert not ledger.can_spend(OPUS, "brain").allowed
    assert ledger.pools()[0]["usd_period"] == pytest.approx(1.0)


def test_month_cap_blocks_until_next_period(tmp_path):
    ledger, st = make(tmp_path, at(2026, 11, 9), monthly=10.0)
    ledger.record(OPUS, 10, 10, usd=10.0)
    d = ledger.can_spend(OPUS, "brain")
    assert not d.allowed and d.reason == "usd_month"
    assert d.retry_at == pytest.approx(at(2026, 11, 10, 0))
    st["t"] = at(2026, 11, 10)
    assert ledger.can_spend(OPUS, "brain").allowed
    assert ledger.pools()[0]["usd_period"] == 0.0


def test_exhausted_parks_every_key_on_the_pool_for_a_while(tmp_path):
    ledger, st = make(tmp_path, at(2026, 10, 17))
    ledger.record_exhausted(OPUS)
    d = ledger.can_spend(HAIKU, "expert")
    assert not d.allowed and d.reason == "exhausted"
    st["t"] += EXHAUSTED_RETRY_S + 1
    assert ledger.can_spend(HAIKU, "expert").allowed


def test_month_spend_survives_restart_across_a_day_roll(tmp_path):
    ledger, st = make(tmp_path, at(2026, 10, 17))
    ledger.record(OPUS, 10, 10, usd=3.25)
    st["t"] = at(2026, 10, 19)
    reloaded, _ = make(tmp_path, st["t"])
    [pool] = reloaded.pools()
    assert pool["usd_period"] == pytest.approx(3.25)
    assert pool["usd_day"] == 0.0


def test_corrupt_ledger_assumes_half_the_month_is_gone(tmp_path):
    (tmp_path / "l.json").write_text("{not json")
    ledger, _ = make(tmp_path, at(2026, 10, 17), monthly=30.0)
    assert ledger.pools()[0]["usd_period"] == pytest.approx(15.0)


def test_usage_reports_pools_only_when_configured(tmp_path):
    ledger, _ = make(tmp_path, at(2026, 10, 17))
    assert ledger.usage()["pools"][0]["pool"] == "anthropic"
    plain = Ledger({"gemini:a": Limits(rpm=1, tpm=1, rpd=1)}, tmp_path / "p.json")
    assert "pools" not in plain.usage()


# --- rounds_left prices rounds in dollars too -------------------------------


def test_rounds_left_counts_the_days_dollars_down(tmp_path):
    ledger, _ = make(tmp_path, at(2026, 10, 17), monthly=24.0)  # 1.00 a day
    # Unpriced, a pool says nothing a request count could: the (huge) rpd.
    assert ledger.rounds_left(OPUS, "brain") == 10**6
    # 0.30 a round: 0, 0.3, 0.6 and 0.9 are all under 1.00, the 5th is not.
    assert ledger.rounds_left(OPUS, "brain", round_usd=0.3) == 4
    ledger.record(OPUS, 10, 10, usd=0.5, priority="brain")
    assert ledger.rounds_left(OPUS, "brain", round_usd=0.3) == 2


def test_rounds_left_gives_an_expert_only_its_share_of_the_day(tmp_path):
    ledger, _ = make(tmp_path, at(2026, 10, 17), monthly=24.0)
    # Experts may spend 60% of the 1.00 allowance: 0, 0.25, 0.5 are under 0.6.
    assert ledger.rounds_left(OPUS, "expert", round_usd=0.25) == 3
    assert ledger.rounds_left(OPUS, "brain", round_usd=0.25) == 4
    # The brain's own spend counts against the day but not the experts' share.
    ledger.record(OPUS, 10, 10, usd=0.3, priority="brain")
    assert ledger.rounds_left(OPUS, "expert", round_usd=0.25) == 3


def test_rounds_left_is_zero_for_a_dry_pool(tmp_path):
    ledger, _ = make(tmp_path, at(2026, 10, 17), monthly=24.0)
    ledger.record(OPUS, 10, 10, usd=1.5, priority="brain")
    assert ledger.can_spend(OPUS, "brain").reason == "usd_day"
    assert ledger.rounds_left(OPUS, "brain", round_usd=0.1) == 0


@pytest.mark.parametrize("priority", ["brain", "expert"])
@pytest.mark.parametrize("already", [0.0, 0.4, 0.99])
@pytest.mark.parametrize(
    "round_usd", [0.01, 0.1, 0.25, 0.3, 1 / 3, 0.5, 0.6, 0.999, 1.0, 1.0001, 2.5]
)
def test_rounds_left_is_exactly_how_many_rounds_can_spend_admits_in_dollars(
    tmp_path, priority, already, round_usd
):
    """Same boundary check as the token one, against the pool's three limits."""
    t0 = at(2026, 10, 17)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    predicted, _ = make(tmp_path / "a", t0, monthly=24.0)
    actual, state = make(tmp_path / "b", t0, monthly=24.0)
    for ledger in (predicted, actual):
        if already:
            ledger.record(OPUS, 10, 10, usd=already, priority=priority)

    n = predicted.rounds_left(OPUS, priority, round_tokens=20, round_usd=round_usd)

    admitted = 0
    while actual.can_spend(OPUS, priority).allowed:
        actual.record(OPUS, 10, 10, usd=round_usd, priority=priority)
        # Past the 50 rpm window; the day is nowhere near over at noon.
        state["t"] += 61
        admitted += 1
    assert n == admitted
