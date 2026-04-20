import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmExpr } from '../datasource.ts';
import {
  thresholds, greenThreshold, paletteColor,
  legendBottom, tooltipMulti,
  overrideDisplayAndColor,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function volvoPanels(): cog.Builder<dashboard.Panel>[] {
  // 🔋 Volvo XC40 Batteri (timeseries) - battery %, SoC, targets
  const batteryTs = new TimeseriesBuilder()
    .title('🔋 Volvo XC40 Batteri')
    .datasource(VM_DS)
    .unit('percent')
    .min(0)
    .max(100)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      overrideDisplayAndColor('battery', 'Batteri', 'green'),
      overrideDisplayAndColor('soc', 'SoC', 'blue'),
      overrideDisplayAndColor('target_soc', 'Mål-SoC', 'purple'),
      overrideDisplayAndColor('target_charge', 'Mål laddnivå', 'yellow'),
    ])
    .withTarget(vmExpr('A', 'last_over_time(ha_volvo_xc40_battery_value[$__interval])', 'battery'))
    .withTarget(vmExpr('B', 'last_over_time(ha_xc40_state_of_charge[$__interval])', 'soc'))
    .withTarget(vmExpr('C', 'last_over_time(ha_xc40_target_state_of_charge[$__interval])', 'target_soc'))
    .withTarget(vmExpr('D', 'last_over_time(ha_volvo_xc40_target_battery_charge_level_value[$__interval])', 'target_charge'))
    .gridPos({ h: 8, w: 12, x: 0, y: 85 });

  // ⚡ XC40 Laddeffekt (timeseries) - charging power
  const chargingTs = new TimeseriesBuilder()
    .title('⚡ XC40 Laddeffekt')
    .datasource(VM_DS)
    .unit('kwatt')
    .axisSoftMin(0)
    .colorScheme(paletteColor())
    .thresholds(greenThreshold())
    .legend(legendBottom(false))
    .tooltip(tooltipMulti())
    .insertNulls(SPAN_NULLS_MS)
    .withTarget(vmExpr('A', 'last_over_time(ha_volvo_xc40_charging_power_value[$__interval])', 'Laddeffekt'))
    .gridPos({ h: 8, w: 6, x: 12, y: 85 });

  // 🛣️ Räckvidd (stat) - distance to empty battery
  const distanceStat = new StatBuilder()
    .title('🛣️ Räckvidd')
    .datasource(VM_DS)
    .unit('lengthkm')
    .min(0)
    .max(500)
    .thresholds(thresholds([
      { color: 'red', value: null },
      { color: 'yellow', value: 50 },
      { color: 'green', value: 100 },
    ]))
    .withTarget(vmExpr('A', 'last_over_time(ha_volvo_xc40_distance_to_empty_battery_value[$__interval])', 'Räckvidd'))
    .gridPos({ h: 8, w: 3, x: 18, y: 85 });

  // 🔧 Service (stat) - time to service
  const serviceStat = new StatBuilder()
    .title('🔧 Service')
    .datasource(VM_DS)
    .unit('d')
    .min(0)
    .thresholds(thresholds([
      { color: 'red', value: null },
      { color: 'yellow', value: 30 },
      { color: 'green', value: 90 },
    ]))
    .withTarget(vmExpr('A', 'last_over_time(ha_volvo_xc40_time_to_service_value[$__interval])', 'Service'))
    .gridPos({ h: 8, w: 3, x: 21, y: 85 });

  return [batteryTs, chargingTs, distanceStat, serviceStat];
}
