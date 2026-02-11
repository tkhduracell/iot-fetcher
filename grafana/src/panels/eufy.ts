import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';

import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmMetric } from '../datasource.ts';
import {
  thresholds, greenThreshold, paletteColor,
  legendBottom, tooltipSingle,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function eufyPanels(): cog.Builder<dashboard.Panel>[] {
  // Eufy Battery (timeseries + labelsToFields)
  const batteryTs = new TimeseriesBuilder()
    .title('Eufy Battery')
    .datasource(VM_DS)
    .unit('percent')
    .max(100)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .withTransformation({ id: 'labelsToFields', options: { valueLabel: 'device_name' } })
    .withTarget(vmMetric('A', 'eufy_device', 'battery'))
    .timeFrom('7d/d')
    .gridPos({ h: 8, w: 8, x: 0, y: 77 });

  // Eufy Battery Temperature (timeseries + labelsToFields)
  const batteryTemp = new TimeseriesBuilder()
    .title('Eufy Battery Temperature')
    .datasource(VM_DS)
    .unit('celsius')
    .min(0)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .withTransformation({ id: 'labelsToFields', options: { valueLabel: 'device_name' } })
    .withTarget(vmMetric('A', 'eufy_device', 'batteryTemperature'))
    .timeFrom('7d/d')
    .gridPos({ h: 8, w: 8, x: 8, y: 77 });

  // Eufy WiFi Signal (timeseries, dBm)
  const wifiRssi = new TimeseriesBuilder()
    .title('Eufy WiFi Signal')
    .datasource(VM_DS)
    .unit('dBm')
    .max(0)
    .colorScheme(paletteColor())
    .thresholds(thresholds([
      { color: 'red', value: null },
      { color: 'yellow', value: -80 },
      { color: 'green', value: -60 },
    ]))
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .withTransformation({ id: 'labelsToFields', options: { valueLabel: 'device_name' } })
    .withTarget(vmMetric('A', 'eufy_device', 'wifiRssi'))
    .timeFrom('7d/d')
    .gridPos({ h: 8, w: 8, x: 16, y: 77 });

  return [batteryTs, batteryTemp, wifiRssi];
}
