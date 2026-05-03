import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { reduceExpr, thresholdExpr, vmAlertQuery } from './helpers.ts';

export function energiPerFas(): alerting.RuleBuilder {
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
      summary: 'En av faserna är snart överbelastad',
    })
    .notificationSettings(new alerting.NotificationSettingsBuilder().receiver('Slack'))
    .withQuery(
      vmAlertQuery('A', 'union(tibber_powerL1{}, tibber_powerL2{}, tibber_powerL3{})', {
        intervalMs: 1000,
        rangeSeconds: 300,
      }),
    )
    .withQuery(reduceExpr('B', 'A', 'mean', 'last'))
    .withQuery(thresholdExpr('C', 'B', 'gt', 5750));
}
