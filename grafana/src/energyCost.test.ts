import { test } from 'node:test';
import assert from 'node:assert/strict';
import { accumulatedCostExpr, loadedPriceExpr, spaPowerWattsExpr, spaCostExpr, SPA_JET_W, SPA_HEATER_W, SPA_CIRC_W } from './energyCost.ts';

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

test('spa wattage constants match the calibrated model', () => {
  assert.equal(SPA_JET_W, 2000);
  assert.equal(SPA_HEATER_W, 3000);
  assert.equal(SPA_CIRC_W, 80);
});

test('spaPowerWattsExpr sums each component with a label-safe zero fallback, except circ', () => {
  // The circulation-pump term has no `or vector(0)` fallback on purpose: it is the
  // metric present whenever the spa is monitored at all, so leaving it bare makes
  // the whole expression yield no point (not a real 0) for any window before the
  // spa was monitored, even though the price series (and the other terms' `or
  // vector(0)`) already has data further back.
  assert.equal(
    spaPowerWattsExpr('5m'),
    '((sum(avg_over_time(spa_pump_1_value[5m])) or vector(0)) * 2000' +
    ' + (sum(avg_over_time(spa_pump_2_value[5m])) or vector(0)) * 2000' +
    ' + (max(avg_over_time(spa_heater_on_value[5m])) or vector(0)) * 3000' +
    ' + sum(avg_over_time(spa_circulation_pump_value[5m])) * 80)',
  );
});

test('spaCostExpr wraps the power model in the loaded price integration', () => {
  const expr = spaCostExpr({ step: '15m', stepMinutes: 15, range: '$__range' });
  assert.ok(expr.startsWith('sum_over_time(('));
  assert.ok(expr.includes('spa_heater_on_value[15m]'));
  assert.ok(expr.includes('+ 0.6184) * 1.25'));
  assert.ok(expr.endsWith(')[$__range:15m]) * 15 / 60'));
});
