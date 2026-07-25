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
      vmAlertQuery('A', 'count_over_time(ha_volvo_xc40_battery_value[4h]) or vector(0)', {
        legendFormat: 'samples_4h',
        intervalMs: 60000,
        rangeSeconds: 600,
      }),
    )
    .withQuery(reduceExpr('B', 'A', 'last'))
    .withQuery(thresholdExpr('C', 'B', 'lt', 1));
}
