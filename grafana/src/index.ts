import * as fs from 'node:fs';
import * as path from 'node:path';
import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { buildDashboard } from './dashboard.ts';
import { buildAlerts } from './alerts/index.ts';

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

async function uploadAlertGroups(groups: alerting.RuleGroup[]) {
  const grafanaUrl = process.env.GRAFANA_URL;
  const grafanaToken = process.env.GRAFANA_TOKEN;

  if (!grafanaUrl || !grafanaToken) {
    console.error('GRAFANA_URL and GRAFANA_TOKEN must be set in .env to upload alerts');
    return;
  }

  const base = grafanaUrl.replace(/\/$/, '');
  for (const group of groups) {
    const folderUid = group.folderUid;
    const groupName = group.title;
    if (!folderUid || !groupName) {
      console.warn('Skipping alert group with missing folderUid/title:', group);
      continue;
    }
    const url = `${base}/api/v1/provisioning/folder/${folderUid}/rule-groups/${encodeURIComponent(groupName)}`;
    console.log(`Uploading ${group.rules?.length ?? 0} rule(s) to ${url}...`);

    const response = await fetch(url, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${grafanaToken}`,
        // Keep rules editable in the UI for ad-hoc debugging; code remains
        // source of truth because the next deploy overwrites.
        'X-Disable-Provenance': 'true',
      },
      body: JSON.stringify(group),
    });
    const body = await response.text();
    if (!response.ok) {
      throw new Error(`Alert upload failed (${response.status}) for group "${groupName}": ${body}`);
    }
    console.log(`Alert group "${groupName}" uploaded.`);
  }
}

async function main() {
  const distDir = path.join(import.meta.dirname, '..', 'dist');
  fs.mkdirSync(distDir, { recursive: true });

  const dashboard = buildDashboard();
  const dashboardJson = JSON.stringify(dashboard, null, 2);
  const dashboardPath = path.join(distDir, 'dashboard.json');
  fs.writeFileSync(dashboardPath, dashboardJson, 'utf-8');
  console.log(`Dashboard JSON written to ${dashboardPath}`);

  const folderUid = process.env.GRAFANA_FOLDER_UID?.trim() || undefined;
  const alertGroups = buildAlerts(folderUid);
  const alertsPath = path.join(distDir, 'alerts.json');
  fs.writeFileSync(alertsPath, JSON.stringify(alertGroups, null, 2), 'utf-8');
  console.log(`Alerts JSON written to ${alertsPath}`);

  if (!process.env.GRAFANA_SKIP_UPLOAD) {
    await uploadToGrafana(dashboard);
    await uploadAlertGroups(alertGroups);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
