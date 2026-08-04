# Spa Energy & Cost Metric — Design

Date: 2026-07-29
Branch: `cc/uto-spa-energy-estimation-1f8414`

## Problem

There is no measurement of what the Neptun Utö spa costs to run. Tibber Pulse meters
the whole house per phase, not the spa circuit, so spa cost has to be *modelled* from
the spa's own on/off state signals multiplied by per-component wattages, then priced
with the same Swedish grid+tax loading the pool panels already use.

Two things block this today:

1. **The heater signal in VictoriaMetrics is unusable.** `spa_climate_hvac_action_value`
   is a series that can only ever receive the literal value `0`.
2. **Per-component wattages were nameplate guesses**, with no figure at all for the
   circulation pump.

The heater is ~83% of the spa's energy, so without it the metric is meaningless.

## Findings

### Phase map (measured)

Established by correlating HA state transitions against Tibber per-phase power
(method and caveats: `uto-spa-calibrated-wattage` memory).

| Phase | Load(s) | Measured |
|---|---|---|
| L1 | Jet 1 + circulation pump | 1934 W / ~80 W |
| L2 | Heater | 2749 W |
| L3 | Jet 2 | 2030 W |

Circulation pump: median 76 W across 73 isolated transitions (bootstrap 95% CI
[62, 88] W); 77 W median across the 39 ultra-clean subset. L2/L3 deltas for circ
transitions were flat with CI spanning zero, confirming L1. This corrects the original
spec-sheet assumption that heater and circ shared a phase.

**This contradicts published figures.** Desk research on comparable Balboa BP2100 spas
points to ~200 W (range 150–260) for a circ pump of this class — LX WTC50M (250 W) or
LX WE14 (180 W). Direct measurement wins here: two textbook-clean traces show the step
unambiguously, with the pre/post plateaus stable to ±0.5 W.

```
2026-07-07  L1 377.6 → 283.2  = 94 W step   (HA reports circ=0 one minute later)
2026-07-18  L1 378.2 → 330.7  = 47 W step
```

Note both nights sit at an *identical* 378 W pre-transition baseline — the "circ on"
level is highly reproducible; it is the post-off level that differs. The Balboa
BP2100G0 sheet says **ozone is slaved to the circ pump** in setups 1–9, so the observed
step is circ + ozone when the ozonator happens to be co-running. The ultra-clean subset
clusters at ~75 W with secondary clusters near 50 W and 100 W, consistent with that.

For energy modelling the median (~80 W) is the right constant, since it reflects the
real mix of co-running loads. It is a *subsystem* figure, not a pump nameplate.

Duty cycles over the trailing 10 days: heater 3.27 h/day, circ 10.9 h/day (45%),
jet1 0.24 h/day, jet2 0.33 h/day. At the constants below that is ~11.8 kWh/day in
summer, of which the heater is ~83%.

### Why `hvac_action_value` is always 0

In the exporter (sibling repo `home-assistant-victoria-metrics-exporter`),
`_process_attribute()` (`custom_components/victoria_metrics/attributes.py:70-89`)
tries `float(raw_value)`, then falls back to `STATE_MAP`, then returns the raw string.
`STATE_MAP` (`const.py:30-47`) has no climate-action entries — of HA's `hvac_action`
values (`off, idle, heating, cooling, drying, fan, preheating, defrosting`) only
`off` is present, mapping to `0.0`.

`writer.py:101-104` then picks the field name by Python type, **either/or**:

```python
if isinstance(value, str):
    field_str = f'state_text="{value}"'
else:
    field_str = f"value={value}"
```

So `off` → `value=0`, while `heating`/`idle` → `state_text="heating"` and **no `_value`
sample at all**. VictoriaMetrics cannot store strings, so the `state_text` series lands
as `0` too. Panels using `last_over_time(...)` with `spanNulls` carry the last `0`
forward, which is why it reads as a constant rather than a gappy series.

Sample counts confirm the split exactly:

| Series | Samples (10d) | Share | HA ground truth |
|---|---|---|---|
| `spa_climate_hvac_action_value` | 12 321 | 85.6% | `off` = 85.7% |
| `spa_climate_hvac_action_state_text` | 2 075 | 14.4% | heating+idle = 14.3% |

**Same bug class affects other entities:** `spa_climate_value` (state is `heat`/`auto`/`off`)
and `spa_temperature_range_state_text` (`high`/`low`) are also permanently 0. The latter
is already plotted as "Temp Range" in `grafana/src/panels/spa.ts:33` and is silently flat.

