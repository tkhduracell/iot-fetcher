# Roborock Clean Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead Roborock button on the dashboard with a dialog that lists whole-floor cleans on top and individual rooms below, driven by Home Assistant automations discovered via HA labels.

**Architecture:** Home Assistant already holds every cleaning automation. We label them (`roborock_floor` / `roborock_room`) via a one-off provisioning script, then the Next.js web-ui reads those labels over HA's REST template API from server-side route handlers, so the HA token never reaches the browser. Pure parsing logic lives in `app/lib/roborock.ts` and is unit-tested; route handlers stay thin.

**Tech Stack:** Next.js 15 (App Router), React 18, TypeScript, Tailwind v4, vitest. Home Assistant REST + WebSocket APIs.

## Global Constraints

- Working directory for all `npm` commands: `fetcher-core/webui`.
- Test runner is **vitest**; tests are co-located as `*.test.ts` next to the module. Run with `npm test`.
- Existing code uses **relative imports** (`../lib/types`), not the `@/*` alias. Match that.
- `HOMEASSISTANT_TOKEN` must **never** be referenced from a `'use client'` component or reach the browser. All HA calls happen in route handlers under `app/api/`.
- Home Assistant base URL: `http://192.168.68.87:8123`.
- The vacuum entity is `vacuum.roborock_s6_maxv`; the map selector is `select.roborock_s6_maxv_selected_map`.
- Commit with `git commit -m "..." -m "..."`. Never use `$()` or heredoc syntax in commit commands.
- UI chrome text is English (matching the existing `Speakers` / `Reload` buttons); room and floor names come from HA and stay Swedish.
- Do **not** modify the room automations' existing ElevenLabs TTS actions. Triggering from the dashboard will announce, and that is intended.

## Verified HA facts

These were confirmed against the live HA on 2026-07-25 — build against them, don't re-derive.

Map options on `select.roborock_s6_maxv_selected_map`: `Floor 1`, `Floor 0`, `Uterummet`, `Annexet`.

Existing automations:

| Alias | Map | Action |
|---|---|---|
| `Städa Vån1` | Floor 1 | `vacuum.start` |
| `Städa Uterum` | Uterummet | `vacuum.start` |
| `Städa Köket` | Floor 1 | `app_segment_clean [21]` |
| `Städa Kontoret` | Floor 1 | `app_segment_clean [16]` |
| `Städa Vardagsrummet` | Floor 1 | `app_segment_clean [19, 20]` |
| `Städa Matrummet` | Floor 1 | `app_segment_clean [22]` |
| `Städa Lobbyn` | Floor 1 | `app_segment_clean [23]` |
| `Städa` | *(none)* | full clean — **stays unlabelled, excluded from the dialog** |

The targets template returns exactly:

```json
{"floors": [{"entity_id": "automation.stada_van1", "name": "Städa Vån1"}], "rooms": []}
```

The status template returns exactly (all values are strings):

```json
{"battery": "100", "error": "none", "room": "Office", "state": "docked", "status": "charging"}
```

`config/entity_registry/update` **replaces** the `labels` array rather than appending, and these automations already carry `area_id` (e.g. `stada_koket` → `kok`) which must not be disturbed.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/roborock-ha-provision.mjs` | One-off, idempotent HA provisioning: labels + two floor automations |
| `fetcher-core/webui/app/lib/roborock.ts` | Pure: templates, parsing, name stripping, allow-list check |
| `fetcher-core/webui/app/lib/roborock.test.ts` | Unit tests for the above |
| `fetcher-core/webui/app/lib/haClient.ts` | Server-only HA REST helpers (config, template, service call) |
| `fetcher-core/webui/app/api/roborock/targets/route.ts` | `GET` labelled floors + rooms |
| `fetcher-core/webui/app/api/roborock/status/route.ts` | `GET` vacuum status |
| `fetcher-core/webui/app/api/roborock/trigger/route.ts` | `POST` trigger an allow-listed automation |
| `fetcher-core/webui/app/api/roborock/dock/route.ts` | `POST` return to dock |
| `fetcher-core/webui/app/hooks/useRoborockTargets.ts` | Fetch targets on mount + refetch |
| `fetcher-core/webui/app/hooks/useRoborockStatus.ts` | Poll status only while dialog is open |
| `fetcher-core/webui/app/components/RoborockCleanDialog.tsx` | The modal |
| `fetcher-core/webui/app/components/RoborockCleanButton.tsx` | Rewritten button + hash-synced modal |

---

### Task 1: Provision HA labels and the two missing floor automations

**Files:**
- Create: `scripts/roborock-ha-provision.mjs`
- Create: `scripts/roborock-ha-verify.mjs` (written in Step 4)

**Interfaces:**
- Consumes: nothing.
- Produces: labels `roborock_floor` and `roborock_room` in HA, assigned to 9 automations; automations `automation.stada_van0` and `automation.stada_annexet`. Task 3's manual verification depends on these existing.

This is an operations script, not application code, so it is verified by running it rather than by unit tests. It must be idempotent — running it twice changes nothing the second time.

- [ ] **Step 1: Write the provisioning script**

Create `scripts/roborock-ha-provision.mjs`:

```javascript
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
```

- [ ] **Step 2: Dry run to preview every change**

Run from the repo root:

```bash
node scripts/roborock-ha-provision.mjs --dry-run
```

Expected, ending with `Dry run complete, nothing changed.`:
- two `label ...: CREATE` lines
- two `automation ...: CREATE` lines
- **seven** `ADD label` lines — the five rooms plus `Städa Vån1` and `Städa Uterum`
- exactly two `WARN` lines, for `Städa Vån0` and `Städa Annexet`

Those two warn because a dry run doesn't actually create them, so they can't be labelled yet. That is expected here and must **not** appear on the real run in Step 3. A `WARN` for any other alias means an automation was renamed in HA — stop and reconcile the alias lists in the script before continuing.

- [ ] **Step 3: Run it for real**

```bash
node scripts/roborock-ha-provision.mjs
```

Expected: same lines without the dry-run suffix, ending `Provisioning complete.`, and **no** `WARN` lines.

- [ ] **Step 4: Verify via the same template the web-ui will use**

Nesting Jinja quotes inside a shell string is error-prone, so drive the check from Node
instead. Create `scripts/roborock-ha-verify.mjs`:

```javascript
#!/usr/bin/env node
// Prints the labelled Roborock automations exactly as the web-ui will see them.
const URL_BASE = (process.env.HOMEASSISTANT_URL || '').replace(/\/$/, '');
const TOKEN = process.env.HOMEASSISTANT_TOKEN;

