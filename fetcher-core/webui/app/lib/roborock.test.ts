import { describe, it, expect } from 'vitest';
import {
  TARGETS_TEMPLATE,
  parseTargets,
  parseStatus,
  isAllowedTarget,
  shouldRetryTargets,
  stripCleanPrefix,
  classifyStart,
  START_CONFIRM_DELAY_MS,
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
        { entity_id: 'automation.stada_uterum', name: 'Städa Uterum' },
      ],
      rooms: [
        { entity_id: 'automation.stada_koket', name: 'Städa Köket' },
        { entity_id: 'automation.stada_kontoret', name: 'Städa Kontoret' },
      ],
    });
    const targets = parseTargets(raw);
    expect(targets.floors.map(f => f.name)).toEqual(['Uterum', 'Vån1']);
    // In Swedish collation 'ö' sorts after 'z', so Kontoret precedes Köket.
    expect(targets.rooms.map(r => r.name)).toEqual(['Kontoret', 'Köket']);
    expect(targets.floors[0].entity_id).toBe('automation.stada_uterum');
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

describe('TARGETS_TEMPLATE', () => {
  // A production minifier once folded this template's concatenated parts and
  // dropped the entire floors block, producing unbalanced Jinja that Home
  // Assistant rejected with a 400. Nothing caught it because the source was
  // fine — only the bundle was broken. These assert the shape stays intact.
  it('queries both labels', () => {
    expect(TARGETS_TEMPLATE).toContain("label_entities('roborock_floor')");
    expect(TARGETS_TEMPLATE).toContain("label_entities('roborock_room')");
  });

  it('builds both accumulators and balances every block', () => {
    expect(TARGETS_TEMPLATE).toContain('ns.floors = ns.floors +');
    expect(TARGETS_TEMPLATE).toContain('ns.rooms = ns.rooms +');

    const opens = TARGETS_TEMPLATE.match(/\{%-?\s*for\b/g) ?? [];
    const closes = TARGETS_TEMPLATE.match(/\{%-?\s*endfor\b/g) ?? [];
    expect(opens).toHaveLength(2);
    expect(closes).toHaveLength(2);
  });

  it('emits both keys in its output expression', () => {
    expect(TARGETS_TEMPLATE).toContain("'floors': ns.floors");
    expect(TARGETS_TEMPLATE).toContain("'rooms': ns.rooms");
    expect(TARGETS_TEMPLATE).toContain('tojson');
  });
});

describe('shouldRetryTargets', () => {
  it('retries after a failed fetch so a transient HA outage self-heals', () => {
    expect(shouldRetryTargets({ configured: true, isEmpty: true, lastFetchFailed: true })).toBe(true);
    // Even with targets already known — the next fetch may be the one that matters.
    expect(shouldRetryTargets({ configured: true, isEmpty: false, lastFetchFailed: true })).toBe(true);
  });

  it('retries when HA is configured but has reported no labels yet', () => {
    // This is the post-reboot case: HA's HTTP API is up before its Roborock
    // entities register, so label_entities() renders to an empty list.
    expect(shouldRetryTargets({ configured: true, isEmpty: true, lastFetchFailed: false })).toBe(true);
  });

  it('does not retry when Home Assistant is deliberately unconfigured', () => {
    expect(shouldRetryTargets({ configured: false, isEmpty: true, lastFetchFailed: false })).toBe(false);
  });

  it('stops retrying once targets are known', () => {
    expect(shouldRetryTargets({ configured: true, isEmpty: false, lastFetchFailed: false })).toBe(false);
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
    // Name-prefix lookalikes must not slip through: matching is on the full
    // entity_id, not on the "stada" prefix the labelled automations share.
    expect(isAllowedTarget(targets, 'automation.stada_something_else')).toBe(false);
  });
});

describe('classifyStart', () => {
  const T0 = 1_000_000;
  const later = T0 + START_CONFIRM_DELAY_MS + 1;

  it('is pending before anything was requested', () => {
    expect(
      classifyStart({ requestedAt: null, now: later, state: 'docked', stale: false })
    ).toBe('pending');
  });

  it('is pending inside the confirmation delay, even while docked', () => {
    expect(
      classifyStart({ requestedAt: T0, now: T0 + 1000, state: 'docked', stale: false })
    ).toBe('pending');
  });

  it('reports not-started when the vacuum is still docked after the delay', () => {
    expect(
      classifyStart({ requestedAt: T0, now: later, state: 'docked', stale: false })
    ).toBe('not-started');
  });

  it('reports started once the vacuum is cleaning', () => {
    expect(
      classifyStart({ requestedAt: T0, now: later, state: 'cleaning', stale: false })
    ).toBe('started');
  });

  // Regression: a failing Roborock integration 503s the status route, which
  // froze the old status-keyed check and suppressed the warning entirely. A
  // stale feed must read as "cannot confirm", never as a silent success and
  // never as a confident vacuum failure.
  it('reports unknown when the status feed is stale, whatever the last state said', () => {
    expect(
      classifyStart({ requestedAt: T0, now: later, state: 'docked', stale: true })
    ).toBe('unknown');
    expect(
      classifyStart({ requestedAt: T0, now: later, state: 'cleaning', stale: true })
    ).toBe('unknown');
  });

  it('reports unknown when no status has ever arrived', () => {
    expect(
      classifyStart({ requestedAt: T0, now: later, state: undefined, stale: false })
    ).toBe('unknown');
  });
});
