import { PanelBuilder as TableBuilder } from '@grafana/grafana-foundation-sdk/table';
import { PanelBuilder as PieChartBuilder } from '@grafana/grafana-foundation-sdk/piechart';
import { PanelBuilder as StatBuilder } from '@grafana/grafana-foundation-sdk/stat';
import type * as cog from '@grafana/grafana-foundation-sdk/cog';
import type * as dashboard from '@grafana/grafana-foundation-sdk/dashboard';
import { VM_DS, vmExpr } from '../datasource.ts';
import { thresholds } from '../helpers.ts';

// watchdog pushes its metrics, so a series whose labels changed (health starting →
// healthy, update_available true → false) gets no staleness marker and lingers for
// the lookback window next to its replacement. Keep only the newest series per
// container: timestamp() is each series' last sample time, topk picks the latest.
const LATEST = 'topk by (name) (1, timestamp(watchdog_container_up))';

// watchdog_container_up also carries db/auto_update; keep only the columns a panel
// renders so the table stays tidy. Instant + table format = a current snapshot, one row per container.
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

// Colour the health column by Docker healthcheck state.
const healthMapping: dashboard.ValueMapping[] = [
  {
    type: 'value' as any,
    options: {
      healthy: { color: 'green', index: 0 },
      starting: { color: 'blue', index: 1 },
      unhealthy: { color: 'red', index: 2 },
      none: { text: '–', color: 'text', index: 3 },
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

export function watchdogPanels(): cog.Builder<dashboard.Panel>[] {
  // 📦 Inventory — every running container with image, health and update status (watchdog_container_up).
  const inventory = new TableBuilder()
    .title('📦 Bevakade containrar')
    .description('Alla körande containrar (watchdog_container_up), med image, tagg, hälsa och uppdateringsstatus.')
    .datasource(VM_DS)
    .withTarget(
      vmTable(
        'A',
        `label_keep(${LATEST}, "name", "image_name", "image_tag", "status", "health", "update_available")`,
      ),
    )
    .withTransformation({
      id: 'organize',
      options: {
        excludeByName: { Time: true, Value: true },
        indexByName: {
          name: 0,
          image_name: 1,
          image_tag: 2,
          health: 3,
          status: 4,
          update_available: 5,
        },
        renameByName: {
          name: 'Container',
          image_name: 'Image',
          image_tag: 'Tagg',
          health: 'Hälsa',
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
    .overrideByName('Hälsa', [
      { id: 'mappings', value: healthMapping },
      { id: 'custom.cellOptions', value: { type: 'color-text' } },
    ])
    .overrideByName('Status', [
      { id: 'mappings', value: statusMapping },
      { id: 'custom.cellOptions', value: { type: 'color-text' } },
    ])
    .gridPos({ h: 8, w: 12, x: 0, y: 169 });

  // 📊 Distribution of containers by healthcheck state.
  const byHealth = new PieChartBuilder()
    .title('📊 Containrar per hälsa')
    .description('Antal containrar grupperade på healthcheck-status (count by health).')
    .datasource(VM_DS)
    .pieType('donut' as any)
    .reduceOptions({ build: () => ({ calcs: ['lastNotNull'], fields: '', values: false }) } as any)
    .legend(
      { build: () => ({ displayMode: 'list', placement: 'right', showLegend: true, values: ['value'] }) } as any,
    )
    .withTarget(vmExpr('A', `count by (health) (${LATEST})`, '{{health}}').instant())
    .gridPos({ h: 8, w: 4, x: 12, y: 169 });

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
      vmExpr('A', `count by (name) (${LATEST} and on (name, update_available) watchdog_container_up{update_available="true"})`, '{{name}}').instant(),
    )
    .gridPos({ h: 8, w: 4, x: 16, y: 169 });

  // 🩺 Health restarts per container inside watchdog's rolling window (RESTART_WINDOW,
  // 24h by default). watchdog reports the in-window count itself every tick, so no
  // lookback is needed here. It caps at 3; red = cap hit, container left unhealthy.
  const restarts = new StatBuilder()
    .title('🩺 Omstarter (24h)')
    .description('Automatiska omstarter av ohälsosamma containrar senaste 24h, per namn. Max 3 – rött = taket nått.')
    .datasource(VM_DS)
    .decimals(0)
    .colorMode('background' as any)
    .reduceOptions({ build: () => ({ calcs: ['lastNotNull'], fields: '', values: false }) } as any)
    .noValue('✅ Inga')
    .thresholds(thresholds([{ color: 'orange', value: null }, { color: 'red', value: 3 }]))
    .withTarget(
      vmExpr('A', 'max by (container) (watchdog_restarts_in_window)', '{{container}}').instant(),
    )
    .gridPos({ h: 8, w: 4, x: 20, y: 169 });

  return [inventory, byHealth, updates, restarts];
}
