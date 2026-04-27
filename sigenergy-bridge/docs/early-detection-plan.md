# Power-Signature Early Detection (Plan)

## Goal

React to Wallbox charging within ~10 s of the car drawing current, instead of waiting for the HA WebSocket `state_changed` event (which can lag 20–40 s behind the actual draw because the myWallbox HA integration polls the Wallbox cloud API).

The HA event remains the source of truth for unclamping. Power-signature detection is a *pre-emptive* clamp that gets confirmed (or rolled back) once HA catches up.

## Signal

In default EMS mode (max self-consumption), the house battery covers any new load before the grid does. So a sudden ~8 kW jump in `FromBatteryKW` (battery discharging) is a strong indicator that a high-power load just turned on. Combined with grid power as a secondary signal it's even more reliable.

Concretely, on each poll compute:
```
delta_battery_discharge_kw = current.FromBatteryKW - previous.FromBatteryKW
delta_grid_import_kw       = current.GridFromKW    - previous.GridFromKW
```

A "car likely started" trigger fires when:
- `delta_battery_discharge_kw + delta_grid_import_kw >= JUMP_THRESHOLD_KW` (default 6.0)
- Sustained over **two consecutive polls** (debounce against transient spikes)
- Current state is `IDLE`

## State machine extension

Add a new state `provisionalClamp` between `idle` and `clamped`:

```
idle ──(power jump detected)──> provisionalClamp ──(HA confirms charging)──> clamped
                                       │
                                       └──(grace period elapsed, no HA confirmation)──> idle (rollback)
```

Behaviour:
- `provisionalClamp` performs the same Modbus writes as `clamped` (EMS standby + discharge=0)
- Adds `provisional=1` field to `sigenergy_discharge_control` so the dashboard distinguishes pre-emptive vs HA-confirmed clamps
- If HA fires `charging=on` within the grace period → upgrade to `clamped`
- If grace period elapses without HA confirmation → unclamp (false positive)

## Configuration

New env vars (with defaults):

```
EARLY_DETECT_ENABLED=true
EARLY_DETECT_JUMP_THRESHOLD_KW=6.0
EARLY_DETECT_CONFIRM_GRACE=90s   # how long to wait for HA confirmation before rolling back
POLL_INTERVAL=10s                # shorten from 60s for fast detection
```

Keep the feature gated so it can be turned off if it misfires.

## Trade-offs

**Pros:**
- 10–30 s faster reaction than HA-only path
- Saves ~70–200 Wh per charging session that would otherwise drain the battery before the clamp fires

**Cons:**
- **False positives**: any sudden 6–8 kW load fires a provisional clamp. Likely culprits: induction hob (up to 7 kW), electric sauna, heat pump on cold start, oven preheat. Mitigation: 90 s grace period rolls back automatically — worst case the battery is briefly held while a known non-car load runs. Cosmetic, not damaging.
- **More Modbus traffic**: 6× more polls (60s → 10s). Sigenergy only allows one Modbus client at a time, so the mySigen app will get squeezed. Verify on hardware before rolling out.
- **More state machine surface area**: `provisionalClamp` adds a transition and a timeout — needs unit tests and careful handling of overlapping events (HA event arrives while provisional is rolling back).

## Implementation steps

1. **Add poll history**: store last 1–2 `Readings` snapshots in `Deps` so jump deltas can be computed.
2. **Detect jumps**: helper `detectChargingJump(prev, curr Readings, threshold float64) bool`.
3. **Add `provisionalClamp` state**: extend the `state` enum, transitions in the main loop, and a `provisionalSince` timestamp.
4. **Reuse `clamp()`/`unclamp()`**: the actual Modbus writes are identical — only the metric tag and rollback timer differ.
5. **Add grace-period timer** in the main `select`: when in `provisionalClamp` and `time.Since(provisionalSince) >= EARLY_DETECT_CONFIRM_GRACE` and HA hasn't confirmed → unclamp.
6. **Tests**:
   - Power jump → provisional clamp fires
   - HA confirms within grace → upgrades to confirmed (no extra Modbus writes)
   - Grace expires without HA confirmation → rolls back, metric records `false_positive`
   - Two consecutive small jumps that don't sum to threshold → no fire (debounce)
   - HA event fires *during* provisional → upgrade cleanly without race
7. **Metric**: add `provisional` (0/1) and `outcome` tag (`confirmed`/`rolled_back`) on `sigenergy_discharge_control` for observability.
8. **Grafana**: add a panel showing provisional vs confirmed clamps over time so false-positive rate is visible.

## Open questions

- Should we use `delta_battery_discharge_kw` alone, or sum it with grid delta? Battery alone is cleaner if the inverter always covers new load from battery first. Need to confirm with a few real samples.
- What's the actual Wallbox draw on this house — 8 kW? 11 kW? Threshold tuning depends on it.
- Does the mySigen app meaningfully suffer at 10 s polling, or can we go to 5 s?
- Could we read `RegPlantAvailMaxDischargeW` (30049) as an additional signal? When battery is near empty, available discharge drops — combined with a grid spike, that's another "car charging" indicator.
