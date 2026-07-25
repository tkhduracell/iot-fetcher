import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { reduceExpr, thresholdExpr, vmAlertQuery } from './helpers.ts';

/**
 * ΔT across the heat exchanger (outgoing − incoming). Fires when the
 * difference stays above 3.25 °C for 6h — a sign of poor heat transfer.
 *
 * Backported verbatim from a UI-created rule (uid efrq0w4q0v4e8a) so the
 * Irisgatan group stays fully code-owned; the provisioning PUT replaces the
 * whole group, so a rule missing here would be deleted on the next deploy.
 */
export function deltaTVarmevaxlaren(): alerting.RuleBuilder {
  return new alerting.RuleBuilder('ΔT över värmeväxlaren')
    .uid('efrq0w4q0v4e8a')
    .ruleGroup('Irisgatan')
    .condition('C')
    .forDuration('6h')
    .keepFiringFor('2h')
    .noDataState('NoData')
    .execErrState('Error')
    .annotations({ __dashboardUid__: 'irisgatan-v3', __panelId__: '19' })
    .notificationSettings(new alerting.NotificationSettingsBuilder().receiver('Slack'))
    .withQuery(
      vmAlertQuery('A', 'avg_over_time(aqua_temp_temp_outgoing[5m]) - avg_over_time(aqua_temp_temp_incoming[5m])', {
        legendFormat: 'delta_t',
        intervalMs: 300000,
        rangeSeconds: 21600,
      }),
    )
    .withQuery(reduceExpr('B', 'A', 'last'))
    .withQuery(thresholdExpr('C', 'B', 'gt', 3.25));
}