### Heater history is recoverable

Because the two series are complementary, the **presence** of a `state_text` sample means
"not off". Validated against HA's own recorder: presence-derived duty over 10 days is
**14.358%** vs HA's **14.3%** — 0.06 pp apart. The series extends back 150+ days, so the
full retention window can be reconstructed.

This is a one-time reconstruction input, not a permanent query strategy — see below.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Wattage constants | jets 2000 W, heater 3000 W (nameplate-ish), circ 80 W (measured) | User's call. Round and defensible; heater 3000 vs measured 2749 overstates ~9%. |
| Heater representation | New `spa_heater_on` (0/1) metric | Avoids overloading the climate attribute series and keeps synthetic data separate. |
| Heater history | One-time backfill, `run="backfill"` label | Matches the pool-planner convention already in the repo. |
| heating vs idle | Not distinguished; non-`off` counts as on | Simpler, reproducible from state alone. Overstates heater hours ~5%, partially offsetting the 3000 W constant being ~9% high. |
| Cost computation | Query-time PromQL in Grafana | No vmalert container exists, so VM cannot evaluate recording rules; adding one would be new infra *and* would only compute forward. Query-time also gets full history for free. |

### Why not fix `hvac_action` and use it directly

Mapping `heating` to a number makes the writer emit `value=` and **stop** emitting
`state_text=`, which breaks the presence signal the 150 days of history depend on.
That would split the data into two incompatible eras. A dedicated binary sensor
sidesteps the enum path entirely — binary sensors already round-trip correctly, which
is why `spa_circulation_pump_value` works.

The exporter enum fix still ships, independently, because it repairs Temp Range and
`spa_climate_state_text`.

## Design

### 1. Exporter fix (sibling repo)

`home-assistant-victoria-metrics-exporter`. Add an explicit enum map so climate actions
map to numeric codes instead of falling through to a string:

```
off = 0, idle = 1, heating = 2, cooling = 3,
drying = 4, fan = 5, preheating = 6, defrosting = 7
```

`0` is deliberately kept as `off` so it stays consistent with the existing samples.

**Verify which commit is deployed to rpi5 first** — the local checkout was on branch
`add-vm-source-sensors` at `fa27ab1` while `origin/main` was at `653d27c`.

Because this changes what a `0` means retroactively (old `0` = "off", and old heating
periods have no `_value` at all), the spa panels deliberately do not depend on this
series. It is a correctness fix for other entities.

### 2. `spa_heater_on` — forward

HA template binary sensor:

```yaml
binary_sensor:
  - platform: template
    sensors:
      spa_heating:
        friendly_name: "Neptun Utö – Heating"
        value_template: >-
          {{ is_state_attr('climate.bp2100g0', 'hvac_action', 'heating') }}
```

Exported via the Victoria Metrics panel in HA with metric name `spa_heater_on`,
yielding `spa_heater_on_value` ∈ {0,1}.

This forward signal distinguishes heating from idle — it tests for `heating`
specifically — which turns out to be the property the whole design depends on. See §3.

### 3. `spa_heater_on` — backfill: ABANDONED (2026-08-04)

**Decision: no backfill. `spa_heater_on` starts when the template sensor goes live.**
The panels show heater cost from that date forward and nothing before it. The script was
written, validated, and reverted (`3e3670b`, reverted in `ff73321`). Nothing was ever
written to VictoriaMetrics.

The plan was to reconstruct history from the exporter quirk: `hvac_action="off"` maps to
the number 0 and lands in `..._value`, while `heating`/`idle` have no numeric mapping and
land in `..._state_text`, so the *presence* of a `state_text` sample means "not off".

**The mechanism works.** Against ground truth on 2026-08-01 it recovered **329 of 329**
HA `heating` minutes with only 2 minutes of edge slop (333 reconstructed vs 331 actual
non-off). The trick is sound.

**The assumption behind it does not.** "Not off" is `heating` **+** `idle`, and idle
draws no power. This spec originally assumed idle was ~5% of heater hours, measured over
a 10-day summer window where it genuinely was 0.7%. That window is unrepresentative.
Fraction of reconstructed-ON minutes actually drawing (L2 > 1500 W), per 14-day window
across the intended backfill range:

```
72%  56%  52%  48%  68%  82%  64%  71%  62%  68%  64%
```

April is worst at 48%. Detail for 2026-04-17 shows repeated contiguous 10–15 minute
reconstructed-ON stretches (one of 70 minutes) at a median L2 of 23–117 W — sustained
non-heating, not boundary noise.

