import { PanelBuilder as TableBuilder } from '@grafana/grafana-foundation-sdk/table';
import { PanelBuilder as PieChartBuilder } from '@grafana/grafana-foundation-sdk/piechart';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmExpr } from '../datasource.ts';
import { thresholds } from '../helpers.ts';

// wud_containers carries ~30 labels; keep only the columns a panel renders so the
// table stays tidy. Instant + table format = a current snapshot, one row per container.
function vmTable(refId: string, expr: string): cog.Builder<cog.Dataquery> {
  return {
    build: () => ({
      refId,
      datasource: VM_DS,
      expr,
      instant: true,
      range: false,
      format: 'table',
      _implementsDataqueryVariant() {},
    }),
  } as unknown as cog.Builder<cog.Dataquery>;
}

// Colour update_available cells red (update pending) / green (current).
const updateMapping: dashboard.ValueMapping[] = [
  {
    type: 'value' as any,
    options: {
      true: { text: 'Ja ⬆️', color: 'red', index: 0 },
      false: { text: 'Nej', color: 'green', index: 1 },
    },
  },
];

// Colour the status column by Docker container state.
const statusMapping: dashboard.ValueMapping[] = [
  {
    type: 'value' as any,
    options: {
      running: { color: 'green', index: 0 },
      restarting: { color: 'orange', index: 1 },
      paused: { color: 'yellow', index: 2 },
      exited: { color: 'red', index: 3 },
      created: { color: 'blue', index: 4 },
    },
  },
];

export function wudPanels(): cog.Builder<dashboard.Panel>[] {
  // 📦 Inventory — every watched container with its key image labels (wud_containers).
  const inventory = new TableBuilder()
    .title('📦 Bevakade containrar')
    .description('Alla containrar WUD bevakar (wud_containers), med image, tagg och uppdateringsstatus.')
    .datasource(VM_DS)
    .withTarget(
      vmTable(
        'A',
        'label_keep(wud_containers, "name", "image_name", "image_tag_value", "image_architecture", "status", "update_available")',
      ),
    )
    .withTransformation({
      id: 'organize',
      options: {
        excludeByName: { Time: true, Value: true },
        indexByName: {
          name: 0,
          image_name: 1,
          image_tag_value: 2,
          image_architecture: 3,
          status: 4,
          update_available: 5,
        },
        renameByName: {
          name: 'Container',
          image_name: 'Image',
          image_tag_value: 'Tagg',
          image_architecture: 'Arch',
          status: 'Status',
          update_available: 'Uppdatering',
        },
      },
    })
    .withTransformation({ id: 'sortBy', options: { sort: [{ field: 'Container' }] } })
    .filterable(true)
    .overrideByName('Uppdatering', [
      { id: 'mappings', value: updateMapping },
      { id: 'custom.cellOptions', value: { type: 'color-background', mode: 'basic' } },
    ])
    .overrideByName('Status', [
      { id: 'mappings', value: statusMapping },
      { id: 'custom.cellOptions', value: { type: 'color-text' } },
    ])
    .gridPos({ h: 8, w: 12, x: 0, y: 161 });

  // 📊 Distribution of watched containers by Docker status.
  const byStatus = new PieChartBuilder()
    .title('📊 Containrar per status')
    .description('Antal bevakade containrar grupperade på status (count by status).')
    .datasource(VM_DS)
    .pieType('donut' as any)
    .reduceOptions({ build: () => ({ calcs: ['lastNotNull'], fields: '', values: false }) } as any)
    .legend(
      { build: () => ({ displayMode: 'list', placement: 'right', showLegend: true, values: ['value'] }) } as any,
    )
    .withTarget(vmExpr('A', 'count by (status) (wud_containers)', '{{status}}').instant())
    .gridPos({ h: 8, w: 6, x: 12, y: 161 });

  // 🔄 Containers with an image update available, per name. Empty = everything current.
  const updates = new StatBuilder()
    .title('🔄 Uppdatering tillgänglig')
    .description('Containrar med ny image tillgänglig (update_available="true"), per namn. Tomt = allt aktuellt.')
    .datasource(VM_DS)
    .decimals(0)
    .colorMode('background' as any)
    .reduceOptions({ build: () => ({ calcs: ['lastNotNull'], fields: '', values: false }) } as any)
    .noValue('✅ Alla aktuella')
    .thresholds(thresholds([{ color: 'red', value: null }]))
    .withTarget(
      vmExpr('A', 'count by (name) (wud_containers{update_available="true"})', '{{name}}').instant(),
    )
    .gridPos({ h: 8, w: 6, x: 18, y: 161 });

  return [inventory, byStatus, updates];
}