const template =
  `{% set ns = namespace(floors=[], rooms=[]) %}` +
  `{% for e in label_entities('roborock_floor') %}` +
  `{% set ns.floors = ns.floors + [{'entity_id': e, 'name': state_attr(e, 'friendly_name')}] %}` +
  `{% endfor %}` +
  `{% for e in label_entities('roborock_room') %}` +
  `{% set ns.rooms = ns.rooms + [{'entity_id': e, 'name': state_attr(e, 'friendly_name')}] %}` +
  `{% endfor %}` +
  `{{ {'floors': ns.floors, 'rooms': ns.rooms} | tojson }}`;

const resp = await fetch(`${URL_BASE}/api/template`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ template }),
});
const parsed = JSON.parse(await resp.text());
console.log(`floors (${parsed.floors.length}):`, parsed.floors.map(f => f.name).join(', '));
console.log(`rooms  (${parsed.rooms.length}):`, parsed.rooms.map(r => r.name).join(', '));
```

Run it:

```bash
node scripts/roborock-ha-verify.mjs
```

Expected exactly:

```
floors (4): Städa Vån1, Städa Vån0, Städa Uterum, Städa Annexet
rooms  (5): Städa Köket, Städa Kontoret, Städa Vardagsrummet, Städa Matrummet, Städa Lobbyn
```

Order within each line may vary — HA does not guarantee label ordering, and sorting is
the web-ui's job. Only the counts and membership matter here.

- [ ] **Step 5: Confirm idempotency**

```bash
node scripts/roborock-ha-provision.mjs
```

Expected: every line reads `already exists` or `already labelled`. No `CREATE`, no `ADD`.

- [ ] **Step 6: Commit**

```bash
git add scripts/roborock-ha-provision.mjs scripts/roborock-ha-verify.mjs
git commit -m "feat(roborock): add idempotent HA provisioning script for clean dialog" -m "Creates roborock_floor/roborock_room labels, the Vån0 and Annexet whole-floor automations, and assigns labels while preserving any already present. Adds a verify script that prints what the web-ui will see."
```

---

### Task 2: Pure parsing library

**Files:**
- Create: `fetcher-core/webui/app/lib/roborock.ts`
- Test: `fetcher-core/webui/app/lib/roborock.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces, used by Tasks 3 and 4:
  - `type RoborockTarget = { entity_id: string; name: string }`
  - `type RoborockTargets = { floors: RoborockTarget[]; rooms: RoborockTarget[] }`
  - `type RoborockStatus = { state: string; status: string; battery: number | null; room: string | null; error: string | null }`
  - `const VACUUM_ENTITY: string`
  - `const TARGETS_TEMPLATE: string`, `const STATUS_TEMPLATE: string`
  - `function parseTargets(raw: string): RoborockTargets`
  - `function parseStatus(raw: string): RoborockStatus`
  - `function isAllowedTarget(targets: RoborockTargets, entityId: string): boolean`
  - `function stripCleanPrefix(name: string): string`

- [ ] **Step 1: Write the failing tests**

