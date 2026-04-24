import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmExpr } from '../datasource.ts';
import {
  thresholds,
} from '../helpers.ts';

export function systemPanels(): cog.Builder<dashboard.Panel>[] {
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
    .gridPos({ h: 8, w: 8, x: 0, y: 120 });

  return [vmBackup];
}