A raw backfill would therefore overstate heater energy by roughly **1.6× in bad months**.
The heater is ~83% of modelled spa energy, so that error would dominate the metric.

**Why a power-gated backfill was also rejected:** L2 is not exclusively the spa. On
2026-08-01, 25.7% of HA-`off` minutes still exceeded 1500 W from unrelated household
loads, so gating on "L2 looks high" would import false heating periods. Distinguishing
the heater's specific ~2749 W step from other loads reliably enough to label 200k points
is a bigger problem than the history is worth.

**Consequences to keep in mind:**

- Until the template sensor exists, `spa_heater_on_value` has no data at all. The panels
  degrade gracefully — `or vector(0)` makes the heater term contribute 0 — so spa cost
  currently shows jets + circulation pump only, roughly 17% of true cost. The panel
  descriptions should say so until the sensor is live.
- The `run="backfill"` label, and the `max without(run)` aggregation it required, are no
  longer needed. `spaPowerWattsExpr` still uses `max(...)` for the heater term, which is
  harmless and remains the safer choice if a backfill is ever revisited.
- If history is ever wanted, the honest route is a proper NILM disaggregation of L2, not
  the state-presence trick.

### 4. Shared cost constants

`POOL_COST_FEES` (`0.6184` = 0.2584 nätavgift + 0.36 energiskatt) and `POOL_COST_VAT`
(`1.25`) are currently local to `poolPanels()` in `grafana/src/panels/pool.ts:223-224`.
They are not pool-specific — same house, same meter, same tariff.

Extract to `grafana/src/energyCost.ts`:

```ts
export const GRID_FEES_SEK_PER_KWH = 0.6184;
export const VAT_MULTIPLIER = 1.25;
export function loadedPriceExpr(window: string): string;
```

`pool.ts` imports them instead of defining them. Behaviour unchanged.

### 5. Spa power & cost expressions

```
spa_power_W = (spa_pump_1_value + spa_pump_2_value) * 2000
            + spa_heater_on_value * 3000
            + spa_circulation_pump_value * 80

spa_cost_SEK = spa_power_W / 1000
             * (energy_price_SEK_per_kWh{area="SE4"} + 0.6184) * 1.25
```

Integration follows the existing `poolCostExpr` shape (`pool.ts:225-228`):
`sum_over_time((...)[$__interval:5m]) * 5 / 60`. A 5 m subquery step suits both the
~60 s state cadence and the 15 min price cadence.

The four state series carry different label sets, so each term needs `sum()` to drop
labels before they can be added — the same reason `poolCostYTD` uses `sum()`.

### 6. Panels

Added to `grafana/src/panels/spa.ts`:

| Panel | Type | Content |
|---|---|---|
| Spa kostnad senaste månaden | Stat | Total spa cost, `now-30d` → `now` |
| Spa kostnad per månad | Timeseries (bars) | Monthly cost, trailing 12 months, `interval('1M')` |
| Cirkulationspump drifttid 24h | Stat | `sum_over_time(spa_circulation_pump_value[24h])` scaled to hours, unit `h` |

Caveat for the 12-month panel: VM retention and the spa data itself only reach ~150
days, so the earlier months will be empty rather than zero. `insertNulls` / `spanNulls`
must be set so the gap reads as "no data", not "no cost".

Swedish titles and descriptions matching the existing pool panels. Descriptions state
the modelled wattages and that circ is measured, not nameplate.

**Layout consequence:** the Spabadet row occupies y=68 with panels at y=69 h=8, and the
Energi row starts at y=77 — the row is full. Three more panels need a second band at
y=77, so **every row below Spabadet shifts down by 8** in `dashboard.ts` (Energi 77→85,
Volvo 108→116, Navimow 117→125, Eufy 126→134, Tapo 135→143, System 143→151,
Docker/WUD 160→168). `grafana/src/dashboard.test.ts` asserts panels fall within their
row bounds and will catch any mistake. The §7 panel lands in the Energi row, which must
also be checked for a free band before placing it.

Only the trailing-12-month panel spans the backfill; the other two are recent-window.

### 7. Stacked energy breakdown (Energi row)

Battery charging is treated as a **consumer**, not as part of the total. That resolves
what would otherwise be an ill-defined "other": energy stored is energy that went
somewhere, and at 0.80 kW average (≈19 kWh/day) it is the single largest item on the
list — larger than the spa.