Create `fetcher-core/webui/app/lib/roborock.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import {
  parseTargets,
  parseStatus,
  isAllowedTarget,
  stripCleanPrefix,
  RoborockTargets,
} from './roborock';

describe('stripCleanPrefix', () => {
  it('removes the Städa prefix', () => {
    expect(stripCleanPrefix('Städa Köket')).toBe('Köket');
  });

  it('leaves names without the prefix untouched', () => {
    expect(stripCleanPrefix('Köket')).toBe('Köket');
  });
});

describe('parseTargets', () => {
  it('strips prefixes and sorts each group with Swedish collation', () => {
    const raw = JSON.stringify({
      floors: [
        { entity_id: 'automation.stada_van1', name: 'Städa Vån1' },
        { entity_id: 'automation.stada_annexet', name: 'Städa Annexet' },
      ],
      rooms: [
        { entity_id: 'automation.stada_koket', name: 'Städa Köket' },
        { entity_id: 'automation.stada_kontoret', name: 'Städa Kontoret' },
      ],
    });
    const targets = parseTargets(raw);
    expect(targets.floors.map(f => f.name)).toEqual(['Annexet', 'Vån1']);
    // In Swedish collation 'ö' sorts after 'z', so Kontoret precedes Köket.
    expect(targets.rooms.map(r => r.name)).toEqual(['Kontoret', 'Köket']);
    expect(targets.floors[0].entity_id).toBe('automation.stada_annexet');
  });

  it('drops entries missing an entity_id or name', () => {
    const raw = JSON.stringify({
      floors: [
        { entity_id: 'automation.ok', name: 'Städa Ok' },
        { entity_id: 'automation.broken', name: null },
        null,
      ],
      rooms: [],
    });
    expect(parseTargets(raw).floors).toEqual([{ entity_id: 'automation.ok', name: 'Ok' }]);
  });

  it('returns empty groups when HA reports no labelled entities', () => {
    expect(parseTargets('{"floors": [], "rooms": []}')).toEqual({ floors: [], rooms: [] });
  });
});

describe('parseStatus', () => {
  it('converts the all-strings HA payload into typed values', () => {
    const raw = '{"battery": "100", "error": "none", "room": "Office", "state": "docked", "status": "charging"}';
    expect(parseStatus(raw)).toEqual({
      state: 'docked',
      status: 'charging',
      battery: 100,
      room: 'Office',
      error: null,
    });
  });

  it('maps unknown and unavailable sensor values to null', () => {
    const raw = '{"battery": "unknown", "error": "unavailable", "room": "unknown", "state": "docked", "status": "charging"}';
    const status = parseStatus(raw);
    expect(status.battery).toBeNull();
    expect(status.room).toBeNull();
    expect(status.error).toBeNull();
  });

  it('surfaces a real error string', () => {
    const raw = '{"battery": "42", "error": "stuck", "room": "Office", "state": "error", "status": "error"}';
    expect(parseStatus(raw).error).toBe('stuck');
  });
});

describe('isAllowedTarget', () => {
  const targets: RoborockTargets = {
    floors: [{ entity_id: 'automation.stada_van1', name: 'Vån1' }],
    rooms: [{ entity_id: 'automation.stada_koket', name: 'Köket' }],
  };

  it('accepts a labelled floor and a labelled room', () => {
    expect(isAllowedTarget(targets, 'automation.stada_van1')).toBe(true);
    expect(isAllowedTarget(targets, 'automation.stada_koket')).toBe(true);
  });

  it('rejects any automation that is not labelled', () => {
    expect(isAllowedTarget(targets, 'automation.unlock_front_door')).toBe(false);
    expect(isAllowedTarget(targets, 'automation.stada')).toBe(false);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
npm test -- roborock
```

Expected: FAIL — `Failed to resolve import "./roborock"`.

- [ ] **Step 3: Write the implementation**

Create `fetcher-core/webui/app/lib/roborock.ts`:

