# Spa Energy & Cost Metric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model the Neptun Utö spa's power draw from its Home Assistant state signals, price it with the Swedish grid+tax loading, and surface cost and runtime panels in Grafana.

**Architecture:** Cost is computed at query time in MetricsQL inside the Grafana TypeScript SDK — there is no vmalert container, so VictoriaMetrics cannot evaluate recording rules. The fee/VAT constants currently local to the pool panels are extracted into a shared module and reused. Heater state gets a dedicated `spa_heater_on` metric, backfilled once from a quirk in the existing exporter data and written live by a Home Assistant template binary sensor.

**Tech Stack:** TypeScript (Node 25, `--experimental-strip-types`), `@grafana/grafana-foundation-sdk`, `node:test`, MetricsQL/VictoriaMetrics, Python 3 for the backfill script.

Full background, evidence and rationale: `docs/superpowers/specs/2026-07-29-spa-energy-cost-design.md`.

## Global Constraints

- Wattage constants: jets **2000 W each**, heater **3000 W**, circulation pump **80 W**.
- Grid fees **0.6184** SEK/kWh (0.2584 nätavgift + 0.36 energiskatt); VAT multiplier **1.25**.
- Price metric is `energy_price_SEK_per_kWh{area="SE4"}`.
- Panel titles and descriptions in **Swedish**, matching the existing pool panels.
- Run all commands from the `grafana/` directory unless stated otherwise. Tests: `npm test`.
- Never combine differently-named metrics with bare `or` — see Task 2 notes. `sum(x) or vector(0)` is safe *only* because `sum()` reduces to the empty label set `{}`, matching `vector(0)`.
- Commit after each task. Use `git commit -m "..." -m "..."`; never `$()` or heredocs.

---

### Task 1: Extract shared energy-cost constants

Pure refactor. The generated dashboard JSON must be **byte-identical** afterwards.

**Files:**
- Create: `grafana/src/energyCost.ts`
- Create: `grafana/src/energyCost.test.ts`
- Modify: `grafana/src/panels/pool.ts:223-228` and `:273-281`

**Interfaces:**
- Produces: `GRID_FEES_SEK_PER_KWH: string`, `VAT_MULTIPLIER: string`,
  `loadedPriceExpr(window: string): string`,
  `accumulatedCostExpr(powerWattsExpr: string, opts: { step: string; stepMinutes: number; range: string }): string`

- [ ] **Step 1: Write the failing test**

Create `grafana/src/energyCost.test.ts`:

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { accumulatedCostExpr, loadedPriceExpr } from './energyCost.ts';

test('loadedPriceExpr applies fees then VAT to the SE4 spot price', () => {
  assert.equal(
    loadedPriceExpr('5m'),
    '(scalar(avg_over_time(energy_price_SEK_per_kWh{area="SE4"}[5m])) + 0.6184) * 1.25',
  );
});

test('accumulatedCostExpr reproduces the existing pool per-interval expression', () => {
  const expected =
    'sum_over_time((sum(avg_over_time(pool_iqpump_motordata_power[5m])) / 1000 * ' +
    '(scalar(avg_over_time(energy_price_SEK_per_kWh{area="SE4"}[5m])) + 0.6184) * 1.25' +
    ')[$__interval:5m]) * 5 / 60';

  assert.equal(
    accumulatedCostExpr('sum(avg_over_time(pool_iqpump_motordata_power[5m]))', {
      step: '5m',
      stepMinutes: 5,
      range: '$__interval',
    }),
    expected,
  );
});

