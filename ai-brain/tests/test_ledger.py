from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ai_brain.ledger import Ledger, Limits

PACIFIC = ZoneInfo("America/Los_Angeles")

L = {"gemini:a": Limits(rpm=2, tpm=1000, rpd=10)}


def make(tmp_path, t0=1_757_000_000.0):
    state = {"t": t0}
    clk = lambda: state["t"]  # noqa: E731
    return Ledger(L, tmp_path / "l.json", clock=clk), state


def test_rpm_window(tmp_path):
    l, st = make(tmp_path)
    for _ in range(2):
        assert l.can_spend("gemini:a", "brain").allowed
        l.record("gemini:a", 10, 10)
    d = l.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "rpm"
    assert d.retry_at == pytest.approx(st["t"] + 60, abs=1)
    st["t"] += 61
    assert l.can_spend("gemini:a", "brain").allowed


def test_tpm_window(tmp_path):
    l, st = make(tmp_path)
    l.record("gemini:a", 500, 400)
    d = l.can_spend("gemini:a", "brain", est_tokens=200)
    assert not d.allowed
    assert d.reason == "tpm"
    assert d.retry_at == pytest.approx(st["t"] + 60, abs=1)
    st["t"] += 61
    assert l.can_spend("gemini:a", "brain", est_tokens=200).allowed


def test_estimate_larger_than_tpm_is_not_a_reason_to_wait(tmp_path):
    # tpm=1000 but the default estimate is 4000: no amount of waiting makes
    # that fit, so it must not block. rpm is left generous to isolate tpm.
    limits = {"gemini:a": Limits(rpm=100, tpm=1000, rpd=100)}
    state = {"t": 1_757_000_000.0}
    l = Ledger(limits, tmp_path / "l.json", clock=lambda: state["t"])
    assert l.can_spend("gemini:a", "brain", est_tokens=4000).allowed
    l.record("gemini:a", 1, 1)
    # a small spend is in the window now, but the oversized estimate still
    # must not be what blocks us
    assert l.can_spend("gemini:a", "brain", est_tokens=4000).allowed
    # whereas an estimate that *could* fit is throttled normally
    l.record("gemini:a", 990, 0)
    d = l.can_spend("gemini:a", "brain", est_tokens=100)
    assert not d.allowed
    assert d.reason == "tpm"


def test_expert_starves_at_40_percent(tmp_path):
    l, st = make(tmp_path)
    for _ in range(6):  # 6 of 10 used -> 40% left, not > 40%
        st["t"] += 61
        l.record("gemini:a", 1, 1)
    d = l.can_spend("gemini:a", "expert")
    assert not d.allowed
    assert d.reason == "priority"
    assert l.can_spend("gemini:a", "brain").allowed


def test_expert_allowed_above_40_percent(tmp_path):
    l, st = make(tmp_path)
    for _ in range(5):  # 5 of 10 used -> 50% left
        st["t"] += 61
        l.record("gemini:a", 1, 1)
    assert l.can_spend("gemini:a", "expert").allowed


def test_rpd_exhausted_for_brain(tmp_path):
    l, st = make(tmp_path)
    for _ in range(10):
        st["t"] += 61
        l.record("gemini:a", 1, 1)
    d = l.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "rpd"
    # retry is the next Pacific midnight
    reset = datetime.fromtimestamp(d.retry_at, PACIFIC)
    assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)
    assert d.retry_at > st["t"]


def test_429_cooldown_and_three_strikes(tmp_path):
    l, st = make(tmp_path)
    l.record_429("gemini:a", 12.0)
    d = l.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "cooldown"
    assert d.retry_at == pytest.approx(st["t"] + 12)
    l.record_429("gemini:a", None)
    l.record_429("gemini:a", None)
    d = l.can_spend("gemini:a", "brain")
    assert d.reason == "exhausted"
    assert d.retry_at > st["t"] + 3600


def test_backoff_without_retry_after(tmp_path):
    l, st = make(tmp_path)
    l.record_429("gemini:a", None)
    assert l.can_spend("gemini:a", "brain").retry_at == pytest.approx(st["t"] + 60)
    st["t"] += 61
    l.record_429("gemini:a", None)
    assert l.can_spend("gemini:a", "brain").retry_at == pytest.approx(st["t"] + 120)


def test_record_ok_resets_consecutive_429(tmp_path):
    l, st = make(tmp_path)
    l.record_429("gemini:a", None)
    l.record_429("gemini:a", None)
    l.record_ok("gemini:a")
    st["t"] += 1
    assert l.can_spend("gemini:a", "brain").allowed
    l.record_429("gemini:a", None)
    # back to the first backoff step, not the third
    assert l.can_spend("gemini:a", "brain").retry_at == pytest.approx(st["t"] + 60)


