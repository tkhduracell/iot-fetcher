import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import { PanelBuilder as GaugeBuilder } from '@grafana/grafana-foundation-sdk/gauge';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmMetric } from '../datasource.ts';
import {
  greenThreshold, greenRedThresholds, paletteColor,
  legendBottom, tooltipMulti,
  overrideDisplayAndColor,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function spaPanels(): cog.Builder<dashboard.Panel>[] {
  // Spabadet (timeseries) - value/min/max over last 24h
  const spaTs = new TimeseriesBuilder()
    .title('Spabadet')
    .datasource(VM_DS)
    .unit('celsius')
    .min(0)
    .max(45)
    .lineInterpolation('smooth' as any)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .spanNulls(SPAN_NULLS_MS)
    .withTarget(vmMetric('A', 'spa_temperature', 'value'))
    .withTarget(vmMetric('B', 'spa_temperature', 'min'))
    .withTarget(vmMetric('C', 'spa_temperature', 'max'))
    .gridPos({ h: 8, w: 12, x: 0, y: 37 });

  // Spabadet (stat) - latest temp values
  const spaStat = new StatBuilder()
    .title('Spabadet')
    .datasource(VM_DS)
    .unit('celsius')
    .thresholds(greenThreshold())
    .overrides([
      overrideDisplayAndColor('value', 'Temperatur', 'purple'),
      overrideDisplayAndColor('min', 'Min', 'blue'),
      overrideDisplayAndColor('max', 'Max', 'red'),
    ])
    .withTarget(vmMetric('A', 'spa_temperature', 'value'))
    .withTarget(vmMetric('B', 'spa_temperature', 'min'))
    .withTarget(vmMetric('C', 'spa_temperature', 'max'))
    .gridPos({ h: 8, w: 4, x: 12, y: 37 });

  // Spa Active? (gauge)
  const spaActive = new GaugeBuilder()
    .title('Spa Active?')
    .datasource(VM_DS)
    .unit('bool_on_off')
    .thresholds(greenRedThresholds(80))
    .withTarget(vmMetric('A', 'spa_mode', 'enabled'))
    .gridPos({ h: 8, w: 4, x: 16, y: 37 });

  // Spa Circulation (gauge)
  const spaCirculation = new GaugeBuilder()
    .title('Spa Circulation')
    .datasource(VM_DS)
    .unit('bool_on_off')
    .thresholds(greenThreshold())
    .withTarget(vmMetric('A', 'spa_circulation_pump', 'enabled'))
    .gridPos({ h: 8, w: 4, x: 20, y: 37 });

  return [spaTs, spaStat, spaActive, spaCirculation];
}
