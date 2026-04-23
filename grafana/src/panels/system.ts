import { PanelBuilder as TimeseriesBuilder } from '@grafana/grafana-foundation-sdk/timeseries';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
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
    .withTarget(
      vmExpr('Active', 'last_over_time(sigenergy_discharge_control_active[$__interval])', 'Active'),
    )
    .withTarget(
      vmExpr('Limit', 'last_over_time(sigenergy_discharge_control_limit_w[$__interval])', 'Limit'),
    )
    .gridPos({ h: 8, w: 16, x: 0, y: 112 });

  // 💾 Senaste VM-backup (stat, age in seconds)
  const vmBackup = new StatBuilder()
    .title('💾 Senaste VM-backup')
    .datasource(VM_DS)
    .unit('dtdurations')
    .thresholds(thresholds([
      { color: 'green', value: null },
      { color: '#EAB839', value: 90000 },
      { color: 'red', value: 172800 },
    ]))
    .withTarget(
      vmExpr('A', 'time() - last_over_time(vm_backup_last_success_timestamp[$__interval])'),
    )
    .gridPos({ h: 8, w: 8, x: 16, y: 112 });

  return [dischargeControl, vmBackup];
}