def test_not_found_disables_24h(tmp_path):
    l, st = make(tmp_path)
    l.record_not_found("gemini:a")
    d = l.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "disabled"
    assert d.retry_at == pytest.approx(st["t"] + 86400)


def test_midnight_pacific_reset(tmp_path):
    # Anchor on the 2026-09-06 00:00 Pacific boundary so the ten records land
    # just before midnight and the final check lands just after.
    midnight = datetime(2026, 9, 6, 0, 0, tzinfo=PACIFIC).timestamp()
    t0 = midnight - 10 * 61 - 60
    l, st = make(tmp_path, t0=t0)
    for _ in range(10):
        st["t"] += 61
        l.record("gemini:a", 1, 1)
    assert datetime.fromtimestamp(st["t"], PACIFIC).date() == datetime(2026, 9, 5).date()
    assert not l.can_spend("gemini:a", "brain").allowed
    st["t"] += 120  # past 00:00 PDT
    assert datetime.fromtimestamp(st["t"], PACIFIC).date() == datetime(2026, 9, 6).date()
    assert l.can_spend("gemini:a", "brain").allowed


def test_persist_and_corrupt_recovery(tmp_path):
    l, st = make(tmp_path)
    l.record("gemini:a", 5, 5)
    l2 = Ledger(L, tmp_path / "l.json", clock=lambda: st["t"])
    assert l2.remaining_fraction("gemini:a")[0] == pytest.approx(0.9)
    (tmp_path / "l.json").write_text("{not json")
    l3 = Ledger(L, tmp_path / "l.json", clock=lambda: st["t"])
    assert l3.remaining_fraction("gemini:a")[0] == pytest.approx(0.5)
    assert l3.remaining_fraction("gemini:a")[1] == pytest.approx(0.5)


def test_stale_day_resets_to_zero_counters(tmp_path):
    l, st = make(tmp_path)
    l.record("gemini:a", 5, 5)
    st["t"] += 86400 * 2
    l2 = Ledger(L, tmp_path / "l.json", clock=lambda: st["t"])
    assert l2.remaining_fraction("gemini:a") == (1.0, 1.0)


def test_no_tmp_file_left_behind(tmp_path):
    l, _ = make(tmp_path)
    l.record("gemini:a", 5, 5)
    assert not list(tmp_path.glob("*.tmp"))


def test_unknown_key_is_disabled(tmp_path):
    l, _ = make(tmp_path)
    d = l.can_spend("gemini:nope", "brain")
    assert not d.allowed
    assert d.reason == "disabled"
    assert d.retry_at is None


def test_keys_and_snapshot(tmp_path):
    l, _ = make(tmp_path)
    assert l.keys() == ["gemini:a"]
    l.record("gemini:a", 7, 3)
    snap = l.snapshot()
    assert snap["day"] == datetime.fromtimestamp(1_757_000_000.0, PACIFIC).date().isoformat()
    b = snap["buckets"]["gemini:a"]
    assert b["requests_day"] == 1
    assert b["tokens_day"] == 10
    assert b["consecutive_429"] == 0


def test_next_available_at_picks_earliest(tmp_path):
    limits = {
        "gemini:a": Limits(rpm=2, tpm=1000, rpd=10),
        "gemini:b": Limits(rpm=2, tpm=1000, rpd=10),
    }
    state = {"t": 1_757_000_000.0}
    l = Ledger(limits, tmp_path / "l.json", clock=lambda: state["t"])
    assert l.next_available_at("brain") == state["t"]  # something is free now
    l.record_429("gemini:a", 30.0)
    l.record_429("gemini:b", 90.0)
    assert l.next_available_at("brain") == pytest.approx(state["t"] + 30)


def test_next_available_at_none_when_all_permanently_blocked(tmp_path):
    l, _ = make(tmp_path)
    l.record_not_found("gemini:a")
    nxt = l.next_available_at("brain")
    assert nxt == pytest.approx(1_757_000_000.0 + 86400)


def test_daily_token_budget_blocks_brain(tmp_path):
    # daily tokens = tpm * 60 * 24 / 10 = 1000 * 144 = 144000
    limits = {"gemini:a": Limits(rpm=100000, tpm=1000, rpd=100000)}
    state = {"t": 1_757_000_000.0}
    l = Ledger(limits, tmp_path / "l.json", clock=lambda: state["t"])
    for _ in range(144):
        state["t"] += 61
        l.record("gemini:a", 500, 500)
    assert l.remaining_fraction("gemini:a")[1] == pytest.approx(0.0)
    d = l.can_spend("gemini:a", "brain", est_tokens=1)
    assert not d.allowed
    assert d.reason == "rpd"