test('accumulatedCostExpr reproduces the existing pool YTD expression', () => {
  const power =
    '((sum(avg_over_time(pool_iqpump_motordata_power[15m])) or vector(0)) + ' +
    '(sum(avg_over_time(aqua_temp_power_usage[15m])) or vector(0)))';
  const expected =
    'sum_over_time((' + power + ' / 1000 * ' +
    '(scalar(avg_over_time(energy_price_SEK_per_kWh{area="SE4"}[15m])) + 0.6184) * 1.25' +
    ')[$__range:15m]) * 15 / 60';

  assert.equal(
    accumulatedCostExpr(power, { step: '15m', stepMinutes: 15, range: '$__range' }),
    expected,
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd grafana && npm test`
Expected: FAIL — cannot find module `./energyCost.ts`.

- [ ] **Step 3: Write the implementation**

Create `grafana/src/energyCost.ts`:

```ts
/**
 * Swedish grid + tax loading applied on top of the SE4 spot price, matching
 * pool-pump-planner config defaults (March 2026 E.ON invoice).
 *
 * Not pool-specific — same house, same meter, same tariff — so the spa panels
 * reuse these. Kept as strings because they are interpolated into MetricsQL.
 */
export const GRID_FEES_SEK_PER_KWH = '0.6184'; // 0.2584 nätavgift + 0.36 energiskatt
export const VAT_MULTIPLIER = '1.25';          // 25% moms applied on top of spot+fees

/** SEK/kWh actually paid: (spot + fees) * VAT, averaged over `window`. */
export function loadedPriceExpr(window: string): string {
  return (
    `(scalar(avg_over_time(energy_price_SEK_per_kWh{area="SE4"}[${window}])) ` +
    `+ ${GRID_FEES_SEK_PER_KWH}) * ${VAT_MULTIPLIER}`
  );
}

/**
 * Integrate an instantaneous-watts expression into accumulated SEK.
 *
 * The subquery resamples at `step` so every point lands on a real sample;
 * `* stepMinutes / 60` converts SEK/h sampled every `stepMinutes` into SEK
 * accumulated per bucket. `range` is the outer bucket ($__interval per bar,
 * or $__range for a single total).
 */
export function accumulatedCostExpr(
  powerWattsExpr: string,
  opts: { step: string; stepMinutes: number; range: string },
): string {
  return (
    `sum_over_time((${powerWattsExpr} / 1000 * ${loadedPriceExpr(opts.step)}` +
    `)[${opts.range}:${opts.step}]) * ${opts.stepMinutes} / 60`
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd grafana && npm test`
Expected: PASS (3 new tests).

- [ ] **Step 5: Capture the current dashboard JSON as a baseline**

Run: `cd grafana && npm run build > /tmp/dashboard-before.json`

Note: `npm run build` sets `GRAFANA_SKIP_UPLOAD=1`, so this does **not** deploy.

- [ ] **Step 6: Rewire pool.ts to use the shared helpers**

In `grafana/src/panels/pool.ts`, add to the existing import block near the top:

```ts
import { accumulatedCostExpr } from '../energyCost.ts';
```

Replace the `POOL_COST_FEES` / `POOL_COST_VAT` / `poolCostExpr` block (currently lines 223-228) with:

```ts
  const poolCostExpr = (powerMetric: string): string =>
    accumulatedCostExpr(`sum(avg_over_time(${powerMetric}[5m]))`, {
      step: '5m',
      stepMinutes: 5,
      range: '$__interval',
    });
```

Keep the explanatory comment block above it (lines 214-222) — it documents the
`sum()` label-collapsing rationale and is still accurate.

Replace the `poolCostYTD` target expression (currently lines 273-281) with:

```ts
    .withTarget(vmExpr(
      'A',
      accumulatedCostExpr(
        '((sum(avg_over_time(pool_iqpump_motordata_power[15m])) or vector(0)) + ' +
        '(sum(avg_over_time(aqua_temp_power_usage[15m])) or vector(0)))',
        { step: '15m', stepMinutes: 15, range: '$__range' },
      ),
      'YTD',
    ))
```

- [ ] **Step 7: Verify the generated JSON is unchanged**

Run: `cd grafana && npm run build > /tmp/dashboard-after.json && diff /tmp/dashboard-before.json /tmp/dashboard-after.json && echo IDENTICAL`
Expected: prints `IDENTICAL` with no diff output.

If there is a diff, the extraction changed behaviour — fix it before committing.

- [ ] **Step 8: Run the full test suite**

Run: `cd grafana && npm test`
Expected: PASS, including the pre-existing `dashboard.test.ts`.

- [ ] **Step 9: Commit**

```bash
git add grafana/src/energyCost.ts grafana/src/energyCost.test.ts grafana/src/panels/pool.ts
git commit -m "refactor(grafana): extract shared energy cost constants" -m "Fees and VAT are not pool-specific; the spa panels need the same loading. Generated dashboard JSON is byte-identical."
```

---

### Task 2: Spa power and cost expressions

**Files:**
- Modify: `grafana/src/energyCost.ts`
- Modify: `grafana/src/energyCost.test.ts`

**Interfaces:**
- Consumes: `accumulatedCostExpr` from Task 1.
- Produces: `SPA_JET_W`, `SPA_HEATER_W`, `SPA_CIRC_W` (numbers),
  `spaPowerWattsExpr(window: string): string`,
  `spaCostExpr(opts: { step: string; stepMinutes: number; range: string }): string`

**Why `or vector(0)` is safe here:** each term is wrapped in an aggregation
(`sum(...)` / `max(...)`) which reduces it to the empty label set `{}`, matching
`vector(0)`'s label set, so `or` drops the fallback whenever real data exists. Without
the aggregation, `or` would return *both* series and double-count. Do not remove the
aggregations.

**Why `max` for the heater:** backfilled samples carry `run="backfill"` while live
samples carry no `run` label — two distinct series. `max` collapses them to one; `sum`
would double-count in any accidental overlap and silently double the modelled heater
energy.

- [ ] **Step 1: Write the failing test**

Append to `grafana/src/energyCost.test.ts`:

```ts
import { spaPowerWattsExpr, spaCostExpr, SPA_JET_W, SPA_HEATER_W, SPA_CIRC_W } from './energyCost.ts';

test('spa wattage constants match the calibrated model', () => {
  assert.equal(SPA_JET_W, 2000);
  assert.equal(SPA_HEATER_W, 3000);
  assert.equal(SPA_CIRC_W, 80);
});

test('spaPowerWattsExpr sums each component with a label-safe zero fallback', () => {
  assert.equal(
    spaPowerWattsExpr('5m'),
    '((sum(avg_over_time(spa_pump_1_value[5m])) or vector(0)) * 2000' +
    ' + (sum(avg_over_time(spa_pump_2_value[5m])) or vector(0)) * 2000' +
    ' + (max(avg_over_time(spa_heater_on_value[5m])) or vector(0)) * 3000' +
    ' + (sum(avg_over_time(spa_circulation_pump_value[5m])) or vector(0)) * 80)',
  );
});

test('spaCostExpr wraps the power model in the loaded price integration', () => {
  const expr = spaCostExpr({ step: '15m', stepMinutes: 15, range: '$__range' });
  assert.ok(expr.startsWith('sum_over_time(('));
  assert.ok(expr.includes('spa_heater_on_value[15m]'));
  assert.ok(expr.includes('+ 0.6184) * 1.25'));
  assert.ok(expr.endsWith(')[$__range:15m]) * 15 / 60'));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd grafana && npm test`
Expected: FAIL — `spaPowerWattsExpr` is not exported.

- [ ] **Step 3: Write the implementation**

Append to `grafana/src/energyCost.ts`:

```ts
/**
 * Spa component wattages.
 *
 * Jets and heater are nameplate-derived round numbers; the circulation pump is
 * measured (median 76 W over 73 isolated transitions, 95% CI [62, 88]). The circ
 * figure is a *subsystem* value — the Balboa BP2100G0 slaves the ozonator to the
 * circ pump, so the observed step is circ + ozone when both run.
 *
 * See docs/superpowers/specs/2026-07-29-spa-energy-cost-design.md.
 */
export const SPA_JET_W = 2000;
export const SPA_HEATER_W = 3000;
export const SPA_CIRC_W = 80;

/**
 * Modelled instantaneous spa draw in watts.
 *
 * Every term is aggregated before `or vector(0)` so the fallback shares the empty
 * label set and is dropped when real data exists. The heater uses `max` rather than
 * `sum` because backfilled (run="backfill") and live samples are separate series.
 */
export function spaPowerWattsExpr(window: string): string {
  return (
    `((sum(avg_over_time(spa_pump_1_value[${window}])) or vector(0)) * ${SPA_JET_W}` +
    ` + (sum(avg_over_time(spa_pump_2_value[${window}])) or vector(0)) * ${SPA_JET_W}` +
    ` + (max(avg_over_time(spa_heater_on_value[${window}])) or vector(0)) * ${SPA_HEATER_W}` +
    ` + (sum(avg_over_time(spa_circulation_pump_value[${window}])) or vector(0)) * ${SPA_CIRC_W})`
  );
}

/** Accumulated spa cost in SEK over `range`. */
export function spaCostExpr(opts: { step: string; stepMinutes: number; range: string }): string {
  return accumulatedCostExpr(spaPowerWattsExpr(opts.step), opts);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd grafana && npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add grafana/src/energyCost.ts grafana/src/energyCost.test.ts
git commit -m "feat(grafana): add spa power and cost expressions" -m "Models jets, heater and circulation pump; heater uses max() so backfilled and live series cannot double-count."
```

---

### Task 3: Spa panels and dashboard row reflow

Adds three panels to the Spabadet row. That row is full (panels occupy y=69-76, the
Energi row starts at y=77), so a second band at y=77 is needed and **every row below
Spabadet shifts down by 8**.

**Files:**
- Modify: `grafana/src/panels/spa.ts`
- Modify: `grafana/src/dashboard.ts` (row y values)
- Modify: `grafana/src/panels/energy.ts`, `volvo.ts`, `navimow.ts`, `eufy.ts`, `house.ts`, `system.ts`, `wud.ts` (panel y values)

**Interfaces:**
- Consumes: `spaCostExpr` from Task 2.
- Produces: no new exports; `spaPanels()` returns six builders instead of three.

- [ ] **Step 1: Run the existing layout test to confirm a clean baseline**

Run: `cd grafana && npm test`
Expected: PASS. `dashboard.test.ts` asserts every panel's y falls within its row's bounds — it is the safety net for this whole task.

- [ ] **Step 2: Shift every row below Spabadet down by 8**

In `grafana/src/dashboard.ts`, change **only** the `y` values of these `RowBuilder` calls:

| Row | Old y | New y |
|---|---|---|
| Energi | 77 | 85 |
| Volvo XC40 | 108 | 116 |
| Navimow | 117 | 125 |
| Eufy Cameras | 126 | 134 |
| Tapo | 135 | 143 |
| System | 143 | 151 |
| Docker / WUD | 160 | 168 |

Leave Huset, Belysning (y=15), Poolen (y=23) and Spabadet (y=68) unchanged.

- [ ] **Step 3: Shift the panels in those rows down by 8**

Change only the `y` value inside each `gridPos({...})`:

| File | Old y → New y |
|---|---|
| `panels/energy.ts` | 78→86 (×4), 86→94 (×3), 94→102 (×3), 100→108 (×3) |
| `panels/volvo.ts` | 109→117 (×4) |
| `panels/navimow.ts` | 118→126 (×2) |
| `panels/eufy.ts` | 127→135 (×3) |
| `panels/house.ts` | 136→144 (×1 — the Tapo panel only; leave y=1 and y=8 alone) |
| `panels/system.ts` | 144→152 (×3), 152→160 (×1) |
| `panels/wud.ts` | 161→169 (×3) |

Work bottom-up (wud → energy) so earlier edits do not collide with later search targets.

- [ ] **Step 4: Run the layout test**

Run: `cd grafana && npm test`
Expected: PASS. A failure names the offending panel and the expected y range — fix that panel's y and re-run.

- [ ] **Step 5: Commit the reflow separately**

Committing the mechanical shift on its own keeps the next diff readable.

```bash
git add grafana/src/dashboard.ts grafana/src/panels
git commit -m "refactor(grafana): shift rows below Spabadet down by 8" -m "Frees a second panel band at y=77 for the spa cost panels."
```

- [ ] **Step 6: Add the three spa panels**

In `grafana/src/panels/spa.ts`, add one import:

```ts
import { spaCostExpr } from '../energyCost.ts';
```

Everything else the new panels use (`StatBuilder`, `TimeseriesBuilder`, `greenThreshold`,
`paletteColor`, `legendBottom`, `tooltipMulti`, `vmExpr`, `VM_DS`) is already imported in
this file — do not add duplicate imports.

Then, before the `return` statement, add:

```ts
  // Modelled spa cost. Power comes from HA state signals × calibrated wattages
  // (jets 2000 W, heater 3000 W, circ 80 W), priced with the same SE4 spot +
  // nätavgift + energiskatt + moms loading the pool panels use.
  const KOSTNAD_BESKRIVNING =
    'Modellerad elkostnad för spabadet: jetpumpar 2000 W, värmare 3000 W, ' +
    'cirkulationspump 80 W (uppmätt). Pris = spotpris (SE4) + nätavgift + ' +
    'energiskatt + moms 25%. Cirkulationspumpens värde inkluderar ozonaggregatet, ' +
    'som styrs tillsammans med pumpen.';

  const spaCostLastMonth = new StatBuilder()
    .title('Spa kostnad senaste månaden')
    .description(KOSTNAD_BESKRIVNING)
    .datasource(VM_DS)
    .unit('currencySEK')
    .thresholds(greenThreshold())
    .withTarget(vmExpr(
      'A',
      spaCostExpr({ step: '15m', stepMinutes: 15, range: '$__range' }),
      '30 dagar',
    ))
    .timeFrom('30d')
    .gridPos({ h: 8, w: 8, x: 0, y: 77 });

  // 1h resampling (not 15m) keeps the 12-month point count well under VM's
  // per-series limit; the price series is 15-minutely, so hourly averaging is
  // more than adequate for monthly totals.
  const spaCostPerMonth = new TimeseriesBuilder()
    .title('Spa kostnad per månad')
    .description(
      KOSTNAD_BESKRIVNING +
      ' Månader före mätdatans början visas som tomma, inte som noll.'
    )
    .datasource(VM_DS)
    .unit('currencySEK')
    .drawStyle('bars' as any)
    .fillOpacity(100)
    .axisSoftMin(0)
    .interval('1M')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .withTarget(vmExpr(
      'A',
      spaCostExpr({ step: '1h', stepMinutes: 60, range: '$__interval' }),
      'Spa',
    ))
    .timeFrom('12M')
    .gridPos({ h: 8, w: 8, x: 8, y: 77 });

  // avg_over_time × 24 rather than sum_over_time × sample-interval: robust to the
  // exporter's irregular ~60 s cadence.
  const circRuntime = new StatBuilder()
    .title('Cirkulationspump drifttid 24h')
    .description('Antal timmar cirkulationspumpen varit igång det senaste dygnet.')
    .datasource(VM_DS)
    .unit('h')
    .thresholds(greenThreshold())
    .withTarget(vmExpr(
      'A',
      'sum(avg_over_time(spa_circulation_pump_value[24h])) * 24',
      'Drifttid',
    ))
    .gridPos({ h: 8, w: 8, x: 16, y: 77 });
```

Change the return to:

```ts
  return [spaTs, spaStat, spaCirculation, spaCostLastMonth, spaCostPerMonth, circRuntime];
```

- [ ] **Step 7: Run the tests**

Run: `cd grafana && npm test`
Expected: PASS — the new panels sit at y=77 (77-84), inside the Spabadet row's bounds of [69, 84].

- [ ] **Step 8: Render the dashboard JSON and eyeball the new panels**

Run: `cd grafana && npm run build > /tmp/dashboard-spa.json && grep -c '"title": "Spa kostnad' /tmp/dashboard-spa.json`
Expected: `2`.

This does not deploy — `build` sets `GRAFANA_SKIP_UPLOAD=1`.

- [ ] **Step 9: Commit**

```bash
git add grafana/src/panels/spa.ts
git commit -m "feat(grafana): add spa cost and circ runtime panels" -m "Cost for the last 30 days and per month over 12 months, plus circulation pump runtime for the last 24h."
```

---

### Task 4: `spa_heater_on` backfill script

Reconstructs heater history from the exporter quirk described in the spec: for
`climate.bp2100g0`, `hvac_action="off"` is written as `value=0` while `heating`/`idle`
are written as `state_text="..."` with **no** `_value` sample. So the *presence* of a
`spa_climate_hvac_action_state_text` sample means "not off".

**Files:**
- Create: `scripts/backfill-spa-heater.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `spa_heater_on,run=backfill value=<0|1>` samples in VictoriaMetrics,
  read by `spaPowerWattsExpr` from Task 2 as `spa_heater_on_value`.

**Safety:** this writes to production VictoriaMetrics. `--dry-run` is the default;
writing requires an explicit `--write` flag.

- [ ] **Step 1: Write the script**

Create `scripts/backfill-spa-heater.py`:

```python
#!/usr/bin/env python3
"""Backfill spa_heater_on from the exporter's complementary hvac_action series.

The HA->VM exporter writes climate.bp2100g0's hvac_action as `value=0` when it is
"off", but as `state_text="heating"` (a string, stored as 0 by VM) when it is not.
The two series are therefore complementary, and the PRESENCE of a state_text sample
means the heater was running. Validated against HA's own recorder: presence-derived
duty over 10 days is 14.358% vs HA's 14.3%.

Writes `spa_heater_on,run=backfill value=0|1` at 1-minute resolution. Live samples
(from the HA template binary sensor) carry no `run` label, so the two never collide
-- but they must not overlap in time either, hence --end-before.

Dry-run by default; pass --write to actually POST.
"""
import argparse
import subprocess
import sys
import json
import os
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VM_QUERY = os.path.join(REPO_ROOT, "scripts", "vm-query.sh")
STATE_TEXT = "spa_climate_hvac_action_state_text"  # present <=> hvac_action != "off"
VALUE = "spa_climate_hvac_action_value"            # present <=> hvac_action == "off"


def vm_range(promql, start, end, step="60s"):
    out = subprocess.run(
        [VM_QUERY, "--format", "json", "range", promql,
         "--start", start, "--end", end, "--step", step],
        capture_output=True, text=True, timeout=300,
    )
    if out.returncode != 0:
        raise SystemExit(f"vm-query failed: {out.stderr[:2000]}")
    d = json.loads(out.stdout)
    if d.get("status") != "success":
        raise SystemExit(f"query error: {json.dumps(d)[:2000]}")
    r = d["data"]["result"]
    return {int(float(ts)): float(v) for ts, v in r[0]["values"]} if r else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="RFC3339, e.g. 2026-03-01T00:00:00Z")
    ap.add_argument("--end", required=True, help="RFC3339; must precede the first live sample")
    ap.add_argument("--write", action="store_true", help="actually POST to VictoriaMetrics")
    args = ap.parse_args()

    # Both series are queried because count_over_time yields NO POINT (not 0) when
    # a window contains no samples. Reading only state_text would therefore return
    # exclusively "on" minutes and compute a 100% duty cycle.
    #
    #   state_text present -> hvac_action was heating/idle -> 1
    #   value present      -> hvac_action was "off"        -> 0
    #   neither present    -> exporter was down; emit nothing rather than
    #                         fabricating "off" across an outage.
    on_win = vm_range(f"count_over_time({STATE_TEXT}[2m])", args.start, args.end)
    off_win = vm_range(f"count_over_time({VALUE}[2m])", args.start, args.end)
    if not on_win:
        raise SystemExit("no state_text samples in range -- nothing to backfill")

    lines = []
    on_minutes = 0
    covered = sorted(set(on_win) | set(off_win))
    for ts in covered:
        # A 2m window straddling a transition can contain both; treat as on.
        on = 1 if on_win.get(ts, 0) > 0 else 0
        on_minutes += on
        lines.append(f"spa_heater_on,run=backfill value={on} {ts}")

    total = len(lines)
    gaps = 0
    if covered:
        expected = (covered[-1] - covered[0]) // 60 + 1
        gaps = max(0, expected - total)
    first, last = covered[0], covered[-1]
    print(f"range        : {datetime.fromtimestamp(first, tz=timezone.utc).isoformat()}"
          f" .. {datetime.fromtimestamp(last, tz=timezone.utc).isoformat()}")
    print(f"samples      : {total}")
    print(f"uncovered    : {gaps} min (exporter gaps -- no sample written)")
    print(f"heater on    : {on_minutes} min ({on_minutes/60:.1f} h)")
    print(f"duty         : {100*on_minutes/total:.3f}%  (expect ~14-18%)")
    print(f"last sample  : {datetime.fromtimestamp(last, tz=timezone.utc).isoformat()}"
          f"  <-- must precede the first live spa_heater_on sample")

    if not args.write:
        print("\nDRY RUN -- nothing written. Re-run with --write to POST.")
        return

    import urllib.request
    base = os.environ.get("VM_BASE_URL")
    token = os.environ.get("INFLUX_TOKEN")
    if not base or not token:
        raise SystemExit("set VM_BASE_URL and INFLUX_TOKEN to write "
                         "(source them the same way scripts/vm-env.sh does)")

    body = "\n".join(lines).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/write?precision=s",
        data=body,
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        print(f"wrote {total} samples -- HTTP {resp.status}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make it executable and run the dry run**

```bash
chmod +x scripts/backfill-spa-heater.py
./scripts/backfill-spa-heater.py --start 2026-03-01T00:00:00Z --end 2026-07-29T00:00:00Z
```

Expected: a summary with `samples` in the low hundreds of thousands and `duty` between
roughly 14% and 18%. **If duty is outside that range, stop** — the presence heuristic
is not behaving as validated and writing would corrupt the series.

- [ ] **Step 3: Commit the script (still nothing written to VM)**

```bash
git add scripts/backfill-spa-heater.py
git commit -m "feat(scripts): add spa_heater_on backfill" -m "Reconstructs heater history from the complementary hvac_action state_text series. Dry-run by default."
```

- [ ] **Step 4: STOP — hand back for approval before writing**

Do not run `--write`. Report the dry-run summary and let the human decide. Writing to
production VictoriaMetrics is a mutating action outside this plan's autonomy.

---

### Task 5: Fix enum export in the HA exporter (sibling repo)

**Different repository:** `/Users/filip/Desktop/own/home-assistant-victoria-metrics-exporter`.
Commit there, not in `iot_fetcher`.

This does **not** feed the spa panels — they read `spa_heater_on`. It is an independent
correctness fix that repairs `spa_temperature_range_state_text` ("Temp Range", currently
plotting a flat 0) and `spa_climate_state_text`.

**Files:**
- Modify: `custom_components/victoria_metrics/const.py`
- Modify: `custom_components/victoria_metrics/attributes.py`

- [ ] **Step 1: Confirm which commit is deployed**

```bash
cd /Users/filip/Desktop/own/home-assistant-victoria-metrics-exporter
git status
git log --oneline -3
git log --oneline -3 origin/main
```

The local checkout was last seen on branch `add-vm-source-sensors` at `fa27ab1` while
`origin/main` was at `653d27c`. Branch from whatever is actually deployed to rpi5. If
that is ambiguous, stop and ask.

- [ ] **Step 2: Read the current mapping code**

Read `const.py` (the `STATE_MAP` definition) and `attributes.py:70-89`
(`_process_attribute`) before editing, so the new map follows their existing style.

- [ ] **Step 3: Add the enum map**

In `const.py`, alongside `STATE_MAP`, add:

```python
# Climate hvac_action values. HA reports these as strings; without a numeric
# mapping they fall through to state_text= and never produce a _value sample,
# which makes "is it heating" unqueryable in VictoriaMetrics.
# 0 stays "off" so existing samples remain consistent.
CLIMATE_ACTION_MAP = {
    "off": 0.0,
    "idle": 1.0,
    "heating": 2.0,
    "cooling": 3.0,
    "drying": 4.0,
    "fan": 5.0,
    "preheating": 6.0,
    "defrosting": 7.0,
}
```

- [ ] **Step 4: Apply it in `_process_attribute`**

The current fallback chain ends by returning the raw string:

```python
try:
    return float(raw_value)
except (ValueError, TypeError):
    lower = raw_value.lower()
    return STATE_MAP.get(lower, raw_value)
```

Consult the per-attribute map before the generic `STATE_MAP` fallback:

```python
try:
    return float(raw_value)
except (ValueError, TypeError):
    lower = raw_value.lower()
    if attr_name == "hvac_action" and lower in CLIMATE_ACTION_MAP:
        return CLIMATE_ACTION_MAP[lower]
    return STATE_MAP.get(lower, raw_value)
```

**Confirm the parameter name first.** The snippet above assumes `_process_attribute`
already receives the attribute name; if it does not, thread it through from
`extract_attribute_lines` (which iterates the attributes and already knows each name)
rather than inferring it inside the function. Add the `CLIMATE_ACTION_MAP` import
alongside the existing `STATE_MAP` import.

- [ ] **Step 5: Test**

Run the repo's own test command (check its `README`/`pyproject.toml`/`Makefile` for the
runner; do not assume pytest). Add a test asserting `hvac_action="heating"` yields
`2.0` rather than the string `"heating"`.

- [ ] **Step 6: Commit in the sibling repo**

```bash
git add custom_components/victoria_metrics/const.py custom_components/victoria_metrics/attributes.py
git commit -m "fix: map climate hvac_action enums to numeric values" -m "Without this, only 'off' mapped (via STATE_MAP), so heating/idle were written as state_text= with no numeric _value sample at all."
```

- [ ] **Step 7: STOP — do not deploy**

Deploying means updating the integration inside the `home-assistant-config` volume on
rpi5 and restarting Home Assistant. Report that the change is committed and let the
human deploy.

**Migration note to include in the report:** after deploying, `heating` starts emitting
`value=2` and **stops** emitting `state_text`. Any backfill (Task 4) must therefore cover
only the period *before* the deploy, and `--end` must precede it.

---

## Deferred — not in this plan

**HA template binary sensor (`spa_heater_on` going forward).** Requires editing
`configuration.yaml` inside the `home-assistant-config` docker volume on rpi5 and
restarting HA, then adding the export mapping via HA's Victoria Metrics sidebar panel.
Manual, documented in the spec §2. Until it exists the heater term contributes 0 for
live data and the panels show jets + circ only (the `or vector(0)` fallback degrades
gracefully rather than erroring).

**Stacked energy breakdown panel.** Blocked on the `aqua_temp_power_usage` 10× scale
bug — the pool term would dominate and make the chart useless. Spec §7 and §8.

**`aqua_temp_power_usage` fix.** Needs a human decision: correct at the writer
(historical data stays wrong, needs backfill or rename) versus compensate in the query
(history reads correctly, metric stays misleading for future consumers). Spec §8.
