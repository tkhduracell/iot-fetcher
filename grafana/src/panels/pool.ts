import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import {
  StackingConfigBuilder, StackingMode,
  GraphThresholdsStyleConfigBuilder, GraphThresholdsStyleMode,
} from '@grafana/grafana-foundation-sdk/common';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmMetric, vmExpr } from '../datasource.ts';
import {
  greenRedThresholds, greenThreshold, thresholds, paletteColor,
  legendBottom, tooltipSingle, tooltipMulti,
  overrideDisplayAndColor, overrideDisplayName,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function poolPanels(): cog.Builder<dashboard.Panel>[] {
  // Vattentemperatur - Poolvärmepump (timeseries, 3 queries)
  const waterTemp = new TimeseriesBuilder()
    .title('Vattentemperatur - Poolvärmepump')
    .datasource(VM_DS)
    .unit('celsius')
    .axisSoftMin(25)
    .axisSoftMax(32)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('temp_incoming', 'Ingående', 'blue'),
      overrideDisplayAndColor('temp_outgoing', 'Utgående', 'red'),
      overrideDisplayAndColor('temp_target', 'Måltemperatur', 'purple'),
      overrideDisplayAndColor('pool_temperature_value', 'Pooltemp', 'light-green'),
    ])
    .withTarget(vmMetric('B', 'aqua_temp', 'temp_incoming'))
    .withTarget(vmMetric('A', 'aqua_temp', 'temp_outgoing'))
    .withTarget(vmMetric('C', 'aqua_temp', 'temp_target'))
    .withTarget(vmExpr('D', 'avg_over_time(pool_temperature_value[$__interval])', 'pool_temperature_value'))
    .gridPos({ h: 14, w: 12, x: 0, y: 30 });

  // Pooltemp (stat) - Sonoff sensor
  const poolTempStat = new StatBuilder()
    .title('Pooltemp')
    .datasource(VM_DS)
    .unit('celsius')
    .min(0)
    .max(30)
    .thresholds(greenRedThresholds(80))
    .withTarget(vmExpr('A', 'last_over_time(pool_temperature_value[$__interval])', 'Pooltemp'))
    .timeFrom('now-24h')
    .gridPos({ h: 7, w: 5, x: 12, y: 30 });

  // ΔT över värmeväxlaren (timeseries) — flödesindikator för poolvärmepumpen.
  // Y-axeln klippt till 0–4 °C: när cirkulationspumpen pausas står vattnet
  // stilla i värmeväxlaren och utgående-temp skenar (sett 8–12 °C i datat),
  // vilket bara säger "kompressorn körde nyss" och inget om flödet.
  const deltaT = new TimeseriesBuilder()
    .title('ΔT över värmeväxlaren')
    .description(
      'ΔT = utgående − ingående vattentemperatur över poolvärmepumpens växlare. ' +
      'Mäter om flödet genom värmepumpen är rätt avvägt med bypass-ventilerna.\n\n' +
      '**Mål: ~2 °C** vid full kompressorlast (Hayward / AquaCal-rekommendation för inverter-pumpar).\n\n' +
      '- **ΔT > 2 °C** → för lite flöde genom värmepumpen → stäng bypass-ventilen lite (mer vatten genom HP).\n' +
      '- **ΔT < 2 °C** → för mycket flöde → öppna bypass-ventilen lite.\n' +
      '- **ΔT ≈ 3 °C** = klart för lågt flöde, sämre COP och risk för högtryckslarm.\n\n' +
      'Varför lågt ΔT är bättre på en inverter-pump: kompressorn moduleras, så köldmediet ' +
      'håller sig nära vattentemperaturen. Mindre vatten-ΔT → mindre köldmedie/vatten-gap → bättre COP. ' +
      'Att jaga högre ΔT sparar inte cirkulationspumps-energi (den går ändå för filtrering).\n\n' +
      'Tuningprocedur: Justera ventilerna i små steg, vänta ~10 min tills steady state, ' +
      'avläs när `aqua_temp_power_usage` plateau:at (≥1800 W). Bortse från korta dippar — ' +
      'det är avfrostningscykler (~var 25:e minut vid kall ute-temp).\n\n' +
      'Y-axeln klippt 0–4 °C: värden över 4 °C är nästan alltid avstannat vatten i växlaren ' +
      'när cirkulationspumpen pausas, inte ett verkligt flödesproblem.'
    )
    .datasource(VM_DS)
    .unit('celsius')
    .min(0)
    .max(4)
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: 'yellow', value: 2 },
      { color: 'red', value: 3 },
    ]))
    .thresholdsStyle(new GraphThresholdsStyleConfigBuilder().mode(GraphThresholdsStyleMode.Dashed))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([overrideDisplayAndColor('delta_t', 'ΔT (utgående − ingående)', 'blue')])
    .withTarget(vmExpr(
      'A',
      'avg_over_time(aqua_temp_temp_outgoing[$__interval]) - avg_over_time(aqua_temp_temp_incoming[$__interval])',
      'delta_t',
    ))
    .gridPos({ h: 8, w: 12, x: 0, y: 44 });

  // Pool Energi (timeseries)
  const heatPump = new TimeseriesBuilder()
    .title('Pool Energi')
    .datasource(VM_DS)
    .unit('watt')
    .interval('5m')
    .axisSoftMin(15)
    .axisSoftMax(35)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .insertNulls(SPAN_NULLS_MS)
    .fillOpacity(10)
    .stacking(new StackingConfigBuilder().mode(StackingMode.Normal))
    .overrides([
      overrideDisplayName('pool_iqpump_motordata_power', 'Circulationspump'),
      overrideDisplayName('power_usage', 'Värmepump'),
    ])
    .withTarget(vmExpr('A', 'avg_over_time(pool_iqpump_motordata_power[$__interval])', 'pool_iqpump_motordata_power'))
    .withTarget(vmMetric('B', 'aqua_temp', 'power_usage'))
    .gridPos({ h: 7, w: 7, x: 17, y: 30 });

  // Pumpvarvtal (stat)
  const pumpSpeedStat = new StatBuilder()
    .title('Pumpvarvtal')
    .datasource(VM_DS)
    .unit('rotrpm')
    .decimals(0)
    .min(0)
    .max(3600)
    .thresholds(thresholds([{ color: 'blue', value: null }]))
    .withTarget(vmMetric('B', 'pool_iqpump_motordata', 'speed', { agg: 'LAST_VALUE' }))
    .timeFrom('now-24h')
    .gridPos({ h: 7, w: 5, x: 12, y: 37 });

  // Poolpump varvtal (timeseries)
  const pumpSpeedTs = new TimeseriesBuilder()
    .title('Poolpump varvtal')
    .datasource(VM_DS)
    .axisSoftMin(5)
    .axisSoftMax(20)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([overrideDisplayAndColor('speed', 'Varvtal', 'blue')])
    .withTarget(vmMetric('B', 'pool_iqpump_motordata', 'speed'))
    .gridPos({ h: 7, w: 7, x: 17, y: 37 });

  // Poolpump plan (timeseries) — 24h MILP schedule from pool-pump-planner
  const pumpPlan = new TimeseriesBuilder()
    .title('Smart Planner - Pumpschema (24h)')
    .datasource(VM_DS)
    .interval('15m')
    .lineInterpolation('stepAfter' as any)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('on', 'Pump på', 'blue'),
      overrideDisplayAndColor('price_sek_per_kwh', 'Spotpris (SEK/kWh)', 'yellow'),
      overrideDisplayAndColor('solar_forecast_masked_kwh', 'Solprognos maskad (kWh)', 'orange'),
      overrideDisplayAndColor('solar_forecast_kwh', 'Solprognos rådata (kWh)', 'light-orange'),
    ])
    // Strictly run="live" — the untagged legacy series in VM is a fossil
    // mixture of points from older deployments (e.g. when slot-size was 30m)
    // and would otherwise conflict with the current plan at overlapping
    // timestamps. max() collapses any remaining mode-variants (optimal vs
    // fallback on the same day) into one line.
    .withTarget(vmExpr('A', 'max(last_over_time(pool_iqpump_plan_on{run="live"}[$__interval]))', 'on'))
    .withTarget(vmExpr('B', 'max(last_over_time(pool_iqpump_plan_price_sek_per_kwh{run="live"}[$__interval]))', 'price_sek_per_kwh'))
    .withTarget(vmExpr('C', 'max(last_over_time(pool_iqpump_plan_solar_forecast_masked_kwh{run="live"}[$__interval]))', 'solar_forecast_masked_kwh'))
    .withTarget(vmExpr('D', 'max(last_over_time(pool_iqpump_plan_solar_forecast_kwh{run="live"}[$__interval]))', 'solar_forecast_kwh'))
    // Show the full ISO week (Mon 00:00 -> Sun 23:59:59) so the 24h forecast
    // sits in context of the rest of the week. Grafana rejects negative
    // timeShift, so this week-anchored form is the documented workaround for
    // including future slots in a panel with a bounded window.
    .timeFrom('now/w')
    .timeShift('0w/w')
    .gridPos({ h: 8, w: 12, x: 0, y: 52 });

  // Smart Planner — 30-day backfill cost comparison: optimizer vs naive
  // schedules. Each anchor_date is its own VM series (different tag) with one
  // point per day; sum without(anchor_date) collapses them so the panel draws
  // a single line with 30 points. anchor_date (backfill) and plan_date (live)
  // are both removed so the two sources collapse into a single line — live
  // runs fill in the most recent days that the backfill subcommand hasn't
  // covered yet.
  const pumpPlanCost = new TimeseriesBuilder()
    .title('Smart Planner - Kostnad (30 dagar)')
    .datasource(VM_DS)
    .unit('currencySEK')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(86_400_000)
    .interval('1d')
    .overrides([
      overrideDisplayAndColor('expected_cost_sek', 'Optimerad', 'yellow'),
      overrideDisplayAndColor('night_baseline_sek', 'Naiv 00-06', 'purple'),
      overrideDisplayAndColor('afternoon_baseline_sek', 'Naiv 12-18', 'red'),
    ])
    .withTarget(vmExpr('B', 'max without(anchor_date, plan_date, mode, missing_inputs, run) (pool_iqpump_plan_summary_expected_cost_sek{run=~"backfill|live"})', 'expected_cost_sek'))
    // Night/afternoon fixed-schedule baselines emitted alongside every plan
    // (both backfill and live). Plotting all three on the same panel makes the
    // optimizer's value directly visible: the gap between yellow and the
    // baselines is SEK the planner saved vs a naive always-at-this-time rule.
    .withTarget(vmExpr('D', 'max without(anchor_date, plan_date, mode, missing_inputs, run) (pool_iqpump_plan_summary_expected_cost_sek{run="baseline_night"})', 'night_baseline_sek'))
    .withTarget(vmExpr('E', 'max without(anchor_date, plan_date, mode, missing_inputs, run) (pool_iqpump_plan_summary_expected_cost_sek{run="baseline_afternoon"})', 'afternoon_baseline_sek'))
    .timeFrom('14d/d')
    .gridPos({ h: 8, w: 12, x: 12, y: 52 });

  // Pool kostnad — actual pump cost using SE4 spot price plus Swedish
  // grid+tax loading: (spot + 0.2584 transferfee + 0.36 energiskatt) * 1.25 VAT,
  // matching pool-pump-planner config defaults (March 2026 E.ON invoice).
  // Subquery resamples at 5m to cover the heat pump's 5m scrape interval; the
  // [5m] inner lookback ensures every step lands on a real sample. * 5 / 60
  // converts SEK/h × 5-min samples into SEK accumulated per bucket. The inner
  // sum() collapses incidental label variants (e.g. a stray device="00" series
  // alongside the real device="17") that show up over a YTD range so the
  // legend stays a single line per pump.
  const POOL_COST_FEES = '0.6184'; // 0.2584 transferfee + 0.36 energiskatt
  const POOL_COST_VAT = '1.25';    // 25% moms applied on top of spot+fees
  const poolCostExpr = (powerMetric: string): string =>
    `sum_over_time((sum(avg_over_time(${powerMetric}[5m])) / 1000 * ` +
    `(scalar(avg_over_time(energy_price_SEK_per_kWh{area="SE4"}[5m])) + ${POOL_COST_FEES}) * ${POOL_COST_VAT}` +
    `)[$__interval:5m]) * 5 / 60`;

  const poolCostDaily = new TimeseriesBuilder()
    .title('Pool kostnad per dag')
    .description(
      'Daglig elkostnad för pool-cirkulationspumpen och värmepumpen. ' +
      'Beräknas som energi × spotpris (SE4) + nätavgift + energiskatt + moms 25%, ' +
      'samma formel som pool-pump-planner använder.'
    )
    .datasource(VM_DS)
    .unit('currencySEK')
    .drawStyle('bars' as any)
    .fillOpacity(100)
    .axisSoftMin(0)
    .interval('1d')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(86_400_000)
    .stacking(new StackingConfigBuilder().mode(StackingMode.Normal))
    .overrides([
      overrideDisplayAndColor('pool_iqpump_motordata_power', 'Cirkulationspump', 'blue'),
      overrideDisplayAndColor('aqua_temp_power_usage', 'Värmepump', 'red'),
    ])
    .withTarget(vmExpr('A', poolCostExpr('pool_iqpump_motordata_power'), 'pool_iqpump_motordata_power'))
    .withTarget(vmExpr('B', poolCostExpr('aqua_temp_power_usage'), 'aqua_temp_power_usage'))
    .timeFrom('now/y')
    .gridPos({ h: 8, w: 16, x: 0, y: 60 });

  // Pool kostnad i år (stat) — accumulated YTD cost across both pumps.
  // sum() drops labels so the two metrics with disjoint label sets can be
  // added; `or vector(0)` handles the period before aqua_temp_power_usage
  // started emitting (April 2026). 15m subquery step keeps the YTD point
  // count under VM's per-series limit while still capturing pump-cycle
  // dynamics; spot price is hourly so 15m is plenty for the price factor.
  const poolCostYTD = new StatBuilder()
    .title('Pool kostnad i år')
    .description(
      'Ackumulerad elkostnad för poolen sedan årsskiftet. ' +
      'Inkluderar cirkulationspump + värmepump × spotpris (SE4) + nätavgift + energiskatt + moms 25%.'
    )
    .datasource(VM_DS)
    .unit('currencySEK')
    .thresholds(greenThreshold())
    .withTarget(vmExpr(
      'A',
      'sum_over_time((' +
        '((sum(avg_over_time(pool_iqpump_motordata_power[15m])) or vector(0)) + ' +
        '(sum(avg_over_time(aqua_temp_power_usage[15m])) or vector(0))) / 1000 * ' +
        `(scalar(avg_over_time(energy_price_SEK_per_kWh{area="SE4"}[15m])) + ${POOL_COST_FEES}) * ${POOL_COST_VAT}` +
      ')[$__range:15m]) * 15 / 60',
      'YTD',
    ))
    .timeFrom('now/y')
    .gridPos({ h: 8, w: 8, x: 16, y: 60 });

  // Smart Planner — 30-day planned-vs-slack hours. Split out from the cost
  // panel so the two unit families (SEK / hours) don't fight over a shared
  // axis. Planned = hours the planner committed to running; slack = hours
  // budgeted but not yet locked in (deferrable headroom).
  const pumpPlanHours = new TimeseriesBuilder()
    .title('Smart Planner - Drifttimmar (30 dagar)')
    .datasource(VM_DS)
    .unit('h')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(86_400_000)
    .interval('1d')
    .overrides([
      overrideDisplayAndColor('planned_hours', 'Planerade timmar', 'blue'),
      overrideDisplayAndColor('slack_hours', 'Slack (h)', 'orange'),
    ])
    .withTarget(vmExpr('A', 'max without(anchor_date, plan_date, mode, missing_inputs, run) (pool_iqpump_plan_summary_planned_hours{run=~"backfill|live"})', 'planned_hours'))
    .withTarget(vmExpr('C', 'max without(anchor_date, plan_date, mode, missing_inputs, run) (pool_iqpump_plan_summary_slack_hours{run=~"backfill|live"})', 'slack_hours'))
    .timeFrom('14d/d')
    .gridPos({ h: 8, w: 12, x: 12, y: 44 });

  return [waterTemp, poolTempStat, heatPump, pumpSpeedStat, pumpSpeedTs, deltaT, pumpPlanHours, pumpPlan, pumpPlanCost, poolCostDaily, poolCostYTD];
}
