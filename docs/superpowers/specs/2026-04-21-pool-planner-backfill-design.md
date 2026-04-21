# Pool-pump planner — backfill subcommand

## Goal

Let me re-run the planner for each of the last ~30 days as if it had fired at that day's `POOL_PLAN_TIME`, so I can see how the optimizer behaves across different real-world price, solar, and temperature profiles. Primary question it should answer: *"if we had been running this planner the last 30 days, what schedule would it have produced and what would it have cost?"*

Scope is a one-shot local analysis tool, not a production feature.

## CLI

```
pool-pump-planner                       # unchanged: one run + daily schedule at POOL_PLAN_TIME
pool-pump-planner once                  # unchanged
pool-pump-planner backfill [flags]      # NEW
  --days=30                             # how many calendar days to plan (default 30)
  --end=YYYY-MM-DD                      # last day (default: yesterday, site-local)
  --dry-run                             # compute + print table, skip VM writes
```

Defaults produce "last 30 days ending yesterday, writing tagged points and printing a summary table". The existing `--once` flag keeps working for back-compat; `main.go` grows a small `os.Args[1]` verb dispatcher.

## VM write strategy

**New tag on every plan point, both live and backfill:**

| Tag            | Value on live runs | Value on backfill runs  |
|----------------|--------------------|-------------------------|
| `run`          | `live`             | `backfill`              |
| `anchor_date`  | (unset)            | `YYYY-MM-DD` (site-local)|
| `mode`         | `optimal`/`fallback` (unchanged) | `optimal`/`fallback` (unchanged) |
| `horizon`      | `24h` (unchanged)  | `24h` (unchanged)       |

Tagging live runs with `run=live` is a small behavior change to existing writes, approved in design discussion. Re-running backfill is idempotent: `(measurement, tags, timestamp)` is identical, and VM/Influx line protocol overwrites.

## Internal refactor

### 1. `plan()` takes explicit `now` and a tag map

```go
func plan(cfg *Config, now time.Time, extraTags map[string]string) error
```

- Scheduled / `once` path: `plan(cfg, time.Now().UTC(), map[string]string{"run": "live"})`
- Backfill: `plan(cfg, anchor, map[string]string{"run": "backfill", "anchor_date": date.Format("2006-01-02")})`

`writePlan` merges `extraTags` into the `pool_iqpump_plan` and `pool_iqpump_plan_summary` points.

### 2. Point-in-time-aware fetchers

- `fetchHourlyPrices(slots)` — already parameterized on slots, no change.
- `fetchWaterTempAt(t time.Time) (float64, bool)` — new VM instant query with `&time=<unix>`; existing `fetchWaterTemp()` becomes a one-line wrapper.
- `fetchSolarKWh(slots) []float64` — dispatcher:
  - `cfg.Backfill == false` → existing `fetchSolarForecast` (forecast.solar).
  - `cfg.Backfill == true` → `fetchSolarHistoricalKWh(slots)`: VM range query `avg_over_time(sigenergy_pv_power_power_kw{string="total"}[<slot>])`, bucket samples by slot timestamp, convert `kW × slotHours` → kWh per slot.

`cfg.Backfill` is set only by the backfill subcommand.

### 3. New file `backfill.go`

Contains:
- `runBackfill(cfg *Config, days int, end time.Time, dryRun bool) error`
- `backfillResult` struct with per-day fields (date, mode, hours, target, cost, slack, missing, onHours).
- Day loop: for `i := 0..days-1`, compute `anchor = end.AddDate(0,0,-i) @ POOL_PLAN_TIME`, call `plan(cfg, anchor.UTC().Truncate(time.Hour), {"run":"backfill","anchor_date":...})`, collect the result.
- Table printer.

Keeps `planner.go` focused on "build, solve, write one plan".

### 4. File layout diff

```
pool-pump-planner/
  main.go         # verb dispatcher added
  backfill.go     # NEW
  planner.go      # plan() signature change, writePlan accepts tags
  vm.go           # fetchWaterTempAt + historical PV query helper
  solar.go        # fetchSolarKWh dispatcher
  backfill_test.go # NEW
```

## Grafana dashboard update (in scope for same PR)

File: `grafana/src/panels/pool.ts` — `pumpPlan` panel.

Problem: current queries (e.g. `last_over_time(pool_iqpump_plan_on[$__interval])`) don't filter by `run`, so once backfill points start landing in VM they blend with live points in the "Poolpump plan" panel.

Fix: add `{run="live"}` selector to each PromQL query in the `pumpPlan` panel so the existing visualization stays clean:

```ts
.withTarget(vmExpr('A', 'last_over_time(pool_iqpump_plan_on{run="live"}[$__interval])', 'on'))
.withTarget(vmExpr('B', 'last_over_time(pool_iqpump_plan_price_sek_per_kwh{run="live"}[$__interval])', 'price_sek_per_kwh'))
.withTarget(vmExpr('C', 'last_over_time(pool_iqpump_plan_solar_kwh{run="live"}[$__interval])', 'solar_kwh'))
```

