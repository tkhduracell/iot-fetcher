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
