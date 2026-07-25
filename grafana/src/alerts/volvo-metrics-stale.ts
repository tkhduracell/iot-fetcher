import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { reduceExpr, thresholdExpr, vmAlertQuery } from './helpers.ts';

/**
 * Fires when the Volvo XC40 metrics go stale — no new sample for 4h — which
 * means the Home Assistant Volvo integration has stopped reporting.
 *
 * `ha_volvo_xc40_battery_value` is the sentinel: all six Volvo series update
 * in lockstep (same scrape), so battery going stale means the whole group is.
 *
 * Staleness detection: `count_over_time(sentinel[4h])` returns the sample
 * count over the trailing 4h; VictoriaMetrics returns an *empty* result once
 * that window holds zero samples. `or vector(0)` turns that emptiness into a
 * concrete 0 so the threshold fires on a real value rather than depending on
 * noDataState. The 4h window itself is the delay, so the pending period is 0.
 *
 * The `sum(...)` wrapper is load-bearing: without it, `count_over_time(...)`
 * keeps the metric's labels while `vector(0)` has an empty label set, so `or`
 * returns BOTH series when data is present (e.g. `48{...}` AND `0{}`). Grafana
 * reduces each series independently, so the phantom `0{}` trips `lt 1` and the
 * rule fires even when data is fresh. `sum` collapses the count to a single
 * `{}` series that shares vector(0)'s label set, so `or` drops the fallback
 * whenever real data exists.
 */
export function volvoMetricsStale(): alerting.RuleBuilder {
  return new alerting.RuleBuilder('Volvo XC40 data saknas')
    .uid('volvo-stale-4h')
    .ruleGroup('Irisgatan')
    .condition('C')
    .forDuration('0s')
    .noDataState('Alerting')
    .execErrState('Error')
    .annotations({
      __dashboardUid__: 'irisgatan-v3',
      __panelId__: '44',
      summary: 'Inga nya Volvo XC40-mätvärden på 4h — HA-integrationen kan ha slutat rapportera',
    })
    .notificationSettings(new alerting.NotificationSettingsBuilder().receiver('Slack'))
    .withQuery(
      vmAlertQuery('A', 'sum(count_over_time(ha_volvo_xc40_battery_value[4h])) or vector(0)', {
        legendFormat: 'samples_4h',
        intervalMs: 60000,
        rangeSeconds: 600,
      }),
    )
    .withQuery(reduceExpr('B', 'A', 'last'))
    .withQuery(thresholdExpr('C', 'B', 'lt', 1));
}
