#!/usr/bin/env node
// Idempotent Home Assistant provisioning for the dashboard Roborock dialog.
// Creates the roborock_floor / roborock_room labels, creates the two missing
// whole-floor automations, and assigns labels to every Städa automation.
//
// Usage:
//   HOMEASSISTANT_URL=... HOMEASSISTANT_TOKEN=... node scripts/roborock-ha-provision.mjs [--dry-run]

const URL_BASE = (process.env.HOMEASSISTANT_URL || '').replace(/\/$/, '');
const TOKEN = process.env.HOMEASSISTANT_TOKEN;
const DRY_RUN = process.argv.includes('--dry-run');

if (!URL_BASE || !TOKEN) {
  console.error('HOMEASSISTANT_URL and HOMEASSISTANT_TOKEN must be set');
  process.exit(1);
}

const VACUUM = 'vacuum.roborock_s6_maxv';
const MAP_SELECT = 'select.roborock_s6_maxv_selected_map';
const FLOOR_LABEL = 'roborock_floor';
const ROOM_LABEL = 'roborock_room';

// Automations to create if absent. Fixed ids keep this idempotent.
const NEW_AUTOMATIONS = [
  { id: '1900000000001', alias: 'Städa Vån0', map: 'Floor 0' },
  { id: '1900000000002', alias: 'Städa Annexet', map: 'Annexet' },
];

const FLOOR_ALIASES = ['Städa Vån1', 'Städa Vån0', 'Städa Uterum', 'Städa Annexet'];
const ROOM_ALIASES = [
  'Städa Köket', 'Städa Kontoret', 'Städa Vardagsrummet',
  'Städa Matrummet', 'Städa Lobbyn',
];

function automationBody({ alias, map }) {
  return {
    alias,
    description: `Selects the ${map} map and starts a full clean.`,
    triggers: [],
    conditions: [],
    actions: [
      { action: 'roborock.get_maps', metadata: {}, data: {}, response_variable: 'maps', target: { entity_id: VACUUM } },
      { action: 'select.select_option', metadata: {}, target: { entity_id: MAP_SELECT }, data: { option: map } },
      { action: 'vacuum.start', metadata: {}, target: { entity_id: VACUUM }, data: {} },
    ],
    mode: 'single',
  };
}

async function rest(path, init = {}) {
  const resp = await fetch(`${URL_BASE}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
  });
  if (!resp.ok) throw new Error(`HA ${path} -> ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

function connect() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${URL_BASE.replace(/^http/, 'ws')}/api/websocket`);
    const pending = new Map();
    let id = 0;
    ws.onerror = () => reject(new Error('HA websocket error'));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'auth_required') return ws.send(JSON.stringify({ type: 'auth', access_token: TOKEN }));
      if (m.type === 'auth_invalid') return reject(new Error('HA websocket auth failed'));
      if (m.type === 'auth_ok') {
        return resolve({
          send: (msg) => new Promise((res, rej) => {
            const mid = ++id;
            pending.set(mid, { res, rej });
            ws.send(JSON.stringify({ id: mid, ...msg }));
          }),
          close: () => ws.close(),
        });
      }
      if (m.type === 'result') {
        const p = pending.get(m.id);
        if (!p) return;
        pending.delete(m.id);
        if (m.success) p.res(m.result);
        else p.rej(new Error(JSON.stringify(m.error)));
      }
    };
  });
}

// Map friendly_name -> entity_id for every automation currently in HA.
async function automationsByAlias() {
  const states = await rest('/api/states');
  const out = new Map();
  for (const s of states) {
    if (s.entity_id.startsWith('automation.')) out.set(s.attributes.friendly_name, s.entity_id);
  }
  return out;
}

async function main() {
  const ha = await connect();
  try {
    // 1. Labels
    const labels = await ha.send({ type: 'config/label_registry/list' });
    const existing = new Set(labels.map((l) => l.label_id));
    for (const [labelId, name, icon] of [
      [FLOOR_LABEL, 'Roborock floor', 'mdi:home-floor-1'],
      [ROOM_LABEL, 'Roborock room', 'mdi:floor-plan'],
    ]) {
      if (existing.has(labelId)) {
        console.log(`label ${labelId}: already exists`);
        continue;
      }
      console.log(`label ${labelId}: CREATE`);
      if (!DRY_RUN) await ha.send({ type: 'config/label_registry/create', name, icon });
    }

    // 2. Missing floor automations
    let known = await automationsByAlias();
    let created = false;
    for (const spec of NEW_AUTOMATIONS) {
      if (known.has(spec.alias)) {
        console.log(`automation ${spec.alias}: already exists`);
        continue;
      }
      console.log(`automation ${spec.alias}: CREATE (map ${spec.map})`);
      if (!DRY_RUN) {
        await rest(`/api/config/automation/config/${spec.id}`, {
          method: 'POST',
          body: JSON.stringify(automationBody(spec)),
        });
        created = true;
      }
    }
    if (created) {
      // Give HA a moment to reload automations and register the new entities.
      await new Promise((r) => setTimeout(r, 3000));
      known = await automationsByAlias();
    }

    // 3. Label assignment, merging with any labels already present
    const registry = await ha.send({ type: 'config/entity_registry/list' });
    const byEntity = new Map(registry.map((e) => [e.entity_id, e]));

    for (const [aliases, label] of [[FLOOR_ALIASES, FLOOR_LABEL], [ROOM_ALIASES, ROOM_LABEL]]) {
      for (const alias of aliases) {
        const entityId = known.get(alias);
        if (!entityId) {
          console.warn(`WARN ${alias}: no automation found, skipping`);
          continue;
        }
        const current = byEntity.get(entityId)?.labels ?? [];
        if (current.includes(label)) {
          console.log(`${entityId}: already labelled ${label}`);
          continue;
        }
        console.log(`${entityId}: ADD label ${label}`);
        if (!DRY_RUN) {
          await ha.send({
            type: 'config/entity_registry/update',
            entity_id: entityId,
            labels: [...current, label],
          });
        }
      }
    }
    console.log(DRY_RUN ? '\nDry run complete, nothing changed.' : '\nProvisioning complete.');
  } finally {
    ha.close();
  }
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
