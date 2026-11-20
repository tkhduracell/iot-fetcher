import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { reduceExpr, thresholdExpr, vmAlertQuery } from './helpers.ts';

/**
 * Fires when the Sigenergy metrics go stale — no new sample for 30m — which
 * means sigenergy-bridge has stopped (container down, crashed, or stopped by
 * hand) or can no longer reach the inverter.
 *
 * `sigenergy_battery_soc_percent` is the sentinel: the bridge writes all
 * sigenergy_* series from the same poll, so one going stale means the whole
 * group is.
 *
 * Same staleness pattern as volvo-metrics-stale.ts: `sum(count_over_time(...))`
 * collapses to one `{}` series, and `or vector(0)` supplies a concrete 0 when
 * the window is empty, so the `lt 1` threshold fires on a real value. The
 * `sum` is load-bearing; see the volvo rule for why.
 *
 * `noData`/`execErr` are `KeepLast` so transient Caddy/network blips to the
 * VM datasource don't page; real staleness shows up as the count = 0 value.
 */
export function sigenergyStale(): alerting.RuleBuilder {
  return new alerting.RuleBuilder('Sigenergy data saknas')
    .uid('sigenergy-stale-30m')
    .ruleGroup('Irisgatan')
    .condition('C')
    .forDuration('0s')
    .noDataState('KeepLast')
    .execErrState('KeepLast')
    .annotations({
      summary: 'Inga nya Sigenergy-mätvärden på 30 min — sigenergy-bridge på rpi5 kan vara nere (docker compose ps sigenergy-bridge)',
    })
    .notificationSettings(new alerting.NotificationSettingsBuilder().receiver('Slack'))
    .withQuery(
      vmAlertQuery('A', 'sum(count_over_time(sigenergy_battery_soc_percent[30m])) or vector(0)', {
        legendFormat: 'samples_30m',
        intervalMs: 60000,
        rangeSeconds: 3600,
      }),
    )
    .withQuery(reduceExpr('B', 'A', 'last'))
    .withQuery(thresholdExpr('C', 'B', 'lt', 1));
}
