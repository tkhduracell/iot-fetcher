import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmMetric, vmExpr } from '../datasource.ts';
import {
  greenRedThresholds, greenThreshold, paletteColor, fixedColor,
  legendBottom, tooltipMulti, tooltipSingle,
  overrideDisplayAndColor, overrideDisplayName,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function housePanels(): cog.Builder<dashboard.Panel>[] {
  // Ngenic Innegivare - Temperatur (timeseries, two fields split into two targets)
  const indoorTemp = new TimeseriesBuilder()
    .title('Ngenic Innegivare - Temperatur')
    .datasource(VM_DS)
    .interval('1m')
    .colorScheme(paletteColor())
    .thresholds(greenRedThresholds(80))
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .spanNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('temperature_C', 'Temperatur', 'light-green'),
      overrideDisplayAndColor('target_temperature_C', 'Måltemperatur', 'purple'),
    ])
    .withTarget(
      vmMetric('A', 'ngenic_node_sensor_measurement_value', 'temperature_C', {
        where: `"node_type" = 'SENSOR'`,
      }),
    )
    .withTarget(
      vmMetric('B', 'ngenic_node_sensor_measurement_value', 'target_temperature_C', {
        where: `"node_type" = 'SENSOR'`,
      }),
    )
    .gridPos({ h: 7, w: 8, x: 0, y: 1 });

  // Ngenic Inomhus (stat)
  const indoorStat = new StatBuilder()
    .title('')
    .datasource(VM_DS)
    .unit('celsius')
    .colorScheme(fixedColor('purple'))
    .thresholds(greenRedThresholds(80))
    .overrides([overrideDisplayName('temperature_C', 'Innetemperatur')])
    .withTarget(
      vmMetric('B', 'ngenic_node_sensor_measurement_value', 'temperature_C', {
        where: `"node_type" = 'SENSOR' AND "node" = 'a84f4c8f-47c5-465d-878e-957c0affb60b'`,
      }),
    )
    .timeFrom('30m')
    .gridPos({ h: 7, w: 4, x: 8, y: 1 });

  // Ngenic Utetemp (timeseries)
  const outdoorTemp = new TimeseriesBuilder()
    .title('Ngenic Utetemp')
    .datasource(VM_DS)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .spanNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('temperature_C', 'Utomhustemperatur', 'blue'),
    ])
    .withTarget(
      vmMetric('A', 'ngenic_node_sensor_measurement_value', 'temperature_C', {
        where: `"node_type" = 'CONTROLLER'`,
      }),
    )
    .gridPos({ h: 7, w: 8, x: 12, y: 1 });

  // Ngenic Utomhus (stat)
  const outdoorStat = new StatBuilder()
    .title('')
    .datasource(VM_DS)
    .unit('celsius')
    .colorScheme(fixedColor('purple'))
    .thresholds(greenRedThresholds(80))
    .overrides([overrideDisplayName('temperature_C', 'Utetemperatur')])
    .withTarget(
      vmMetric('B', 'ngenic_node_sensor_measurement_value', 'temperature_C', {
        where: `"node_type" = 'CONTROLLER' AND "node" = 'efc2897b-d9d3-41dd-81c6-b376d4bd4996'`,
      }),
    )
    .timeFrom('30m')
    .gridPos({ h: 7, w: 4, x: 20, y: 1 });

  // Ngenic Innegivare - Relativ Luftfuktighet (timeseries)
  const humidity = new TimeseriesBuilder()
    .title('Ngenic Innegivare - Relativ Luftfuktighet')
    .datasource(VM_DS)
    .unit('humidity')
    .axisSoftMin(40)
    .axisSoftMax(50)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .spanNulls(SPAN_NULLS_MS)
    .overrides([overrideDisplayName('humidity_relative_percent', 'Relativ fuktighet')])
    .withTarget(
      vmMetric('A', 'ngenic_node_sensor_measurement_value', 'humidity_relative_percent', {
        where: `"node_type" = 'SENSOR'`,
      }),
    )
    .gridPos({ h: 7, w: 12, x: 0, y: 8 });

  // AQI - Luftkvalitét (timeseries, 7d range)
  const aqi = new TimeseriesBuilder()
    .title('AQI - Luftkvalitét')
    .datasource(VM_DS)
    .min(0)
    .max(100)
    .interval('1h')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .overrides([overrideDisplayName('aqi', 'Luftkvalitet (AQI)')])
    .withTarget(
      vmMetric('A', 'air_quality', 'aqi', { agg: 'LAST_VALUE' }),
    )
    .timeFrom('7d/d')
    .gridPos({ h: 7, w: 12, x: 12, y: 8 });

  // Ngenic Batteri (timeseries, 30d) - aggregated by node type
  const sensorBattery = new TimeseriesBuilder()
    .title('Ngenic Batteri')
    .datasource(VM_DS)
    .unit('percent')
    .min(0)
    .max(100)
    .interval('1h')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayName('SENSOR', 'Innegivare'),
      overrideDisplayName('CONTROLLER', 'Styrenhet'),
    ])
    .withTarget(
      vmExpr('A', 'avg by (node_type) (avg_over_time(ngenic_node_battery_value[$__interval]))', '{{node_type}}'),
    )
    .timeFrom('30d/d')
    .gridPos({ h: 7, w: 12, x: 0, y: 15 });

  // Ngenic Radiosignal (timeseries, 30d) - aggregated by node type
  const sensorSignal = new TimeseriesBuilder()
    .title('Ngenic Radiosignal')
    .datasource(VM_DS)
    .unit('percent')
    .min(0)
    .max(100)
    .interval('1h')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .spanNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayName('SENSOR', 'Innegivare'),
      overrideDisplayName('CONTROLLER', 'Styrenhet'),
    ])
    .withTarget(
      vmExpr('A', 'avg by (node_type) (avg_over_time(ngenic_node_radio_signal_value[$__interval]))', '{{node_type}}'),
    )
    .timeFrom('30d/d')
    .gridPos({ h: 7, w: 12, x: 12, y: 15 });

  return [indoorTemp, indoorStat, outdoorTemp, outdoorStat, humidity, aqi, sensorBattery, sensorSignal];
}

export function tapoPanels(): cog.Builder<dashboard.Panel>[] {
  // Tapo Enheter Online (timeseries, 7d)
  const tapoOnline = new TimeseriesBuilder()
    .title('Tapo Enheter Online')
    .datasource(VM_DS)
    .min(0)
    .interval('5m')
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(
      vmExpr('A', 'last_over_time(tapo_cloud_device_device_count[$__interval])', '{{device_alias}}'),
    )
    .timeFrom('7d/d')
    .gridPos({ h: 7, w: 12, x: 0, y: 78 });

  return [tapoOnline];
}