```typescript
export type RoborockTarget = {
  entity_id: string;
  name: string;
};

export type RoborockTargets = {
  floors: RoborockTarget[];
  rooms: RoborockTarget[];
};

export type RoborockStatus = {
  state: string;
  status: string;
  battery: number | null;
  room: string | null;
  error: string | null;
};

export const VACUUM_ENTITY = 'vacuum.roborock_s6_maxv';
export const FLOOR_LABEL = 'roborock_floor';
export const ROOM_LABEL = 'roborock_room';

const CLEAN_PREFIX = 'Städa ';

// Sensor values that mean "nothing to show" rather than a real reading.
const EMPTY_VALUES = new Set(['unknown', 'unavailable', 'none', '']);

// Emits {"floors": [{entity_id, name}], "rooms": [...]} in a single call.
export const TARGETS_TEMPLATE =
  `{% set ns = namespace(floors=[], rooms=[]) %}` +
  `{% for e in label_entities('${FLOOR_LABEL}') %}` +
  `{% set ns.floors = ns.floors + [{'entity_id': e, 'name': state_attr(e, 'friendly_name')}] %}` +
  `{% endfor %}` +
  `{% for e in label_entities('${ROOM_LABEL}') %}` +
  `{% set ns.rooms = ns.rooms + [{'entity_id': e, 'name': state_attr(e, 'friendly_name')}] %}` +
  `{% endfor %}` +
  `{{ {'floors': ns.floors, 'rooms': ns.rooms} | tojson }}`;

export const STATUS_TEMPLATE =
  `{{ {` +
  `'state': states('${VACUUM_ENTITY}'), ` +
  `'status': states('sensor.roborock_s6_maxv_status'), ` +
  `'battery': states('sensor.roborock_s6_maxv_battery'), ` +
  `'room': states('sensor.roborock_s6_maxv_current_room'), ` +
  `'error': states('sensor.roborock_s6_maxv_vacuum_error')` +
  `} | tojson }}`;

export function stripCleanPrefix(name: string): string {
  return name.startsWith(CLEAN_PREFIX) ? name.slice(CLEAN_PREFIX.length) : name;
}

function optionalString(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  return EMPTY_VALUES.has(value.toLowerCase()) ? null : value;
}

function toTargetList(raw: unknown): RoborockTarget[] {
  if (!Array.isArray(raw)) return [];
  const targets: RoborockTarget[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const { entity_id: entityId, name } = item as Record<string, unknown>;
    if (typeof entityId !== 'string' || typeof name !== 'string') continue;
    targets.push({ entity_id: entityId, name: stripCleanPrefix(name) });
  }
  return targets.sort((a, b) => a.name.localeCompare(b.name, 'sv'));
}

/** Parses the TARGETS_TEMPLATE response. Throws if the payload is not JSON. */
export function parseTargets(raw: string): RoborockTargets {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  return {
    floors: toTargetList(parsed?.floors),
    rooms: toTargetList(parsed?.rooms),
  };
}

/** Parses the STATUS_TEMPLATE response, whose values are all strings. */
export function parseStatus(raw: string): RoborockStatus {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const battery = Number(parsed?.battery);
  return {
    state: typeof parsed?.state === 'string' ? parsed.state : 'unknown',
    status: typeof parsed?.status === 'string' ? parsed.status : 'unknown',
    battery: Number.isFinite(battery) ? battery : null,
    room: optionalString(parsed?.room),
    error: optionalString(parsed?.error),
  };
}

/**
 * Guards the trigger endpoint: only automations carrying one of our labels may
 * be fired from the browser.
 */
export function isAllowedTarget(targets: RoborockTargets, entityId: string): boolean {
  return [...targets.floors, ...targets.rooms].some(t => t.entity_id === entityId);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npm test -- roborock
```

Expected: PASS, 10 tests.

- [ ] **Step 5: Typecheck**

```bash
npm run typecheck
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add fetcher-core/webui/app/lib/roborock.ts fetcher-core/webui/app/lib/roborock.test.ts
git commit -m "feat(webui): add Roborock HA template parsing library" -m "Pure helpers for the HA targets/status templates plus the allow-list guard used by the trigger endpoint."
```

---

### Task 3: HA client and API routes

**Files:**
- Create: `fetcher-core/webui/app/lib/haClient.ts`
- Create: `fetcher-core/webui/app/api/roborock/targets/route.ts`
- Create: `fetcher-core/webui/app/api/roborock/status/route.ts`
- Create: `fetcher-core/webui/app/api/roborock/trigger/route.ts`
- Create: `fetcher-core/webui/app/api/roborock/dock/route.ts`
- Modify: `fetcher-core/webui/README.md`

**Interfaces:**
- Consumes from Task 2: `TARGETS_TEMPLATE`, `STATUS_TEMPLATE`, `VACUUM_ENTITY`, `parseTargets`, `parseStatus`, `isAllowedTarget`.
- Produces, used by Task 4:
  - `GET /api/roborock/targets` → `RoborockTargets` (always 200; `{floors:[],rooms:[]}` when HA is unset or unreachable)
  - `GET /api/roborock/status` → `RoborockStatus`, or 503 `{error}`
  - `POST /api/roborock/trigger` body `{entity_id}` → 200 `{ok:true}` / 400 / 403 / 503
  - `POST /api/roborock/dock` → 200 `{ok:true}` / 503

- [ ] **Step 1: Write the HA client**

Create `fetcher-core/webui/app/lib/haClient.ts`:

```typescript
// Server-side only. Never import this from a 'use client' component — it reads
// the long-lived Home Assistant token from the environment.

export type HaConfig = {
  url: string;
  token: string;
};

export function haConfig(): HaConfig | null {
  const url = process.env.HOMEASSISTANT_URL;
  const token = process.env.HOMEASSISTANT_TOKEN;
  if (!url || !token) return null;
  return { url: url.replace(/\/$/, ''), token };
}

async function haFetch(cfg: HaConfig, path: string, body: unknown): Promise<Response> {
  const resp = await fetch(`${cfg.url}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${cfg.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
    cache: 'no-store',
  });
  if (!resp.ok) {
    throw new Error(`HA ${path} returned ${resp.status}: ${await resp.text()}`);
  }
  return resp;
}