Dashboard JSON regenerated via `convert_dashboard.py` per repo convention (CLAUDE.md).

Backfill data is intentionally **not** given a panel in this PR — it's for ad-hoc analysis via Explore or direct PromQL queries. A dedicated backfill panel can be added later if useful.

## Data flow (backfill path)

```
for each day in [end, end-1, ..., end-(days-1)]:
    anchor_local = day @ POOL_PLAN_TIME (site-local)
    now          = anchor_local.UTC().Truncate(time.Hour)
    slots        = [now, now+slot, ..., now+24h)

    prices       = VM range query over slots window
    waterTemp    = VM instant query at anchor_local.UTC()
    solar        = avg_over_time(sigenergy_pv_power_power_kw{string="total"}[slot]) per slot

    → plan() runs MILP or fallback same as live
    → writePlan() emits tagged points to VM (unless --dry-run)
    → collect result for table
```

## Stdout table

Printed after the loop finishes, newest first:

```
Pool pump backfill — 30 days ending 2026-04-20 (anchor 14:05 Europe/Stockholm)

  DATE        MODE        HRS   TGT  COST(SEK)  SLACK  MISSING   ON_HOURS (local)
  2026-04-20  optimal     6.0     6     11.42   0.0   -           00 01 02 03 13 14
  2026-04-19  optimal     6.0     6      9.81   0.0   -           01 02 03 04 13 14
  2026-04-18  fallback    8.0     8     18.40   0.0   prices      01 02 03 04 12 13 14 15
  ...
  2026-03-22  optimal     7.0     7     13.04   0.0   -           00 01 02 12 13 14 21
  ──────────────────────────────────────────────────────────────────────────────────
  Totals                 192.0   195    382.10   avg/day 6.40h, 12.74 SEK
  Failures: 0             Days skipped for missing prices: 0
```

- `HRS/TGT` highlights when the solver hit slack.
- `MISSING` surfaces whatever `writePlan`'s `missing_inputs` already tracks (`-` when `none`).
- `ON_HOURS` is compact even for 15m slots: space-joined list of local clock hours in which any slot was `on`.
- Failed days print with `ERR` in MODE and the error on the next indented line; loop continues.

## Error handling

- **Per-day failures are non-fatal.** Fetch errors, solver errors, or write errors: log, mark the row, move on. Matches the live path, which already logs-and-continues.
- **Fatal:** bad flags (`--days=-1`, unparseable `--end`), config validation, VM auth failure on first call. Exit 1.
- **Exit code on completion:** 0 if ≥1 day succeeded, 1 if *all* days failed.

## Testing

- `backfill_test.go` (new)
  - Anchor-date math (`end=2026-04-20, days=3` → iterates `04-20, 04-19, 04-18`; DST boundary sanity).
  - Table formatter: synthetic `[]backfillResult` → expected string (golden).
  - `runBackfill` with a stub data provider (inject via a small interface inside the orchestration layer only; `cfg.Backfill bool` remains for the prod switch).
- Existing `planner_test.go` / `milp_test.go` keep passing after the `plan()` signature change.
- No integration test against real VM — too flaky for CI; the existing VM-fetcher tests cover that layer.

## Follow-ups (out of scope)

### PV shadow mask for live planner
The `sigenergy_pv_power_power_kw` curve over the last week shows:
- **06–09h local:** near-zero (0.01–0.23 kW) — the house shades the panels until mid-morning.
- **10–12h:** ramp from 0.45 → 1.02 kW.
- **13–14h:** peak (1.39–1.52 kW avg, 2.85–3.10 max).
- **15–19h:** gradual descent.
- **20–05h:** zero.

forecast.solar predicts a symmetric bell centred near solar noon (~13:00 CEST); reality is shifted right and has a flat morning shoulder. This means the live planner over-weights the 8–11h window when choosing "cheap solar hours" for the pump.

Proposed fix: `POOL_SOLAR_HOURLY_MASK` env var, 24 comma-separated multipliers in `[0,1]`, applied to `fetchSolarForecast`'s output by hour-of-day. Default = all 1s (no-op). Configurable per hour-of-day so the mask can be tuned against observed PV.

### Backfill-dedicated Grafana panel
If the stdout table isn't enough, add a panel that queries `{run="backfill"}` with `anchor_date` as a variable, to visualize any single backfilled day.

## Non-goals

- No historical solar forecast (forecast.solar has no real past-date API; their `history` endpoint is a calendar-day average and requires a paid plan).
- No backfill scheduling / cron. One-shot CLI only.
- No retro-write of `run=live` tag onto pre-existing live points in VM. New writes only.
