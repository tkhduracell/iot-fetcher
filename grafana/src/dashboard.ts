import { DashboardBuilder, RowBuilder, TimePickerBuilder, TimeOptionBuilder } from '@grafana/grafana-foundation-sdk/dashboard';
import { housePanels, tapoPanels } from './panels/house.ts';
import { poolPanels } from './panels/pool.ts';
import { spaPanels } from './panels/spa.ts';
import { energyPanels } from './panels/energy.ts';
import { eufyPanels } from './panels/eufy.ts';
import { lightingPanels } from './panels/lighting.ts';
import { navimowPanels } from './panels/navimow.ts';
import { volvoPanels } from './panels/volvo.ts';
import { systemPanels } from './panels/system.ts';

export function buildDashboard() {
  const builder = new DashboardBuilder('Irisgatan')
    .uid('irisgatan-v3')
    .description('Detaljer i Irisgatan 16')
    .tags(['hus'])
    .timezone('browser')
    .time({ from: 'now-12h', to: 'now' })
    .refresh('10s')
    .editable()
    .liveNow(true)
    .timepicker(
      new TimePickerBuilder().quickRanges([
        new TimeOptionBuilder().display('2h').from('now-2h').to('now'),
        new TimeOptionBuilder().display('6h').from('now-6h').to('now'),
        new TimeOptionBuilder().display('12h').from('now-12h').to('now'),
        new TimeOptionBuilder().display('24h').from('now-24h').to('now'),
        new TimeOptionBuilder().display('48h').from('now-48h').to('now'),
        new TimeOptionBuilder().display('7 days').from('now-7d').to('now'),
        new TimeOptionBuilder().display('14 days').from('now-14d').to('now'),
        new TimeOptionBuilder().display('30 days').from('now-30d').to('now'),
        new TimeOptionBuilder().display('90 days').from('now-90d').to('now'),
      ])
    );

  // Huset row
  builder.withRow(new RowBuilder('Huset'));
  for (const panel of housePanels()) {
    builder.withPanel(panel);
  }

  // Belysning row (collapsed)
  const belysningRow = new RowBuilder('Belysning').collapsed(true).gridPos({ h: 1, w: 24, x: 0, y: 15 });
  for (const panel of lightingPanels()) {
    belysningRow.withPanel(panel);
  }
  builder.withRow(belysningRow);

  // Poolen row
  builder.withRow(new RowBuilder('Poolen').gridPos({ h: 1, w: 24, x: 0, y: 23 }));
  for (const panel of poolPanels()) {
    builder.withPanel(panel);
  }

  // Spabadet row
  builder.withRow(new RowBuilder('Spabadet').gridPos({ h: 1, w: 24, x: 0, y: 60 }));
  for (const panel of spaPanels()) {
    builder.withPanel(panel);
  }

  // Energi row
  builder.withRow(new RowBuilder('Energi').gridPos({ h: 1, w: 24, x: 0, y: 69 }));
  for (const panel of energyPanels()) {
    builder.withPanel(panel);
  }

  // Volvo XC40 row
  builder.withRow(new RowBuilder('Volvo XC40').gridPos({ h: 1, w: 24, x: 0, y: 100 }));
  for (const panel of volvoPanels()) {
    builder.withPanel(panel);
  }

  // Navimow row
  builder.withRow(new RowBuilder('Navimow').gridPos({ h: 1, w: 24, x: 0, y: 109 }));
  for (const panel of navimowPanels()) {
    builder.withPanel(panel);
  }

  // Eufy Cameras row (collapsed)
  const eufyRow = new RowBuilder('Eufy Cameras').collapsed(true).gridPos({ h: 1, w: 24, x: 0, y: 118 });
  for (const panel of eufyPanels()) {
    eufyRow.withPanel(panel);
  }
  builder.withRow(eufyRow);

  // Tapo row
  builder.withRow(new RowBuilder('Tapo').gridPos({ h: 1, w: 24, x: 0, y: 127 }));
  for (const panel of tapoPanels()) {
    builder.withPanel(panel);
  }

  // System row (meta metrics)
  builder.withRow(new RowBuilder('System').gridPos({ h: 1, w: 24, x: 0, y: 135 }));
  for (const panel of systemPanels()) {
    builder.withPanel(panel);
  }

  return builder.build();
}
