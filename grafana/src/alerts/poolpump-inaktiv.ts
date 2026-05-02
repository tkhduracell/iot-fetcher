import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { vmExpr } from '../datasource.ts';
import { reduceExpr, thresholdExpr } from './helpers.ts';

export function poolpumpInaktiv(): alerting.RuleBuilder {
  const query = vmExpr('B', 'avg_over_time(pool_iqpump_motordata_speed[5m])', 'speed');

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
      new alerting.QueryBuilder('B')
        .relativeTimeRange({ from: 86400, to: 0 })
        .datasourceUid('cfc7gnph2ojr4d')
        .model(query),
    )
    .withQuery(reduceExpr('A', 'B', 'last'))
    .withQuery(thresholdExpr('C', 'A', 'lt', 500));
}
