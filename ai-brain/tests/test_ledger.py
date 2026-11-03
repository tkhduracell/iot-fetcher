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


def test_expert_only_key_has_no_floor(tmp_path):
    # Nothing to hold back for a brain that may not spend here: experts get
    # the whole day, up to rpd.
    limits = {"gemini:lite": Limits(rpm=100, tpm=10**6, rpd=10, loops=frozenset({"expert"}))}
    ledger = Ledger(limits, tmp_path / "l.json", clock=lambda: 1_757_000_000.0)
    for _ in range(9):
        ledger.record("gemini:lite", 1, 1)
    assert ledger.can_spend("gemini:lite", "expert").allowed
    ledger.record("gemini:lite", 1, 1)
    assert ledger.can_spend("gemini:lite", "expert").reason == "rpd"


def test_shared_key_keeps_the_floor(tmp_path):
    limits = {"gemini:a": Limits(rpm=100, tpm=10**6, rpd=10, loops=frozenset({"brain", "expert"}))}
    ledger = Ledger(limits, tmp_path / "l.json", clock=lambda: 1_757_000_000.0)
    for _ in range(6):
        ledger.record("gemini:a", 1, 1)
    assert ledger.can_spend("gemini:a", "expert").reason == "priority"


def test_expert_allowed_above_40_percent(tmp_path):
    ledger, st = make(tmp_path)
    for _ in range(5):  # 5 of 10 used -> 50% left
        st["t"] += 61
        ledger.record("gemini:a", 1, 1)
    assert ledger.can_spend("gemini:a", "expert").allowed


def test_rounds_left_counts_the_daily_budget_down(tmp_path):
    ledger, st = make(tmp_path)
    assert ledger.rounds_left("gemini:a", "brain") == 10
    for _ in range(3):
        st["t"] += 61
        ledger.record("gemini:a", 1, 1)
    assert ledger.rounds_left("gemini:a", "brain") == 7


def test_rounds_left_stops_an_expert_at_the_floor(tmp_path):
    ledger, st = make(tmp_path)
    assert ledger.rounds_left("gemini:a", "expert") == 6
    for _ in range(5):
        st["t"] += 61
        ledger.record("gemini:a", 1, 1)
    assert ledger.rounds_left("gemini:a", "expert") == 1
    assert ledger.rounds_left("gemini:a", "brain") == 5
    st["t"] += 61
    ledger.record("gemini:a", 1, 1)
    assert ledger.rounds_left("gemini:a", "expert") == 0


@pytest.mark.parametrize("rpd", [1, 3, 5, 7, 10, 11, 12, 100, 200])
def test_rounds_left_agrees_with_can_spend_at_the_floor(tmp_path, rpd):
    """The count must be exactly how many more can_spend would allow."""
    limits = {"k": Limits(rpm=10**6, tpm=10**9, rpd=rpd)}
    ledger = Ledger(limits, tmp_path / "l.json", clock=lambda: 1_757_000_000.0)
    spent = 0
    while ledger.can_spend("k", "expert").allowed:
        ledger.record("k", 1, 1)
        spent += 1
    fresh = Ledger(limits, tmp_path / "other.json", clock=lambda: 1_757_000_000.0)
    assert fresh.rounds_left("k", "expert") == spent


def test_rounds_left_is_zero_for_anything_that_will_not_clear_soon(tmp_path):
    limits = {
        "res": Limits(rpm=100, tpm=10**6, rpd=10, loops=frozenset({"expert"})),
        "dead": Limits(rpm=100, tpm=10**6, rpd=10),
    }
    ledger = Ledger(limits, tmp_path / "l.json", clock=lambda: 1_757_000_000.0)
    assert ledger.rounds_left("res", "brain") == 0
    assert ledger.rounds_left("unknown", "brain") == 0
    ledger.record_not_found("dead")
    assert ledger.rounds_left("dead", "brain") == 0
    ledger.record_429("dead", 120)
    assert ledger.rounds_left("dead", "brain") == 0


def test_rounds_left_ignores_the_per_minute_windows(tmp_path):
    ledger, _ = make(tmp_path)
    for _ in range(2):  # rpm=2 is now full
        ledger.record("gemini:a", 1, 1)
    assert ledger.can_spend("gemini:a", "brain").reason == "rpm"
    assert ledger.rounds_left("gemini:a", "brain") == 8


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


def test_usage_reports_spend_against_budget(tmp_path):
    ledger, _st = make(tmp_path)
    ledger.record("gemini:a", 100, 50)

    usage = ledger.usage()

    assert usage["day"] == ledger.day
    assert usage["keys"] == [
        {
            "key": "gemini:a",
            "requests_day": 1,
            "tokens_day": 150,
            "requests_limit": 10,
            "tokens_limit": Limits(rpm=2, tpm=1000, rpd=10).daily_tokens,
            "requests_remaining": pytest.approx(0.9),
            "tokens_remaining": pytest.approx(1 - 150 / Limits(rpm=2, tpm=1000, rpd=10).daily_tokens),
            "consecutive_429": 0,
            "blocked_until": None,
            "disabled_until": None,
        }
    ]


