import { describe, it, expect } from 'vitest';
import {
  parseTargets,
  parseStatus,
  isAllowedTarget,
  shouldRetryTargets,
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
    expect(isAllowedTarget(targets, 'automation.stada')).toBe(false);
  });
});
