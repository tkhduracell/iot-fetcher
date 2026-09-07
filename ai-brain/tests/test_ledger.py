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
    ledger, st = make(tmp_path)
    for _ in range(2):
        assert ledger.can_spend("gemini:a", "brain").allowed
        ledger.record("gemini:a", 10, 10)
    d = ledger.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "rpm"
    assert d.retry_at == pytest.approx(st["t"] + 60, abs=1)
    st["t"] += 61
    assert ledger.can_spend("gemini:a", "brain").allowed


def test_tpm_window(tmp_path):
    ledger, st = make(tmp_path)
    ledger.record("gemini:a", 500, 400)
    d = ledger.can_spend("gemini:a", "brain", est_tokens=200)
    assert not d.allowed
    assert d.reason == "tpm"
    assert d.retry_at == pytest.approx(st["t"] + 60, abs=1)
    st["t"] += 61
    assert ledger.can_spend("gemini:a", "brain", est_tokens=200).allowed


def test_estimate_larger_than_tpm_is_not_a_reason_to_wait(tmp_path):
    # tpm=1000 but the default estimate is 4000: no amount of waiting makes
    # that fit, so it must not block. rpm is left generous to isolate tpm.
    limits = {"gemini:a": Limits(rpm=100, tpm=1000, rpd=100)}
    state = {"t": 1_757_000_000.0}
    ledger = Ledger(limits, tmp_path / "l.json", clock=lambda: state["t"])
    assert ledger.can_spend("gemini:a", "brain", est_tokens=4000).allowed
    ledger.record("gemini:a", 1, 1)
    # a small spend is in the window now, but the oversized estimate still
    # must not be what blocks us
    assert ledger.can_spend("gemini:a", "brain", est_tokens=4000).allowed
    # whereas an estimate that *could* fit is throttled normally
    ledger.record("gemini:a", 990, 0)
    d = ledger.can_spend("gemini:a", "brain", est_tokens=100)
    assert not d.allowed
    assert d.reason == "tpm"


def test_expert_starves_at_40_percent(tmp_path):
    ledger, st = make(tmp_path)
    for _ in range(6):  # 6 of 10 used -> 40% left, not > 40%
        st["t"] += 61
        ledger.record("gemini:a", 1, 1)
    d = ledger.can_spend("gemini:a", "expert")
    assert not d.allowed
    assert d.reason == "priority"
    assert ledger.can_spend("gemini:a", "brain").allowed


def test_expert_allowed_above_40_percent(tmp_path):
    ledger, st = make(tmp_path)
    for _ in range(5):  # 5 of 10 used -> 50% left
        st["t"] += 61
        ledger.record("gemini:a", 1, 1)
    assert ledger.can_spend("gemini:a", "expert").allowed


def test_rpd_exhausted_for_brain(tmp_path):
    ledger, st = make(tmp_path)
    for _ in range(10):
        st["t"] += 61
        ledger.record("gemini:a", 1, 1)
    d = ledger.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "rpd"
    # retry is the next Pacific midnight
    reset = datetime.fromtimestamp(d.retry_at, PACIFIC)
    assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)
    assert d.retry_at > st["t"]


def test_429_cooldown_and_three_strikes(tmp_path):
    ledger, st = make(tmp_path)
    ledger.record_429("gemini:a", 12.0)
    d = ledger.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "cooldown"
    assert d.retry_at == pytest.approx(st["t"] + 12)
    ledger.record_429("gemini:a", None)
    ledger.record_429("gemini:a", None)
    d = ledger.can_spend("gemini:a", "brain")
    assert d.reason == "exhausted"
    assert d.retry_at > st["t"] + 3600


def test_backoff_without_retry_after(tmp_path):
    ledger, st = make(tmp_path)
    ledger.record_429("gemini:a", None)
    assert ledger.can_spend("gemini:a", "brain").retry_at == pytest.approx(st["t"] + 60)
    st["t"] += 61
    ledger.record_429("gemini:a", None)
    assert ledger.can_spend("gemini:a", "brain").retry_at == pytest.approx(st["t"] + 120)


def test_record_ok_resets_consecutive_429(tmp_path):
    ledger, st = make(tmp_path)
    ledger.record_429("gemini:a", None)
    ledger.record_429("gemini:a", None)
    ledger.record_ok("gemini:a")
    st["t"] += 1
    assert ledger.can_spend("gemini:a", "brain").allowed
    ledger.record_429("gemini:a", None)
    # back to the first backoff step, not the third
    assert ledger.can_spend("gemini:a", "brain").retry_at == pytest.approx(st["t"] + 60)


def test_not_found_disables_24h(tmp_path):
    ledger, st = make(tmp_path)
    ledger.record_not_found("gemini:a")
    d = ledger.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "disabled"
    assert d.retry_at == pytest.approx(st["t"] + 86400)


def test_midnight_pacific_reset(tmp_path):
    # Anchor on the 2026-09-06 00:00 Pacific boundary so the ten records land
    # just before midnight and the final check lands just after.
    midnight = datetime(2026, 9, 6, 0, 0, tzinfo=PACIFIC).timestamp()
    t0 = midnight - 10 * 61 - 60
    ledger, st = make(tmp_path, t0=t0)
    for _ in range(10):
        st["t"] += 61
        ledger.record("gemini:a", 1, 1)
    assert datetime.fromtimestamp(st["t"], PACIFIC).date() == datetime(2026, 9, 5).date()
    assert not ledger.can_spend("gemini:a", "brain").allowed
    st["t"] += 120  # past 00:00 PDT
    assert datetime.fromtimestamp(st["t"], PACIFIC).date() == datetime(2026, 9, 6).date()
    assert ledger.can_spend("gemini:a", "brain").allowed


