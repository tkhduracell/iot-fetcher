import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmMetric, vmExpr } from '../datasource.ts';
import {
  greenRedThresholds, greenThreshold, thresholds, paletteColor,
  legendBottom, tooltipSingle, tooltipMulti,
  overrideDisplayAndColor,
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

  // Poolvärmepump (timeseries)
  const heatPump = new TimeseriesBuilder()
    .title('Poolvärmepump')
    .datasource(VM_DS)
    .axisSoftMin(15)
    .axisSoftMax(35)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .insertNulls(SPAN_NULLS_MS)
    .withTarget(vmMetric('A', 'aqua_temp', 'power_usage'))
    .gridPos({ h: 7, w: 7, x: 17, y: 30 });

  // Pumpvarvtal (stat)
  const pumpSpeedStat = new StatBuilder()
    .title('Pumpvarvtal')
    .datasource(VM_DS)
    .unit('rotrpm')
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
    .title('Poolpump plan (24h)')
    .datasource(VM_DS)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('on', 'Pump på', 'blue'),
      overrideDisplayAndColor('price_sek_per_kwh', 'Spotpris (SEK/kWh)', 'yellow'),
      overrideDisplayAndColor('solar_kwh', 'Solprognos (kWh)', 'orange'),
    ])
    // run!="backfill" matches both untagged legacy writes and the new run="live"
    // tag. max() wrapper collapses multiple series that differ on mode/run
    // (e.g. an optimal series from today and a fallback series from an older
    // day) into one line on the plot.
    .withTarget(vmExpr('A', 'max(last_over_time(pool_iqpump_plan_on{run!="backfill"}[$__interval]))', 'on'))
    .withTarget(vmExpr('B', 'max(last_over_time(pool_iqpump_plan_price_sek_per_kwh{run!="backfill"}[$__interval]))', 'price_sek_per_kwh'))
    .withTarget(vmExpr('C', 'max(last_over_time(pool_iqpump_plan_solar_kwh{run!="backfill"}[$__interval]))', 'solar_kwh'))
    // Lock the panel to a fixed calendar-day window: today 00:00 -> 24:00.
    // Grafana rejects negative timeShift, so a rolling now-24h..now+24h window
    // isn't reachable at the panel level. This calendar-day form is the
    // documented workaround and still exposes the fresh 24h forecast.
    .timeFrom('now/d')
    .timeShift('0d/d')
    .gridPos({ h: 8, w: 12, x: 0, y: 52 });

  // Poolpump plan — 30-day backfill: per-day summary of planned hours + cost
  // emitted by `pool-pump-planner backfill`. Each anchor_date is its own VM
  // series (different tag) with one point per day; sum without(anchor_date)
  // collapses them so the panel draws a single line with 30 points.
  const pumpPlanBackfill = new TimeseriesBuilder()
    .title('Poolpump plan (30 dagar backfill)')
    .datasource(VM_DS)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('planned_hours', 'Planerade timmar', 'blue'),
      overrideDisplayAndColor('expected_cost_sek', 'Förväntad kostnad (SEK)', 'yellow'),
      overrideDisplayAndColor('slack_hours', 'Slack (h)', 'orange'),
    ])
    .withTarget(vmExpr('A', 'sum without(anchor_date, mode, missing_inputs) (pool_iqpump_plan_summary_planned_hours{run="backfill"})', 'planned_hours'))
    .withTarget(vmExpr('B', 'sum without(anchor_date, mode, missing_inputs) (pool_iqpump_plan_summary_expected_cost_sek{run="backfill"})', 'expected_cost_sek'))
    .withTarget(vmExpr('C', 'sum without(anchor_date, mode, missing_inputs) (pool_iqpump_plan_summary_slack_hours{run="backfill"})', 'slack_hours'))
    .timeFrom('now-30d')
    .gridPos({ h: 8, w: 12, x: 12, y: 52 });

  return [waterTemp, poolTempStat, heatPump, pumpSpeedStat, pumpSpeedTs, pumpPlan, pumpPlanBackfill];
}
