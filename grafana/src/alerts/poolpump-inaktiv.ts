import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { reduceExpr, thresholdExpr, vmAlertQuery } from './helpers.ts';

export function poolpumpInaktiv(): alerting.RuleBuilder {
  return new alerting.RuleBuilder('Poolpump inaktiv')
    .uid('ffkvqdeth3qwwd')
    .ruleGroup('Irisgatan')
    .condition('C')
    .forDuration('3h')
    .noDataState('Alerting')
    .execErrState('Error')
    .annotations({ __dashboardUid__: 'irisgatan-v3', __panelId__: '18' })
    .notificationSettings(new alerting.NotificationSettingsBuilder().receiver('Grafana'))
    .withQuery(
      vmAlertQuery('B', 'avg_over_time(pool_iqpump_motordata_speed[5m])', {
        legendFormat: 'speed',
        intervalMs: 15000,
        rangeSeconds: 86400,
      }),
    )
    .withQuery(reduceExpr('A', 'B', 'last'))
    .withQuery(thresholdExpr('C', 'A', 'lt', 500));
}