def test_midnight_reset_clears_429_streak(tmp_path):
    # Three strikes park the key until midnight. Once that midnight passes the
    # streak must be gone too, otherwise the next single 429 re-exhausts the
    # key for another whole day, cascading off one rate-limit response.
    midnight = datetime(2026, 9, 6, 0, 0, tzinfo=PACIFIC).timestamp()
    ledger, st = make(tmp_path, t0=midnight - 3600)
    for _ in range(3):
        ledger.record_429("gemini:a", None)
    assert ledger.can_spend("gemini:a", "brain").reason == "exhausted"

    st["t"] = midnight + 60
    assert ledger.can_spend("gemini:a", "brain").allowed

    ledger.record_429("gemini:a", None)
    d = ledger.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "cooldown"
    assert d.retry_at == pytest.approx(st["t"] + 60)


def test_midnight_reset_leaves_not_found_disable_alone(tmp_path):
    # The 404 disable is a flat 24h, deliberately independent of the quota day.
    midnight = datetime(2026, 9, 6, 0, 0, tzinfo=PACIFIC).timestamp()
    ledger, st = make(tmp_path, t0=midnight - 3600)
    ledger.record_not_found("gemini:a")
    st["t"] = midnight + 60
    d = ledger.can_spend("gemini:a", "brain")
    assert not d.allowed
    assert d.reason == "disabled"


def test_persist_and_corrupt_recovery(tmp_path):
    ledger, st = make(tmp_path)
    ledger.record("gemini:a", 5, 5)
    l2 = Ledger(L, tmp_path / "l.json", clock=lambda: st["t"])
    assert l2.remaining_fraction("gemini:a")[0] == pytest.approx(0.9)
    (tmp_path / "l.json").write_text("{not json")
    l3 = Ledger(L, tmp_path / "l.json", clock=lambda: st["t"])
    assert l3.remaining_fraction("gemini:a")[0] == pytest.approx(0.5)
    assert l3.remaining_fraction("gemini:a")[1] == pytest.approx(0.5)


def test_stale_day_resets_to_zero_counters(tmp_path):
    ledger, st = make(tmp_path)
    ledger.record("gemini:a", 5, 5)
    st["t"] += 86400 * 2
    l2 = Ledger(L, tmp_path / "l.json", clock=lambda: st["t"])
    assert l2.remaining_fraction("gemini:a") == (1.0, 1.0)


def test_no_tmp_file_left_behind(tmp_path):
    ledger, _ = make(tmp_path)
    ledger.record("gemini:a", 5, 5)
    assert not list(tmp_path.glob("*.tmp"))


def test_unknown_key_is_disabled(tmp_path):
    ledger, _ = make(tmp_path)
    d = ledger.can_spend("gemini:nope", "brain")
    assert not d.allowed
    assert d.reason == "disabled"
    assert d.retry_at is None


def test_keys_and_snapshot(tmp_path):
    ledger, _ = make(tmp_path)
    assert ledger.keys() == ["gemini:a"]
    ledger.record("gemini:a", 7, 3)
    snap = ledger.snapshot()
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
    ledger = Ledger(limits, tmp_path / "l.json", clock=lambda: state["t"])
    assert ledger.next_available_at("brain") == state["t"]  # something is free now
    ledger.record_429("gemini:a", 30.0)
    ledger.record_429("gemini:b", 90.0)
    assert ledger.next_available_at("brain") == pytest.approx(state["t"] + 30)


def test_next_available_at_none_when_all_permanently_blocked(tmp_path):
    ledger, _ = make(tmp_path)
    ledger.record_not_found("gemini:a")
    nxt = ledger.next_available_at("brain")
    assert nxt == pytest.approx(1_757_000_000.0 + 86400)


def test_daily_token_budget_blocks_brain(tmp_path):
    # daily tokens = tpm * 60 * 24 / 10 = 1000 * 144 = 144000
    limits = {"gemini:a": Limits(rpm=100000, tpm=1000, rpd=100000)}
    state = {"t": 1_757_000_000.0}
    ledger = Ledger(limits, tmp_path / "l.json", clock=lambda: state["t"])
    for _ in range(144):
        state["t"] += 61
        ledger.record("gemini:a", 500, 500)
    assert ledger.remaining_fraction("gemini:a")[1] == pytest.approx(0.0)
    d = ledger.can_spend("gemini:a", "brain", est_tokens=1)
    assert not d.allowed
    assert d.reason == "rpd"


def test_rolling_the_day_is_persisted_immediately(tmp_path):
    """A restart right after the roll must not reload yesterday's spent budget."""
    ledger, st = make(tmp_path)
    for _ in range(10):
        ledger.record("gemini:a", 10, 10)
    assert not ledger.can_spend("gemini:a", "brain").allowed

    st["t"] += 24 * 3600
    assert ledger.can_spend("gemini:a", "brain").allowed  # rolls the day

    reloaded = Ledger(L, tmp_path / "l.json", clock=lambda: st["t"])
    assert reloaded.snapshot()["buckets"]["gemini:a"]["requests_day"] == 0
    assert reloaded.can_spend("gemini:a", "brain").allowed


def test_a_day_that_did_not_change_is_not_rewritten(tmp_path):
    ledger, _st = make(tmp_path)
    ledger.record("gemini:a", 10, 10)
    path = tmp_path / "l.json"
    before = path.stat().st_mtime_ns

    ledger.can_spend("gemini:a", "brain")  # same day: no roll, no save

    assert path.stat().st_mtime_ns == before
