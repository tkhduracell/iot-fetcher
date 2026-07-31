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