def test_usage_reflects_a_blocked_key(tmp_path):
    ledger, _st = make(tmp_path)
    ledger.record_429("gemini:a", retry_after_s=30)

    usage = ledger.usage()

    assert usage["keys"][0]["consecutive_429"] == 1
    assert usage["keys"][0]["blocked_until"] is not None


# --- rounds_left beyond the request count -----------------------------------
#
# tpm=100 gives a synthetic daily token budget of 14400 and, being under the
# 4000-token estimate, never throttles by itself -- so what these keys can run
# out of is exactly the day's requests, the day's tokens and the expert floor.

DAY_TOKENS = 14_400


def token_ledger(path, rpd=10**6, loops=None, t0=1_757_000_000.0):
    limits = {"k": Limits(rpm=10**6, tpm=100, rpd=rpd, loops=loops)}
    assert limits["k"].daily_tokens == DAY_TOKENS
    return Ledger(limits, path / "t.json", clock=lambda: t0)


def test_rounds_left_counts_the_daily_tokens_down(tmp_path):
    ledger = token_ledger(tmp_path)
    # Unpriced, only the request budget speaks and it is huge.
    assert ledger.rounds_left("k", "brain") == 10**6
    # 1000 a round: 15 rounds start below 14400 (0..14000), the 16th would not.
    assert ledger.rounds_left("k", "brain", round_tokens=1000) == 15
    ledger.record("k", 4000, 1000)
    assert ledger.rounds_left("k", "brain", round_tokens=1000) == 10


def test_rounds_left_takes_the_tighter_of_requests_and_tokens(tmp_path):
    ledger = token_ledger(tmp_path, rpd=5)
    assert ledger.rounds_left("k", "brain", round_tokens=1000) == 5
    assert ledger.rounds_left("k", "brain", round_tokens=5000) == 3


def test_rounds_left_stops_an_expert_at_the_token_floor(tmp_path):
    ledger = token_ledger(tmp_path)
    # An expert on a shared key is refused once 60% of the day's tokens (8640)
    # are spent: at 1000 a round that is 9 rounds (0..8000).
    assert ledger.rounds_left("k", "expert", round_tokens=1000) == 9
    assert ledger.rounds_left("k", "brain", round_tokens=1000) == 15
    # On a key only experts may use there is no brain share to hold back.
    (tmp_path / "only").mkdir()
    only = token_ledger(tmp_path / "only", loops=frozenset({"expert"}))
    assert only.rounds_left("k", "expert", round_tokens=1000) == 15


def test_rounds_left_is_zero_when_the_tokens_are_already_gone(tmp_path):
    ledger = token_ledger(tmp_path)
    ledger.record("k", DAY_TOKENS, 0)
    assert ledger.can_spend("k", "brain").reason == "rpd"
    assert ledger.rounds_left("k", "brain", round_tokens=1000) == 0


def test_rounds_left_ignores_negative_prices(tmp_path):
    ledger = token_ledger(tmp_path, rpd=7)
    assert ledger.rounds_left("k", "brain", round_tokens=-5, round_usd=-1.0) == 7


@pytest.mark.parametrize("rpd", [3, 10, 11, 12, 30])
@pytest.mark.parametrize(
    ("priority", "loops"),
    [("brain", None), ("expert", None), ("expert", frozenset({"expert"}))],
)
@pytest.mark.parametrize("already", [0, 1, 4321])
@pytest.mark.parametrize(
    "round_tokens", [0, 1, 100, 999, 1000, 1001, 1500, 7000, DAY_TOKENS - 1, DAY_TOKENS, 50_000]
)
def test_rounds_left_is_exactly_how_many_rounds_can_spend_admits(
    tmp_path, rpd, priority, loops, already, round_tokens
):
    """The count and the verdict share their rules; this holds them to it at
    every boundary: the last counted round is admitted and the next is refused."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    predicted = token_ledger(tmp_path / "a", rpd=rpd, loops=loops)
    actual = token_ledger(tmp_path / "b", rpd=rpd, loops=loops)
    for ledger in (predicted, actual):
        if already:
            ledger.record("k", already, 0)

    n = predicted.rounds_left("k", priority, round_tokens=round_tokens)

    admitted = 0
    while actual.can_spend("k", priority).allowed:
        actual.record("k", round_tokens, 0)
        admitted += 1
        assert admitted <= rpd  # the request cap alone ends it; more is a test bug
    assert n == admitted
