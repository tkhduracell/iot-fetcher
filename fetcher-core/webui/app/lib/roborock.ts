export type RoborockTarget = {
  entity_id: string;
  name: string;
};

export type RoborockTargets = {
  floors: RoborockTarget[];
  rooms: RoborockTarget[];
};

export type RoborockTargetsResponse = RoborockTargets & {
  /**
   * False when HOMEASSISTANT_URL/TOKEN are unset. That is a deliberate opt-out
   * rather than a failure, and is what lets the client tell "no Home Assistant
   * here" apart from "Home Assistant has not reported its labels yet".
   */
  configured: boolean;
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
 * Whether the dashboard should keep retrying the targets fetch in the background.
 *
 * An empty target set means two very different things. Home Assistant may not be
 * configured at all — deliberate, so stop asking. Or it is configured but has not
 * reported any labelled automations yet, which is exactly what happens while HA is
 * still starting up and its Roborock entities have not registered. Only the second
 * case should retry; without it the Clean button hides itself after a reboot and
 * stays hidden until somebody reloads the wall tablet by hand.
 */
export function shouldRetryTargets(state: {
  configured: boolean;
  isEmpty: boolean;
  lastFetchFailed: boolean;
}): boolean {
  if (state.lastFetchFailed) return true;
  if (!state.configured) return false;
  return state.isEmpty;
}

/**
 * Guards the trigger endpoint: only automations carrying one of our labels may
 * be fired from the browser.
 */
export function isAllowedTarget(targets: RoborockTargets, entityId: string): boolean {
  return [...targets.floors, ...targets.rooms].some(t => t.entity_id === entityId);
}
