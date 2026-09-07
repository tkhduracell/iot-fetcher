import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  getJson,
  fetchJournal,
  fetchFact,
  statusTone,
  formatAgo,
  formatIn,
  parseJournalLine,
} from './aiBrain';

function stubFetch(resp: { ok?: boolean; status?: number; body?: unknown; text?: string }) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: resp.ok ?? true,
    status: resp.status ?? 200,
    json: async () => {
      if (resp.text !== undefined) throw new Error('not json');
      return resp.body;
    },
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('getJson', () => {
  it('returns the parsed body and routes through the proxy prefix', async () => {
    const fetchMock = stubFetch({ body: { agents: [] } });

    expect(await getJson('api/agents')).toEqual({ agents: [] });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ai-brain/api/agents');
  });

  it("throws the upstream error message, not a bare status", async () => {
    stubFetch({ ok: false, status: 404, body: { error: 'unknown agent: nope' } });

    await expect(getJson('api/agents/nope')).rejects.toThrow('unknown agent: nope');
  });

  it('falls back to the status code when the error body has no message', async () => {
    stubFetch({ ok: false, status: 502, text: '<html>bad gateway</html>' });

    await expect(getJson('api/status')).rejects.toThrow('HTTP 502');
  });

  it('rejects a 200 that is not JSON rather than returning null', async () => {
    stubFetch({ ok: true, status: 200, text: 'not json' });

    await expect(getJson('api/status')).rejects.toThrow(/Ogiltigt svar/);
  });
});

describe('fetch helpers', () => {
  it('passes the day count as a query parameter', async () => {
    const fetchMock = stubFetch({ body: { agent: 'brain', days: 7, entries: [] } });

    await fetchJournal('brain', 7);

    expect(fetchMock.mock.calls[0][0]).toBe('/api/ai-brain/api/agents/brain/journal?days=7');
  });

  it('encodes agent and fact names so a slash cannot forge a path', async () => {
    const fetchMock = stubFetch({ body: { agent: 'a/b', name: 'c d', body: '' } });

    await fetchFact('a/b', 'c d');

    expect(fetchMock.mock.calls[0][0]).toBe('/api/ai-brain/api/agents/a%2Fb/facts/c%20d');
  });
});

describe('statusTone', () => {
  it.each([
    ['ok', 'ok'],
    ['done', 'ok'],
    ['paused', 'warn'],
    ['skipped', 'warn'],
    ['error', 'error'],
    ['timeout', 'error'],
    ['running', 'busy'],
  ])('maps %s to %s', (status, tone) => {
    expect(statusTone(status)).toBe(tone);
  });

  it('treats null and anything unrecognised as idle', () => {
    expect(statusTone(null)).toBe('idle');
    expect(statusTone(undefined)).toBe('idle');
    expect(statusTone('something-new')).toBe('idle');
  });
});

describe('formatAgo', () => {
  const now = 1_000_000;

  it.each([
    [now - 5, '5 s sedan'],
    [now - 90, '1 min sedan'],
    [now - 3600 * 3, '3 h sedan'],
    [now - 86400 * 2, '2 d sedan'],
  ])('formats %i as %s', (at, expected) => {
    expect(formatAgo(at, now)).toBe(expected);
  });

  it('shows a dash when the agent has never run', () => {
    expect(formatAgo(null, now)).toBe('–');
  });

  it('does not print a negative age for a clock skew into the future', () => {
    expect(formatAgo(now + 30, now)).toBe('nyss');
  });
});

describe('formatIn', () => {
  const now = 1_000_000;

  it.each([
    [now + 30, 'om 30 s'],
    [now + 120, 'om 2 min'],
    [now + 3600 * 4, 'om 4 h'],
  ])('formats %i as %s', (at, expected) => {
    expect(formatIn(at, now)).toBe(expected);
  });

  it('reads "nu" once the wake time has passed, so the countdown settles', () => {
    expect(formatIn(now, now)).toBe('nu');
    expect(formatIn(now - 60, now)).toBe('nu');
  });

  it('shows a dash for an unknown wake time', () => {
    expect(formatIn(null, now)).toBe('–');
  });
});

describe('parseJournalLine', () => {
  it('splits the timestamp off at the first double space', () => {
    expect(parseJournalLine('12:04:31  cycle ok, 3 rounds')).toEqual({
      time: '12:04:31',
      text: 'cycle ok, 3 rounds',
    });
  });

  it('keeps single spaces inside the text intact', () => {
    expect(parseJournalLine('12:04:31  wrote fact a b c').text).toBe('wrote fact a b c');
  });

  it('splits only on the first double space, leaving later ones in the text', () => {
    expect(parseJournalLine('12:04:31  a  b').text).toBe('a  b');
  });

  it('treats a line with no double space as text only', () => {
    expect(parseJournalLine('no timestamp here')).toEqual({
      time: null,
      text: 'no timestamp here',
    });
  });
});
