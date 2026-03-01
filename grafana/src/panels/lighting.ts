import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmExpr } from '../datasource.ts';
import {
  greenThreshold, paletteColor,
  legendBottom, tooltipMulti,
  overrideDisplayAndColor,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function lightingPanels(): cog.Builder<dashboard.Panel>[] {
  // Belysning status (on/off)
  const statusTs = new TimeseriesBuilder()
    .title('Belysning')
    .datasource(VM_DS)
    .unit('bool_on_off')
    .min(0)
    .max(1)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('pool_water_lights', 'Poolbelysning', 'blue'),
      overrideDisplayAndColor('pool_area_lights', 'Staket', 'green'),
      overrideDisplayAndColor('terrace_lightning', 'Terrass', 'yellow'),
      overrideDisplayAndColor('facade_lighting', 'Fasad', 'purple'),
    ])
    .withTarget(vmExpr('A', 'last_over_time(pool_water_lights_value[$__interval])', 'pool_water_lights'))
    .withTarget(vmExpr('B', 'last_over_time(pool_area_lights_value[$__interval])', 'pool_area_lights'))
    .withTarget(vmExpr('C', 'last_over_time(terrace_lightning_value[$__interval])', 'terrace_lightning'))
    .withTarget(vmExpr('D', 'last_over_time(facade_lighting_value[$__interval])', 'facade_lighting'))
    .gridPos({ h: 7, w: 12, x: 0, y: 23 });

  // Belysning effekt (power)
  const powerTs = new TimeseriesBuilder()
    .title('Belysning - Effekt')
    .datasource(VM_DS)
    .unit('watt')
    .min(0)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('pool_water_lights_power', 'Poolbelysning', 'blue'),
      overrideDisplayAndColor('pool_area_lights_power', 'Staket', 'green'),
      overrideDisplayAndColor('terrace_lightning_power', 'Terrass', 'yellow'),
      overrideDisplayAndColor('facade_lighting_power', 'Fasad', 'purple'),
    ])
    .withTarget(vmExpr('A', 'avg_over_time(pool_water_lights_power_value[$__interval])', 'pool_water_lights_power'))
    .withTarget(vmExpr('B', 'avg_over_time(pool_area_lights_power_value[$__interval])', 'pool_area_lights_power'))
    .withTarget(vmExpr('C', 'avg_over_time(terrace_lightning_power_value[$__interval])', 'terrace_lightning_power'))
    .withTarget(vmExpr('D', 'avg_over_time(facade_lighting_power_value[$__interval])', 'facade_lighting_power'))
    .gridPos({ h: 7, w: 12, x: 12, y: 23 });

  return [statusTs, powerTs];
}
