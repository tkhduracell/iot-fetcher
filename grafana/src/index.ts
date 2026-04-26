import * as fs from 'node:fs';
import * as path from 'node:path';
import { buildDashboard } from './dashboard.ts';

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
