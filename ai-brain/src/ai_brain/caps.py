"""How many rounds a cycle has left, as a pure function of what just happened.

The rules have enough interlocking cases (a fallback's lower cap, the once-only
rescue, the sticky token-budget stop) that they earned a place of their own. With
no clock, no provider and no I/O in here, each case can be tested on its own, and
the session log can record the result and a resumed cycle pick it back up.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_brain.config import CYCLE_MAX_ROUNDS_HARD_CEILING

# Sent once per cycle, WRAP_UP_ROUNDS_BEFORE_CAP rounds before the effective
# cap: a cycle that is about to be cut off gets one chance to land what it
# found rather than losing an unfinished thought to max_rounds. Deliberately
# small -- a nudge much earlier would fire on ordinary cycles nowhere near
# their cap, since the cap can be as low as CYCLE_MAX_ROUNDS itself (16).
WRAP_UP_ROUNDS_BEFORE_CAP = 2


@dataclass(frozen=True)
class CapState:
    # The limit in force: that of whichever model answered the most recent
    # round, so a cycle that falls back to a weak model is cut at the weak
    # limit, not the strong one it started on.
    effective_cap: int
    # Set the round a cap-drop rescue or the token-budget stop first fires, to
    # rounds + WRAP_UP_ROUNDS_BEFORE_CAP at that moment -- frozen from then on,
    # so neither a per-model cap bump nor a further cap drop on a later round
    # can move it. Without freezing it, a second per-model drop the very next
    # round (still within the window, still before the *next* nudge check
    # runs) would recompute against the new, later ``rounds`` and quietly
    # shrink the promised window to less than WRAP_UP_ROUNDS_BEFORE_CAP -- the
    # model would have been told "N rounds left" and then get fewer. None means
    # neither has fired yet.
    floor_cap: int | None = None
    # True once the cycle's prompt tokens first cross the budget. Sticky forever
    # after: a per-model cap bump on a later round must not undo a stop the
    # token budget already called for.
    budget_stopped: bool = False
    # True when this round's cap was set by the quota left on the only provider
    # that can answer, not by a rounds setting. Re-derived every round (a fallback
    # coming back into reach lifts it), unlike budget_stopped.
    quota_stopped: bool = False


def after_reply(
    state: CapState,
    *,
    rounds: int,
    new_cap: int,
    over_budget: bool,
    nudge_sent: bool,
    runway: int | None = None,
) -> CapState:
    """The cap state once a round has been answered.

    ``new_cap`` is the limit of the model that just answered and ``rounds``
    counts that answer. This can only ever make the cap *smaller* than a round
    already played past, never retroactively grant more rounds for rounds
    already spent. ``new_cap`` dropping the cycle to (or past) its wrap-up window
    with no nudge sent yet is rescued, once, rather than left to end the cycle
    with the model never having seen a nudge at all. ``over_budget`` is the same
    rescue -- enough extra room for one nudge, never more -- except that its stop
    is sticky.

    ``runway`` is how many more requests the quota allows, if anything limits
    them. It is a limit no rescue can buy more of: a key with 11 requests a day has
    no 12th round to grant however the rest of this feels about it. So it clamps
    last, over the floor too, and the nudge lands while the cycle can still act on
    it instead of the next round meeting ChainExhausted with nothing written down.
    """
    budget_stopped = state.budget_stopped or over_budget
    floor_cap = state.floor_cap
    if floor_cap is None and (
        budget_stopped or (not nudge_sent and new_cap <= rounds + WRAP_UP_ROUNDS_BEFORE_CAP)
    ):
        floor_cap = min(rounds + WRAP_UP_ROUNDS_BEFORE_CAP, CYCLE_MAX_ROUNDS_HARD_CEILING)
    if budget_stopped:
        # The budget stop is exact, not a floor: once tripped, no per-model cap
        # -- however generous -- may push the cycle past floor_cap (set on the
        # same round budget_stopped first became True, and frozen from then on
        # -- so it is never None here), unlike the cap-drop rescue below which
        # only ever raises the cap up to its floor.
        effective_cap = floor_cap if floor_cap is not None else new_cap
    elif floor_cap is not None:
        effective_cap = max(new_cap, floor_cap)
    else:
        effective_cap = new_cap
    quota_stopped = runway is not None and rounds + runway < effective_cap
    if quota_stopped:
        effective_cap = rounds + runway
    return CapState(effective_cap, floor_cap, budget_stopped, quota_stopped)


def nudge_due(state: CapState, *, rounds: int, nudge_sent: bool) -> bool:
    """Whether to send the wrap-up nudge now.

    Once per cycle: fires the round it first comes within
    WRAP_UP_ROUNDS_BEFORE_CAP of the *current* cap, so a cap that later drops (a
    fallback to a weaker model, or the rescue above) can still trigger it even if
    the first check came too early to.
    """
    return not nudge_sent and rounds >= state.effective_cap - WRAP_UP_ROUNDS_BEFORE_CAP
