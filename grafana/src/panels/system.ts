import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { MappingType } from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmExpr } from '../datasource.ts';
import {
  thresholds, paletteColor,
  legendBottom, tooltipMulti,
  SPAN_NULLS_MS,
} from '../helpers.ts';

export function systemPanels(): cog.Builder<dashboard.Panel>[] {
  // 🔒 Urladdningskontroll (timeseries, stepAfter)
  const dischargeControl = new TimeseriesBuilder()
    .title('🔒 Urladdningskontroll')
    .datasource(VM_DS)
    .interval('1m')
    .colorScheme(paletteColor())
    .legend(legendBottom())
    .tooltip(tooltipMulti())
    .lineInterpolation('stepAfter' as any)
    .min(0)
    .insertNulls(SPAN_NULLS_MS)
    .overrides([
      {
        matcher: { id: 'byRegexp', options: 'Lim.*' },
        properties: [
          { id: 'custom.axisPlacement', value: 'right' },
          { id: 'unit', value: 'watt' },
          { id: 'displayName', value: 'Gräns (W)' },
        ],
      },
    ])
    .mappings([{
      type: MappingType.ValueToText,
      options: { '4294967295': { text: 'MAX', index: 0 } },
    }])
    .withTarget(
      vmExpr('Active', 'last_over_time(sum(sigenergy_discharge_control_active[$__interval]) by ())', 'Active'),
    )
    .withTarget(
      vmExpr('Limit', 'last_over_time(sum(sigenergy_discharge_control_limit_w[$__interval]) by ())', 'Limit'),
    )
    .gridPos({ h: 8, w: 12, x: 0, y: 136 });

  // ⚡ Urladdningsgräns (stat, current discharge limit in W)
  const dischargeLimitStat = new StatBuilder()
    .title('⚡ Urladdningsgräns')
    .datasource(VM_DS)
    .unit('watt')
    .decimals(0)
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: 'red', value: 1 },
    ]))
    .withTarget(
      vmExpr('A', 'last_over_time(sum(sigenergy_ems_control_max_discharge_limit_w[$__interval]) by ())'),
    )
    .gridPos({ h: 8, w: 4, x: 12, y: 136 });

  // 💾 Senaste VM-backup (stat, age in seconds)
  const vmBackup = new StatBuilder()
    .title('💾 Senaste VM-backup')
    .datasource(VM_DS)
    .unit('dtdurations')
    .decimals(1)
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 90000 },
      { color: 'red', value: 172800 },
    ]))
    .withTarget(
      vmExpr('A', 'now() - last_over_time(vm_backup_last_success_timestamp[$__interval])'),
    )
    .gridPos({ h: 8, w: 8, x: 16, y: 136 });

  return [dischargeControl, dischargeLimitStat, vmBackup];
}
