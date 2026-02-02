import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import { PanelBuilder as TextBuilder } from '@grafana/grafana-foundation-sdk/text';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { INFLUXDB_DS, influxSql } from '../datasource.ts';
import { greenThreshold, paletteColor, legendBottom, tooltipSingle } from '../helpers.ts';

export function eufyPanels(): cog.Builder<dashboard.Panel>[] {
  // Eufy Battery (timeseries + labelsToFields)
  const batteryTs = new TimeseriesBuilder()
    .title('Eufy Battery')
    .datasource(INFLUXDB_DS)
    .unit('percent')
    .max(100)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .withTransformation({ id: 'labelsToFields', options: { valueLabel: 'device_name' } })
    .withTarget(influxSql('A', 'eufy_device', 'battery'))
    .timeFrom('30d/d')
    .gridPos({ h: 8, w: 12, x: 0, y: 121 });

  // Eufy Battery Temperature (timeseries + labelsToFields)
  const batteryTemp = new TimeseriesBuilder()
    .title('Eufy Battery Temperature')
    .datasource(INFLUXDB_DS)
    .unit('celsius')
    .min(0)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .withTransformation({ id: 'labelsToFields', options: { valueLabel: 'device_name' } })
    .withTarget(influxSql('A', 'eufy_device', 'batteryTemperature'))
    .timeFrom('30d/d')
    .gridPos({ h: 8, w: 12, x: 12, y: 121 });

  // Eufy Workingdays (stat)
  const workingDays = new StatBuilder()
    .title('Eufy Workingdays')
    .datasource(INFLUXDB_DS)
    .unit('d')
    .thresholds(greenThreshold())
    .withTransformation({ id: 'labelsToFields', options: { valueLabel: 'device_name' } })
    .withTarget(
      influxSql('A', 'eufy_device', 'detectionStatisticsWorkingDays', { agg: 'LAST_VALUE' }),
    )
    .timeFrom('30d/d')
    .gridPos({ h: 5, w: 12, x: 0, y: 129 });

  // Eufy Events (stat)
  const events = new StatBuilder()
    .title('Eufy Events')
    .datasource(INFLUXDB_DS)
    .unit('sishort')
    .thresholds(greenThreshold())
    .withTransformation({ id: 'labelsToFields', options: { valueLabel: 'device_name' } })
    .withTarget(influxSql('A', 'eufy_device', 'detectionStatisticsDetectedEvents'))
    .timeFrom('30d/d')
    .gridPos({ h: 5, w: 12, x: 12, y: 129 });

  return [batteryTs, batteryTemp, workingDays, events];
}
