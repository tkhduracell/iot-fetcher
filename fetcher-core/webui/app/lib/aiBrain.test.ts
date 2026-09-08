import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  getJson,
  fetchJournal,
  fetchFact,
  statusTone,
  formatAgo,
  formatIn,
  parseJournalLine,
  quotaLabel,
  quotaTone,
  shortModel,
  quotaCounts,
  compactTokens,
  formatUptime,
  truncate,
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

describe('quotaLabel', () => {
  it('renders used of limit, not used of remaining', () => {
    expect(quotaLabel(12, 1500)).toBe('12 av 1\u00a0500');
  });

  it('shows a dash for the denominator when there is no limit', () => {
    expect(quotaLabel(12, 0)).toBe('12 av –');
  });

  it('survives a missing limit from an older ai-brain', () => {
    expect(quotaLabel(3, undefined as unknown as number)).toBe('3 av –');
    expect(quotaLabel(undefined as unknown as number, 10)).toBe('0 av 10');
  });

  it('renders a fully spent day as used equal to limit', () => {
    expect(quotaLabel(1500, 1500)).toBe('1\u00a0500 av 1\u00a0500');
  });
});

describe('quotaTone', () => {
  it('is green above 40% remaining', () => {
    expect(quotaTone(1)).toBe('ok');
    expect(quotaTone(0.41)).toBe('ok');
  });

  it('is yellow between 15% and 40%', () => {
    expect(quotaTone(0.4)).toBe('warn');
    expect(quotaTone(0.16)).toBe('warn');
  });

  it('is red at or below 15%', () => {
    expect(quotaTone(0.15)).toBe('error');
    expect(quotaTone(0)).toBe('error');
  });

  it('treats a non-numeric fraction as spent rather than full', () => {
    expect(quotaTone(NaN)).toBe('error');
  });
});

describe('shortModel', () => {
  it('strips the provider prefix and its repeat inside the model name', () => {
    expect(shortModel('gemini:gemini-2.5-flash-lite')).toBe('2.5-flash-lite');
    expect(shortModel('gemini:gemini-3.5-flash')).toBe('3.5-flash');
  });

  it('strips a bare provider prefix when the model does not repeat it', () => {
    expect(shortModel('gemini:models/foo')).toBe('models/foo');
    expect(shortModel('openai:gpt-4o')).toBe('gpt-4o');
  });

  it('leaves an unqualified key alone', () => {
    expect(shortModel('flash-lite')).toBe('flash-lite');
  });

  it('keeps the provider when nothing follows the colon', () => {
    expect(shortModel('gemini:')).toBe('gemini');
  });

  it('shows a dash for a missing key rather than an empty cell', () => {
    expect(shortModel('')).toBe('–');
    expect(shortModel(null)).toBe('–');
    expect(shortModel(undefined)).toBe('–');
  });
});

describe('quotaCounts', () => {
  it('renders used over limit', () => {
    expect(quotaCounts(11, 200)).toBe('11/200');
  });

  it('shows a dash denominator when the limit is unknown', () => {
    expect(quotaCounts(11, 0)).toBe('11/–');
    expect(quotaCounts(11, undefined as unknown as number)).toBe('11/–');
  });

  it('treats a missing numerator as zero', () => {
    expect(quotaCounts(undefined as unknown as number, 200)).toBe('0/200');
  });
});

describe('compactTokens', () => {
  it.each([
    [0, '0'],
    [999, '999'],
    [1000, '1k'],
    [74_321, '74k'],
    [1_500_000, '1.5M'],
    [2_000_000, '2M'],
  ])('formats %i as %s', (n, expected) => {
    expect(compactTokens(n)).toBe(expected);
  });

  it('does not crash on a non-numeric spend', () => {
    expect(compactTokens(NaN)).toBe('0');
  });
});

describe('formatUptime', () => {
  it.each([
    [90, '1 min'],
    [3600 * 5 + 120, '5 h 2 min'],
    [86400 * 2 + 3600 * 3, '2 d 3 h'],
  ])('formats %i as %s', (s, expected) => {
    expect(formatUptime(s)).toBe(expected);
  });

  it('shows a dash rather than a negative uptime', () => {
    expect(formatUptime(-1)).toBe('–');
    expect(formatUptime(NaN)).toBe('–');
  });
});

describe('truncate', () => {
  it('leaves a short value untouched and unflagged', () => {
    expect(truncate('hello', 160)).toEqual({ text: 'hello', truncated: false });
  });

  it('cuts at the limit and flags that there is more', () => {
    const long = 'x'.repeat(200);
    const out = truncate(long, 160);
    expect(out.text).toHaveLength(160);
    expect(out.truncated).toBe(true);
  });

  it('does not flag a value exactly at the limit', () => {
    expect(truncate('y'.repeat(160), 160).truncated).toBe(false);
  });

  it('defaults to 160 characters', () => {
    expect(truncate('z'.repeat(300)).text).toHaveLength(160);
  });
});
