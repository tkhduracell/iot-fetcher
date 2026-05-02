import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { vmExpr } from '../datasource.ts';
import { reduceExpr, thresholdExpr } from './helpers.ts';

export function poolpumpVarvtal(): alerting.RuleBuilder {
  const query = vmExpr('B', 'pool_iqpump_motordata_speed{}', '__auto');

  return new alerting.RuleBuilder('Poolpump varvtal')
    .uid('eew9sexxd4tmoc')
    .ruleGroup('Irisgatan')
    .condition('C')
    .forDuration('5m')
    .noDataState('NoData')
    .execErrState('Error')
    .annotations({ __dashboardUid__: 'aehvj7vxn6vi8f', __panelId__: '26' })
    .notificationSettings(new alerting.NotificationSettingsBuilder().receiver('Grafana'))
    .withQuery(
      new alerting.QueryBuilder('B')
        .relativeTimeRange({ from: 172800, to: 0 })
        .datasourceUid('cfc7gnph2ojr4d')
        .model(query),
    )
    .withQuery(reduceExpr('A', 'B', 'last'))
    .withQuery(thresholdExpr('C', 'A', 'outside_range', 0, 4000));
}