Energy balance, all in kW:

```
sinks = pool + car + spa + battery_charge + other
      = pv_ac + grid_import + battery_discharge − grid_export
```

so `other = pv_ac + grid_import + battery_discharge − grid_export
            − pool − car − spa − battery_charge`.

The battery appears on both sides (discharge as a source, charge as a sink); over time
the two net out to the round-trip loss, which lands in `other`.

| Series | Source | Unit |
|---|---|---|
| Pool | `pool_iqpump_motordata_power` + `aqua_temp_power_usage` | W (**see scale bug**) |
| Car | `ha_wallbox_pulsar_max_sn_992144_charging_power_value` | **kW** |
| Spa | modelled, per §5 | W |
| Battery charge | `sigenergy_battery_power_to_battery_kw` | kW |
| Grid import | `sigenergy_grid_power_power_from_grid_kw` | kW |
| Grid export | `sigenergy_grid_power_power_to_grid_kw` | kW |
| PV | `sigenergy_pv_power_power_kw{string="total"}` | kW |
| Battery discharge | `sigenergy_battery_power_from_battery_kw` | kW |

**Units are mixed — normalise to W (or kW) explicitly per term.** This is the most
likely source of a silent 1000× error.

**Use Sigenergy, not Tibber, for the balance.** `sigenergy_grid_power_power_from_grid_kw`
averages 2.458 kW against Tibber's 2.455 kW over 7 days — a 0.1% match — and Sigenergy
additionally knows PV and battery, so one source keeps sign conventions consistent.
Tibber remains the source for *cost*, since it is the billing meter.

**PV: use `string="total"`.** The per-string series are panel-side DC and drop out at
night (`string_1` has 6 829 samples vs `total`'s 10 004, which is why its average looks
higher). In daylight `string_1` reads ~13% above `total` — the DC→AC inverter loss.
`total` is AC output and is sampled continuously, which is what the balance needs.
`string_4` is ~0 and can be ignored.

**Blocked on the `aqua_temp_power_usage` scale bug** — see below. Until that is settled
the pool term is 10× too large and would dominate the chart.

Adding kW-scale terms with different label sets (`host`, `string`, `device`) requires
`sum()`/`sum without(...)` per term before they can be combined, or the expression
silently returns empty — as it did while drafting this.

### 8. `aqua_temp_power_usage` is 10× too high (pre-existing bug)

`aquatemp.py:188` derives `power_usage = T07 × T14` (compressor current × inverter plate
AC voltage). Observed peak is **29 260** while whole-house grid import peaks at
**14 292 W** — the pool heat pump cannot draw twice the entire house. The 7-day average
of 8 578 would be 206 kWh/day.

Divided by 10: 2 926 W peak, 858 W average ≈ 20.6 kWh/day, which is right for a pool
heat pump. Either T07 is deci-amps or T14 is deci-volts; the product is 10× either way.

**Consequence:** `poolCostExpr` divides by 1000 assuming watts, so the existing
*Pool kostnad* and *Pool kostnad i år* panels overstate heat-pump cost by 10×.

Verify T07/T14 raw units against the AquaTemp API before changing anything, then decide
whether to correct at the writer (historical data stays wrong, or needs a backfill/rename)
or to compensate in the query (history reads correctly, but the metric stays misleading
for any future consumer). Tracked separately; not a prerequisite for the spa panels,
which do not use this metric.

## Testing

- `grafana/src/dashboard.test.ts` — existing row-bounds assertion covers the renumbering.
- Backfill `--dry-run` output reviewed before the real write.
- Post-backfill: re-run the duty-cycle check — `spa_heater_on_value` over the last 10
  days must land at ~14.3%, matching HA.
- Cross-check one known heating session (2026-07-28 15:31–15:53 UTC) resolves to 1.
- `npm run build` in `grafana/` (`GRAFANA_SKIP_UPLOAD=1`) to render JSON without deploying.
- Spot-check modelled cost for a day against `tibber_accumulatedCost` — the spa is a
  subset, so spa cost must be materially below house total. Order-of-magnitude only.

## Out of scope

- Fixing `aqua_temp_power_usage` (§8) — identified here, tracked separately.
- Reconstructing heater history from the L2 power signature (would remove the ~5% idle
  overestimate; rejected as unnecessary complexity).
- Recalibrating jet2 (n=6, weakest of the three).
- Any vmalert/recording-rule infrastructure.
- Standby/controller baseline draw — not separately identified, unmodelled.