/** Renders a Jinja template through HA and returns the raw rendered string. */
export async function haTemplate(cfg: HaConfig, template: string): Promise<string> {
  const resp = await haFetch(cfg, '/api/template', { template });
  return resp.text();
}

/** Calls an HA service, e.g. haService(cfg, 'automation', 'trigger', {...}). */
export async function haService(
  cfg: HaConfig,
  domain: string,
  service: string,
  data: Record<string, unknown>,
): Promise<void> {
  await haFetch(cfg, `/api/services/${domain}/${service}`, data);
}
```

- [ ] **Step 2: Write the targets route**

Create `fetcher-core/webui/app/api/roborock/targets/route.ts`:

```typescript
import { NextResponse } from 'next/server';
import { haConfig, haTemplate } from '../../../lib/haClient';
import { TARGETS_TEMPLATE, parseTargets, RoborockTargets } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

const EMPTY: RoborockTargets = { floors: [], rooms: [] };

export async function GET() {
  const cfg = haConfig();
  if (!cfg) return NextResponse.json(EMPTY);

  try {
    const raw = await haTemplate(cfg, TARGETS_TEMPLATE);
    return NextResponse.json(parseTargets(raw));
  } catch (e) {
    console.error('Failed to load Roborock targets from Home Assistant:', e);
    return NextResponse.json(EMPTY);
  }
}
```

- [ ] **Step 3: Write the status route**

Create `fetcher-core/webui/app/api/roborock/status/route.ts`:

```typescript
import { NextResponse } from 'next/server';
import { haConfig, haTemplate } from '../../../lib/haClient';
import { STATUS_TEMPLATE, parseStatus } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

export async function GET() {
  const cfg = haConfig();
  if (!cfg) return NextResponse.json({ error: 'Home Assistant not configured' }, { status: 503 });

  try {
    const raw = await haTemplate(cfg, STATUS_TEMPLATE);
    return NextResponse.json(parseStatus(raw));
  } catch (e) {
    console.error('Failed to load Roborock status from Home Assistant:', e);
    return NextResponse.json({ error: 'Home Assistant unavailable' }, { status: 503 });
  }
}
```

- [ ] **Step 4: Write the trigger route**

The allow-list check is the security boundary — without it the browser could fire any automation in HA. Resolve the labelled set on every request rather than trusting the client.

Create `fetcher-core/webui/app/api/roborock/trigger/route.ts`:

```typescript
import { NextRequest, NextResponse } from 'next/server';
import { haConfig, haTemplate, haService } from '../../../lib/haClient';
import { TARGETS_TEMPLATE, parseTargets, isAllowedTarget } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  const cfg = haConfig();
  if (!cfg) return NextResponse.json({ error: 'Home Assistant not configured' }, { status: 503 });

  let entityId: unknown;
  try {
    ({ entity_id: entityId } = await request.json());
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }
  if (typeof entityId !== 'string' || !entityId) {
    return NextResponse.json({ error: 'entity_id is required' }, { status: 400 });
  }

  try {
    // Re-resolve the labelled set server-side; never trust the client's entity_id.
    const targets = parseTargets(await haTemplate(cfg, TARGETS_TEMPLATE));
    if (!isAllowedTarget(targets, entityId)) {
      console.warn(`Rejected Roborock trigger for unlabelled entity: ${entityId}`);
      return NextResponse.json({ error: 'Not a Roborock cleaning automation' }, { status: 403 });
    }

    await haService(cfg, 'automation', 'trigger', { entity_id: entityId });
    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error(`Failed to trigger ${entityId}:`, e);
    return NextResponse.json({ error: 'Home Assistant unavailable' }, { status: 503 });
  }
}
```

- [ ] **Step 5: Write the dock route**

Create `fetcher-core/webui/app/api/roborock/dock/route.ts`:

```typescript
import { NextResponse } from 'next/server';
import { haConfig, haService } from '../../../lib/haClient';
import { VACUUM_ENTITY } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

