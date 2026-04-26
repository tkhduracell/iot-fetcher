import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import { PanelBuilder as GaugeBuilder } from '@grafana/grafana-foundation-sdk/gauge';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmMetric, vmExpr } from '../datasource.ts';
import {
  greenThreshold, greenRedThresholds, paletteColor,
  legendBottom, tooltipMulti,
  overrideDisplayAndColor, overrideDisplayName,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function spaPanels(): cog.Builder<dashboard.Panel>[] {
  // Spabadet (timeseries) - current temperature from HA climate entity
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
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('current_temperature_value', 'Temperatur', 'light-green'),
      overrideDisplayAndColor('state_text', 'Temp Range', 'yellow'),
    ])
    .withTarget(vmMetric('A', 'spa_climate', 'current_temperature_value'))
    .withTarget(vmExpr('B', 'last_over_time(spa_temperature_range_state_text[$__interval])', 'state_text'))
    .gridPos({ h: 8, w: 12, x: 0, y: 61 });

  // Spabadet (stat) - latest temp
  const spaStat = new StatBuilder()
    .title('Spabadet')
    .datasource(VM_DS)
    .unit('celsius')
    .thresholds(greenThreshold())
    .overrides([
      overrideDisplayAndColor('current_temperature_value', 'Temperatur', 'purple'),
    ])
    .withTarget(vmMetric('A', 'spa_climate', 'current_temperature_value'))
    .gridPos({ h: 8, w: 4, x: 12, y: 61 });

  // Spa Circulation (gauge)
  const spaCirculation = new GaugeBuilder()
    .title('Spa Circulation')
    .datasource(VM_DS)
    .unit('bool_on_off')
    .thresholds(greenThreshold())
    .overrides([
      overrideDisplayName('circulation', 'Circulation'),
      overrideDisplayName('spa_pump_1_value', 'Jet 1'),
      overrideDisplayName('spa_pump_2_value', 'Jet 2'),
    ])
    .withTarget(vmExpr('A', 'last_over_time(spa_circulation_pump_value[$__interval])', 'circulation'))
    .withTarget(vmMetric('B', 'spa_pump_1', 'value'))
    .withTarget(vmMetric('C', 'spa_pump_2', 'value'))
    .gridPos({ h: 8, w: 4, x: 16, y: 61 });

  return [spaTs, spaStat, spaCirculation];
}
