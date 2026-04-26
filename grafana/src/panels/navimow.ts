import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmExpr } from '../datasource.ts';
import {
  greenThreshold, paletteColor,
  legendBottom, tooltipSingle,
  overrideDisplayAndColor,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function navimowPanels(): cog.Builder<dashboard.Panel>[] {
  // Navimow i206 AWD Battery (timeseries)
  const batteryTs = new TimeseriesBuilder()
    .title('Navimow 🟢')
    .datasource(VM_DS)
    .unit('percent')
    .min(0)
    .max(100)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipSingle())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('Navimow i206 AWD Battery', 'Navimow i206 AWD Battery', 'green'),
    ])
    .withTarget(vmExpr('A', 'last_over_time(ha_navimow_i206_awd_battery_value[$__interval])', '{{friendly_name}}'))
    .gridPos({ h: 8, w: 24, x: 0, y: 110 });

  return [batteryTs];
}