export async function POST() {
  const cfg = haConfig();
  if (!cfg) return NextResponse.json({ error: 'Home Assistant not configured' }, { status: 503 });

  try {
    await haService(cfg, 'vacuum', 'return_to_base', { entity_id: VACUUM_ENTITY });
    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error('Failed to send Roborock back to dock:', e);
    return NextResponse.json({ error: 'Home Assistant unavailable' }, { status: 503 });
  }
}
```

- [ ] **Step 6: Document the new environment variables**

Append this section to the end of `fetcher-core/webui/README.md`:

````markdown
## Home Assistant

The Roborock clean dialog reads its floors and rooms from Home Assistant. Add to
`fetcher-core/webui/.env` (loaded by `docker-compose.local.yml`):

```
HOMEASSISTANT_URL=http://192.168.68.87:8123
HOMEASSISTANT_TOKEN=<long-lived access token>
```

Both are read server-side only; the token never reaches the browser. When either is
unset the dialog reports no targets and the Clean button hides itself.

Floors and rooms are discovered from the `roborock_floor` and `roborock_room` HA
labels — run `scripts/roborock-ha-provision.mjs` to create them. To add a room later,
label its automation in Home Assistant; no code change is needed.
````

- [ ] **Step 7: Typecheck and run the full test suite**

```bash
npm run typecheck && npm test
```

Expected: no type errors, all tests pass.

- [ ] **Step 8: Verify the routes against live HA**

Create `fetcher-core/webui/.env` locally with the two variables from Step 6, then:

```bash
npm run dev
```

In a second shell:

```bash
curl -s localhost:3000/api/roborock/targets
```

Expected: four floors (`Annexet`, `Uterum`, `Vån0`, `Vån1`) and five rooms (`Kontoret`, `Köket`, `Lobbyn`, `Matrummet`, `Vardagsrummet`), all with the `Städa ` prefix stripped.

```bash
curl -s localhost:3000/api/roborock/status
```

Expected: JSON with a numeric `battery` and a string `state`.

Now confirm the security boundary actually holds:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:3000/api/roborock/trigger -H "Content-Type: application/json" -d '{"entity_id":"automation.stada"}'
```

Expected: `403`. `automation.stada` exists in HA but is deliberately unlabelled, so this proves the allow-list rejects real-but-unlabelled automations rather than merely nonexistent ones.

- [ ] **Step 9: Commit**

```bash
git add fetcher-core/webui/app/lib/haClient.ts fetcher-core/webui/app/api/roborock fetcher-core/webui/README.md
git commit -m "feat(webui): add Home Assistant-backed Roborock API routes" -m "Adds targets, status, trigger and dock endpoints. The trigger endpoint re-resolves the labelled automation set server-side and rejects anything not carrying a roborock label."
```

---

### Task 4: Hooks, dialog and button

**Files:**
- Create: `fetcher-core/webui/app/hooks/useRoborockTargets.ts`
- Create: `fetcher-core/webui/app/hooks/useRoborockStatus.ts`
- Create: `fetcher-core/webui/app/components/RoborockCleanDialog.tsx`
- Modify: `fetcher-core/webui/app/components/RoborockCleanButton.tsx` (full rewrite)

**Interfaces:**
- Consumes from Tasks 2 and 3: the four API routes and the `RoborockTarget` / `RoborockTargets` / `RoborockStatus` types.
- Produces: `RoborockCleanButton` default export, already mounted in `app/page.tsx:48` — that line does not change.

- [ ] **Step 1: Write the targets hook**

Create `fetcher-core/webui/app/hooks/useRoborockTargets.ts`:

```typescript
'use client';

import { useCallback, useEffect, useState } from 'react';
import { RoborockTargets } from '../lib/roborock';

const EMPTY: RoborockTargets = { floors: [], rooms: [] };

/**
 * Fetches the labelled clean targets on mount, so the button knows whether to
 * render at all, and exposes refetch for when the dialog opens.
 */
export function useRoborockTargets() {
  const [targets, setTargets] = useState<RoborockTargets>(EMPTY);
  const [isLoading, setIsLoading] = useState(true);

  const refetch = useCallback(async () => {
    try {
      const resp = await fetch('/api/roborock/targets', { cache: 'no-store' });
      if (!resp.ok) throw new Error(`targets returned ${resp.status}`);
      setTargets(await resp.json());
    } catch (e) {
      console.error('Failed to load Roborock targets:', e);
      setTargets(EMPTY);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { targets, isLoading, refetch };
}
```

- [ ] **Step 2: Write the status hook**

Create `fetcher-core/webui/app/hooks/useRoborockStatus.ts`:

```typescript
'use client';

import { useEffect, useState } from 'react';
import { RoborockStatus } from '../lib/roborock';

const POLL_INTERVAL_MS = 5000;

/** Polls vacuum status while `enabled`, and stops as soon as it goes false. */
export function useRoborockStatus(enabled: boolean): RoborockStatus | null {
  const [status, setStatus] = useState<RoborockStatus | null>(null);

  useEffect(() => {
    if (!enabled) {
      setStatus(null);
      return;
    }

    let cancelled = false;

    const tick = async () => {
      try {
        const resp = await fetch('/api/roborock/status', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        if (!cancelled) setStatus(data);
      } catch {
        // Transient failure: keep showing the last known status.
      }
    };

    tick();
    const timer = setInterval(tick, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled]);

  return status;
}
```

- [ ] **Step 3: Write the dialog**

Create `fetcher-core/webui/app/components/RoborockCleanDialog.tsx`:

