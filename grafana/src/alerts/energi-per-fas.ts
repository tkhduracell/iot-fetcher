import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { vmExpr } from '../datasource.ts';
import { reduceExpr, thresholdExpr } from './helpers.ts';

export function energiPerFas(): alerting.RuleBuilder {
  const query = vmExpr('A', 'union(tibber_powerL1{}, tibber_powerL2{}, tibber_powerL3{})', '__auto');

  return new alerting.RuleBuilder('Energi per fas')
    .uid('feveqo0rnu3ggc')
    .ruleGroup('Irisgatan')
    .condition('C')
    .forDuration('5m')
    .noDataState('Alerting')
    .execErrState('Error')
    .annotations({
      __dashboardUid__: 'aehvj7vxn6vi8f',
      __panelId__: '6',
      summary: 'En av faserna över snart överbalastad',
    })
    .notificationSettings(new alerting.NotificationSettingsBuilder().receiver('Slack'))
    .withQuery(
      new alerting.QueryBuilder('A')
        .relativeTimeRange({ from: 300, to: 0 })
        .datasourceUid('cfc7gnph2ojr4d')
        .model(query),
    )
    .withQuery(reduceExpr('B', 'A', 'mean', 'last'))
    .withQuery(thresholdExpr('C', 'B', 'gt', 5750));
}
