import * as fs from 'node:fs';
import * as path from 'node:path';
import { DashboardBuilder, RowBuilder } from '@grafana/grafana-foundation-sdk/dashboard';
import { housePanels, tapoPanels } from './panels/house.ts';
import { poolPanels } from './panels/pool.ts';
import { spaPanels } from './panels/spa.ts';
import { energyPanels } from './panels/energy.ts';
import { eufyPanels } from './panels/eufy.ts';

function buildDashboard() {
  const builder = new DashboardBuilder('Irisgatan')
    .uid('irisgatan-v3')
    .description('Detaljer i Irisgatan 16')
    .tags(['hus'])
    .timezone('browser')
    .time({ from: 'now-12h', to: 'now' })
    .refresh('10s')
    .editable()
    .liveNow(true);

  // Huset row
  builder.withRow(new RowBuilder('Huset'));
  for (const panel of housePanels()) {
    builder.withPanel(panel);
  }

  // Poolen row (includes pool + spa panels)
  builder.withRow(new RowBuilder('Poolen'));
  for (const panel of poolPanels()) {
    builder.withPanel(panel);
  }
  for (const panel of spaPanels()) {
    builder.withPanel(panel);
  }

  // Energi row
  builder.withRow(new RowBuilder('Energi').gridPos({ h: 1, w: 24, x: 0, y: 45 }));
  for (const panel of energyPanels()) {
    builder.withPanel(panel);
  }

  // Eufy Cameras row (collapsed)
  const eufyRow = new RowBuilder('Eufy Cameras').collapsed(true).gridPos({ h: 1, w: 24, x: 0, y: 76 });
  for (const panel of eufyPanels()) {
    eufyRow.withPanel(panel);
  }
  builder.withRow(eufyRow);

  // Tapo row
  builder.withRow(new RowBuilder('Tapo').gridPos({ h: 1, w: 24, x: 0, y: 77 }));
  for (const panel of tapoPanels()) {
    builder.withPanel(panel);
  }

  return builder.build();
}

async function uploadToGrafana(dashboardJson: object) {
  const grafanaUrl = process.env.GRAFANA_URL;
  const grafanaToken = process.env.GRAFANA_TOKEN;

  if (!grafanaUrl || !grafanaToken) {
    console.error('GRAFANA_URL and GRAFANA_TOKEN must be set in .env to upload');
    return null;
  }

  const folderUid = process.env.GRAFANA_FOLDER_UID?.trim() || undefined;

  const payload: Record<string, unknown> = {
    dashboard: dashboardJson,
    overwrite: true,
    message: 'Updated via grafana-foundation-sdk',
  };
  if (folderUid) {
    payload.folderUid = folderUid;
  }

  const url = `${grafanaUrl.replace(/\/$/, '')}/api/dashboards/db`;
  console.log(`Uploading dashboard to ${url}...`);

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${grafanaToken}`,
    },
    body: JSON.stringify(payload),
  });

  const body = await response.text();
  if (!response.ok) {
    throw new Error(`Upload failed (${response.status}): ${body}`);
  }

  const result = JSON.parse(body);
  console.log(`Dashboard uploaded successfully!`);
  console.log(`  URL: ${grafanaUrl}/d/${result.uid}`);
  console.log(`  Version: ${result.version}`);
  return result;
}

async function main() {
  const dashboard = buildDashboard();
  const json = JSON.stringify(dashboard, null, 2);

  // Write to dist/dashboard.json
  const distDir = path.join(import.meta.dirname, '..', 'dist');
  fs.mkdirSync(distDir, { recursive: true });
  const outPath = path.join(distDir, 'dashboard.json');
  fs.writeFileSync(outPath, json, 'utf-8');
  console.log(`Dashboard JSON written to ${outPath}`);

  // Upload unless skip is set
  if (!process.env.GRAFANA_SKIP_UPLOAD) {
    await uploadToGrafana(dashboard);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