```tsx
'use client';

import React, { useEffect, useState } from 'react';
import { RoborockTarget, RoborockTargets } from '../lib/roborock';
import { useRoborockStatus } from '../hooks/useRoborockStatus';

type Props = {
  targets: RoborockTargets;
  onClose: () => void;
};

const RoborockCleanDialog: React.FC<Props> = ({ targets, onClose }) => {
  const status = useRoborockStatus(true);
  const [pending, setPending] = useState<string | null>(null);
  const [started, setStarted] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  const start = async (target: RoborockTarget) => {
    setPending(target.entity_id);
    setError(null);
    setStarted(null);
    try {
      const resp = await fetch('/api/roborock/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entity_id: target.entity_id }),
      });
      if (!resp.ok) throw new Error(`Could not start ${target.name}`);
      setStarted(target.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setPending(null);
    }
  };

  const dock = async () => {
    setError(null);
    try {
      const resp = await fetch('/api/roborock/dock', { method: 'POST' });
      if (!resp.ok) throw new Error('Could not send the vacuum back to its dock');
      setStarted('Returning to dock');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    }
  };

  const renderTarget = (target: RoborockTarget, large: boolean) => (
    <button
      key={target.entity_id}
      onClick={() => start(target)}
      disabled={pending !== null}
      className={[
        'rounded-xl font-semibold text-white transition-colors disabled:opacity-50',
        large
          ? 'bg-green-700 hover:bg-green-600 px-6 py-6 text-xl'
          : 'bg-gray-700 hover:bg-gray-600 px-4 py-5 text-lg',
      ].join(' ')}
    >
      {pending === target.entity_id ? 'Starting…' : target.name}
    </button>
  );

  return (
    <div className="fixed inset-0 z-50 bg-gray-900/95 backdrop-blur-sm flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
        <h2 className="text-xl font-semibold text-white">Clean</h2>
        <button
          onClick={onClose}
          className="w-10 h-10 rounded-full bg-gray-700 hover:bg-gray-600 text-white flex items-center justify-center transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Targets */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-3">
          Whole floor
        </h3>
        <div className="grid grid-cols-2 gap-3 mb-8">
          {targets.floors.map(f => renderTarget(f, true))}
        </div>

        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-3">
          Rooms
        </h3>
        <div className="grid grid-cols-3 gap-3">
          {targets.rooms.map(r => renderTarget(r, false))}
        </div>
      </div>

      {/* Status footer */}
      <div className="flex items-center justify-between gap-4 px-6 py-4 border-t border-gray-700">
        <div className="text-sm text-gray-300 flex items-center gap-3 min-w-0">
          {status ? (
            <>
              <span className="capitalize">{status.status}</span>
              {status.battery !== null && <span>{status.battery}%</span>}
              {status.room && <span className="truncate">in {status.room}</span>}
              {status.error && <span className="text-red-400">{status.error}</span>}
            </>
          ) : (
            <span className="text-gray-500">Loading status…</span>
          )}
          {started && <span className="text-green-400 truncate">{started} started</span>}
          {error && <span className="text-red-400 truncate">{error}</span>}
        </div>
        <button
          onClick={dock}
          className="shrink-0 px-4 py-2 rounded-full bg-gray-700 hover:bg-gray-600 text-white text-sm font-semibold transition-colors"
        >
          Return to dock
        </button>
      </div>
    </div>
  );
};

export default RoborockCleanDialog;
```

- [ ] **Step 4: Rewrite the button**

Replace the **entire contents** of `fetcher-core/webui/app/components/RoborockCleanButton.tsx`:

```tsx
'use client';

import React, { useEffect, useState } from 'react';
import RoborockCleanDialog from './RoborockCleanDialog';
import { useRoborockTargets } from '../hooks/useRoborockTargets';

const RoborockCleanButton: React.FC = () => {
  const [open, setOpen] = useState(false);
  const { targets, isLoading, refetch } = useRoborockTargets();

  // Sync with URL hash, matching SpeakersButton.
  useEffect(() => {
    const check = () => setOpen(window.location.hash === '#clean');
    check();
    window.addEventListener('hashchange', check);
    return () => window.removeEventListener('hashchange', check);
  }, []);

  const handleClose = () => {
    history.pushState(null, '', window.location.pathname + window.location.search);
    setOpen(false);
  };

  const toggle = () => {
    if (open) {
      handleClose();
    } else {
      refetch();
      history.pushState(null, '', '#clean');
      setOpen(true);
    }
  };

  const isEmpty = targets.floors.length === 0 && targets.rooms.length === 0;

  // Hide entirely when Home Assistant is unconfigured or unreachable.
  if (isLoading || isEmpty) return null;

  return (
    <>
      <button
        onClick={toggle}
        title="Start Roborock cleaning"
        className="px-4 py-1.5 rounded-full shadow text-sm font-semibold cursor-pointer transition-colors duration-200 bg-green-600 hover:bg-green-700 text-white flex items-center gap-1.5"
      >
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M10 2a8 8 0 100 16 8 8 0 000-16zm0 3a5 5 0 110 10 5 5 0 010-10zm0 3a2 2 0 100 4 2 2 0 000-4z" clipRule="evenodd" />
        </svg>
        Clean
      </button>
      {open && <RoborockCleanDialog targets={targets} onClose={handleClose} />}
    </>
  );
};

export default RoborockCleanButton;
```

