import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { INFLUXDB_DS, influxSql, influxRawSql } from '../datasource.ts';
import {
  greenRedThresholds, thresholds, paletteColor,
  legendBottom, tooltipSingle,
  overrideDisplayAndColor,
} from '../helpers.ts';

export function poolPanels(): cog.Builder<dashboard.Panel>[] {
  // Vattentemperatur - Poolvärmepump (timeseries, 3 queries)
  const waterTemp = new TimeseriesBuilder()
    .title('Vattentemperatur - Poolvärmepump')
    .datasource(INFLUXDB_DS)
    .unit('celsius')
    .axisSoftMin(25)
    .axisSoftMax(32)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .overrides([
      overrideDisplayAndColor('temp_incoming', 'Ingående', 'blue'),
      overrideDisplayAndColor('temp_outgoing', 'Utgående', 'red'),
      overrideDisplayAndColor('temp_target', 'Måltemperatur', 'purple'),
    ])
    .withTarget(influxSql('B', 'aqua_temp', 'temp_incoming'))
    .withTarget(influxSql('A', 'aqua_temp', 'temp_outgoing'))
    .withTarget(influxSql('C', 'aqua_temp', 'temp_target'))
    .gridPos({ h: 14, w: 12, x: 0, y: 23 });

  // Pooltemp (stat)
  const poolTempStat = new StatBuilder()
    .title('Pooltemp')
    .datasource(INFLUXDB_DS)
    .unit('celsius')
    .min(0)
    .max(30)
    .thresholds(greenRedThresholds(80))
    .withTarget(influxSql('B', 'aqua_temp', 'temp_incoming'))
    .timeFrom('now-24h')
    .gridPos({ h: 7, w: 5, x: 12, y: 23 });

  // Poolvärmepump (timeseries)
  const heatPump = new TimeseriesBuilder()
    .title('Poolvärmepump')
    .datasource(INFLUXDB_DS)
    .axisSoftMin(15)
    .axisSoftMax(35)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .withTarget(influxSql('A', 'aqua_temp', 'power_usage'))
    .gridPos({ h: 7, w: 7, x: 17, y: 23 });

  // Pumpvarvtal (stat)
  const pumpSpeedStat = new StatBuilder()
    .title('Pumpvarvtal')
    .datasource(INFLUXDB_DS)
    .unit('rotrpm')
    .min(0)
    .max(3600)
    .thresholds(thresholds([{ color: 'blue', value: null }]))
    .withTarget(influxSql('B', 'pool_iqpump_motordata', 'speed', { agg: 'LAST_VALUE' }))
    .timeFrom('now-24h')
    .gridPos({ h: 7, w: 5, x: 12, y: 30 });

  // Poolpump varvtal (timeseries)
  const pumpSpeedTs = new TimeseriesBuilder()
    .title('Poolpump varvtal')
    .datasource(INFLUXDB_DS)
    .axisSoftMin(5)
    .axisSoftMax(20)
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .overrides([overrideDisplayAndColor('speed', 'Temperatur', 'blue')])
    .withTarget(influxSql('B', 'pool_iqpump_motordata', 'speed'))
    .gridPos({ h: 7, w: 7, x: 17, y: 30 });

  return [waterTemp, poolTempStat, heatPump, pumpSpeedStat, pumpSpeedTs];
}
