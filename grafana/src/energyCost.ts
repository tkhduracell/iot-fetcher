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