- [ ] **Step 5: Typecheck and test**

```bash
npm run typecheck && npm test
```

Expected: no type errors, all tests pass.

- [ ] **Step 6: Verify in the browser**

With `npm run dev` running, open `http://localhost:3000` and confirm:
1. The green **Clean** button appears in the header.
2. Clicking it opens the dialog with 4 large floor buttons and 5 room buttons.
3. The footer shows a status and battery percentage within ~5 seconds.
4. Pressing Escape closes the dialog and the URL hash clears.
5. With the dialog closed, no further requests to `/api/roborock/status` appear in the network tab — polling must stop.

Then trigger a real clean by clicking **Kontoret**, and confirm the footer status changes from `charging` and the vacuum leaves the dock. Click **Return to dock** to send it back.

- [ ] **Step 7: Commit**

```bash
git add fetcher-core/webui/app/hooks/useRoborockTargets.ts fetcher-core/webui/app/hooks/useRoborockStatus.ts fetcher-core/webui/app/components/RoborockCleanDialog.tsx fetcher-core/webui/app/components/RoborockCleanButton.tsx
git commit -m "feat(webui): add Roborock clean dialog with floors, rooms and live status" -m "Rewrites the Clean button as a hash-synced modal listing whole-floor cleans on top and rooms below, with a live status footer and return-to-dock. Status polling runs only while the dialog is open."
```

---

### Task 5: Remove the dead Roborock sidecar code

The previous attempt on this branch called a Roborock cloud sidecar at `http://localhost:8081`. No such service exists anywhere in the repo — no Dockerfile, no compose entry, no Python module. These files and the two markdown documents describing that service are now superseded.

**Files:**
- Delete: `fetcher-core/webui/app/api/roborock/zones/route.ts`
- Delete: `fetcher-core/webui/app/api/roborock/[deviceId]/[mapId]/[zoneId]/clean/route.ts`
- Delete: `fetcher-core/webui/app/hooks/useRoborockZones.ts`
- Delete: `fetcher-core/webui/ROBOROCK_FINDINGS.md`
- Delete: `fetcher-core/webui/ROBOROCK_UI_INTEGRATION.md`
- Modify: `fetcher-core/webui/app/lib/types.ts` (remove `RoborockZone`)

- [ ] **Step 1: Confirm nothing still references the old code**

```bash
grep -rn "useRoborockZones\|RoborockZone\|ROBOROCK_SIDECAR_URL\|roborock/zones" fetcher-core/webui/app fetcher-core/webui/*.md
```

Expected: matches only in the files listed for deletion above. If anything else matches, stop and resolve it before deleting.

- [ ] **Step 2: Delete the dead files**

```bash
git rm fetcher-core/webui/app/api/roborock/zones/route.ts fetcher-core/webui/app/hooks/useRoborockZones.ts fetcher-core/webui/ROBOROCK_FINDINGS.md fetcher-core/webui/ROBOROCK_UI_INTEGRATION.md
git rm -r "fetcher-core/webui/app/api/roborock/[deviceId]"
```

- [ ] **Step 3: Remove the `RoborockZone` type**

In `fetcher-core/webui/app/lib/types.ts`, delete this block at the end of the file (lines 85-95):

```typescript
export type RoborockZone = {
  zone_id: string;
  zone_name: string;
  segment_id: number;
  iot_id: string;
  map_name: string;
  map_flag: number;
  device_id: string;
  device_name: string;
  device_product_id: string;
};
```

- [ ] **Step 4: Verify nothing broke**

```bash
npm run typecheck && npm test && npm run build
```

Expected: no type errors, all tests pass, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add -A fetcher-core/webui
git commit -m "chore(webui): remove dead Roborock cloud sidecar code" -m "The zones and clean routes called a localhost:8081 service that exists nowhere in this repo. Superseded by the Home Assistant-backed dialog."
```

---

## Deployment

After the branch merges, on rpi5:

1. Add to `~/iot-fetcher/fetcher-core/webui/.env`:
   ```
   HOMEASSISTANT_URL=http://192.168.68.87:8123
   HOMEASSISTANT_TOKEN=<long-lived access token>
   ```
   Use a token dedicated to the dashboard so it can be revoked independently.
2. Redeploy: `sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`
3. Load the dashboard, open the Clean dialog, and confirm all 4 floors and 5 rooms appear.
4. Run one room clean and one whole-floor clean, confirming the correct map is selected in HA.

The HA provisioning from Task 1 is already live — it was applied directly to the running Home Assistant and is not part of the container deploy.
