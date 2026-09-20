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
  factSentence,
  cutAtBoundary,
  isLabelLine,
  humanizeFactName,
  isStatusSummary,
  heroSentence,
  conditions,
  groupProposals,
  groupKey,
  proposalSentence,
  naggingLoops,
  activeLedgerKeys,
  usefulShare,
  freshnessTone,
  type AgentSummary,
  type LedgerKey,
  type Loop,
  type Proposal,
  type Status,
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

// --------------------------------------------------------------- fact text

describe('isLabelLine', () => {
  it('is true only for a line that ends in a colon', () => {
    expect(
      isLabelLine(
        'Health data from Garmin Forerunner 245 Music in VictoriaMetrics (Database_Name: irisgatan):',
      ),
    ).toBe(true);
    expect(isLabelLine('Källor:  ')).toBe(true);
    // A colon inside a line is part of a perfectly good sentence.
    expect(isLabelLine('Pooltemp: 26 °C sedan i morse.')).toBe(false);
  });
});

describe('cutAtBoundary', () => {
  it('returns the text untouched when it fits', () => {
    expect(cutAtBoundary('Poolen är 26 grader.', 120)).toBe('Poolen är 26 grader.');
  });

  it('cuts at a sentence end inside the budget, with no ellipsis', () => {
    const text = 'Poolen är 26 grader. Pumpen går mellan 10 och 14 varje dag i sommar.';
    expect(cutAtBoundary(text, 40)).toBe('Poolen är 26 grader.');
  });

  it('never splits a word', () => {
    const text = 'Hälsodata från Garmin Forerunner tvenne hundra fyrtiofem lagras i VictoriaMetrics';
    const cut = cutAtBoundary(text, 40);
    expect(cut.endsWith('…')).toBe(true);
    // Everything before the ellipsis is whole words.
    expect(text.startsWith(cut.slice(0, -1))).toBe(true);
    expect(cut.length).toBeLessThanOrEqual(41);
    expect(cut).not.toMatch(/\s…$/);
  });
});

describe('factSentence', () => {
  it('skips the opening label line and uses the substance beneath it', () => {
    // The shape write_fact actually produces, from the deployed wall.
    const body = [
      'Health data from Garmin Forerunner 245 Music in VictoriaMetrics (Database_Name: irisgatan):',
      '',
      'Vilopulsen ligger runt 52 slag per minut de senaste två veckorna.',
      'Mer detaljer finns i journalen.',
    ].join('\n');

    expect(factSentence(body)).toBe(
      'Vilopulsen ligger runt 52 slag per minut de senaste två veckorna.',
    );
  });

  it('skips markdown headings, rules and table rows', () => {
    const body = ['# Poolen', '---', '| a | b |', '', 'Pumpen går 10–14 varje dag.'].join('\n');
    expect(factSentence(body)).toBe('Pumpen går 10–14 varje dag.');
  });

  it('prefers a plain statement over a bullet that comes first', () => {
    const body = ['- roborock: dockad', '', 'Dammsugaren kör varje tisdag klockan nio.'].join('\n');
    expect(factSentence(body)).toBe('Dammsugaren kör varje tisdag klockan nio.');
  });

  it('falls back to a bullet when there is no plain statement', () => {
    expect(factSentence('* Dammsugaren kör varje tisdag.')).toBe('Dammsugaren kör varje tisdag.');
  });

  it('falls back to the label itself when the body is only a label', () => {
    expect(factSentence('Garmin-data i VictoriaMetrics:')).toBe('Garmin-data i VictoriaMetrics');
  });

  it('keeps a belief to roughly two wall lines', () => {
    const long = `Detta är en mycket lång brödtext ${'som bara fortsätter och fortsätter '.repeat(10)}slut.`;
    const out = factSentence(long);
    expect(out.length).toBeLessThanOrEqual(121);
    expect(out.endsWith('…')).toBe(true);
  });

  it('returns "" for an empty or structural body so the caller can fall back', () => {
    expect(factSentence('')).toBe('');
    expect(factSentence(undefined)).toBe('');
    expect(factSentence('---\n\n```\n```')).toBe('');
    expect(humanizeFactName('garmin_health.md')).toBe('Garmin health');
  });
});

// --------------------------------------------------------------- the hero

describe('isStatusSummary', () => {
  it('rejects the failure string the deployed wall used as its headline', () => {
    expect(isStatusSummary('no provider budget left')).toBe(true);
  });

  it('rejects other cycle status text', () => {
    for (const s of ['error: timeout', 'rate limit hit', 'HTTP 429', 'status=ok rounds=3', '']) {
      expect(isStatusSummary(s)).toBe(true);
    }
  });

  it('accepts a real sentence the model wrote', () => {
    expect(
      isStatusSummary('Jag har lagt till en påminnelse om att byta borsten på dammsugaren.'),
    ).toBe(false);
  });
});

describe('heroSentence', () => {
  const agents = [
    { name: 'brain', facts: 7 } as unknown as AgentSummary,
    { name: 'pool', facts: 3 } as unknown as AgentSummary,
  ];

  it('never puts a cycle failure in the hero — it counts instead', () => {
    expect(heroSentence('no provider budget left', agents, 2)).toBe(
      'Hjärnan håller 10 fakta om huset över 2 loopar, 2 förslag väntar på ditt ✅.',
    );
  });

  it('uses a real summary when there is one', () => {
    expect(heroSentence('Poolen är varm nog att bada i.', agents, 0)).toBe(
      'Poolen är varm nog att bada i.',
    );
  });

  it('says so when there are no agents at all', () => {
    expect(heroSentence('', [], 0)).toBe('Hjärnan har inte sagt något ännu.');
  });
});

describe('conditions', () => {
  const key = (over: Partial<LedgerKey>): LedgerKey => ({
    key: 'gemini:gemini-2.5-flash',
    requests_day: 0,
    tokens_day: 0,
    requests_limit: 1000,
    tokens_limit: 1_000_000,
    requests_remaining: 1,
    tokens_remaining: 1,
    consecutive_429: 0,
    blocked_until: null,
    disabled_until: null,
    recent_requests: 0,
    ...over,
  });

  const status = (keys: LedgerKey[]): Status =>
    ({ paused: false, ledger: { day: '2026-09-20', keys }, settings: {}, slack: {} }) as Status;

  it('surfaces "tom budget" when no key has anything left', () => {
    const out = conditions(status([key({ requests_remaining: 0 })]), []);
    expect(out.map((c) => c.label)).toContain('tom budget');
  });

  it('is empty on a healthy brain', () => {
    expect(conditions(status([key({})]), [])).toEqual([]);
  });

  it('reports no contact before anything else', () => {
    expect(conditions(null, [], true)[0].label).toBe('ingen kontakt');
  });
});

// --------------------------------------------------------------- proposals

describe('groupProposals', () => {
  const p = (id: string, text: string, kind = 'ha_todo_add'): Proposal =>
    ({
      id,
      kind,
      payload: { item: text },
      reason: '',
      topic: 'roborock',
      created: '2026-09-20T10:00:00Z',
      status: 'executed',
      result: 'ok',
    }) as Proposal;

  it('collapses the same action proposed three times into one line with a count', () => {
    const groups = groupProposals([
      p('c', 'Replace Roborock S6 MaxV main brush'),
      p('b', 'Replace Roborock S6 MaxV main brush.'),
      p('a', 'replace roborock s6 maxv main  brush'),
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].count).toBe(3);
    // The newest member is the one that renders.
    expect(groups[0].latest.id).toBe('c');
    expect(groups[0].sentence).toBe('Replace Roborock S6 MaxV main brush');
  });

  it('keeps different actions, and different executors, apart', () => {
    const groups = groupProposals([
      p('a', 'Byt borste'),
      p('b', 'Töm dammbehållaren'),
      p('c', 'Byt borste', 'sonos_say'),
    ]);
    expect(groups).toHaveLength(3);
  });

  it('falls back to the topic when the payload has no text', () => {
    const bare = { ...p('a', ''), payload: {} } as Proposal;
    expect(proposalSentence(bare)).toBe('roborock');
    expect(groupKey(bare)).toContain('roborock');
  });
});

// --------------------------------------------------------------- loops

describe('naggingLoops', () => {
  const loop = (topic: string, laps: number): Loop =>
    ({ topic, laps, pending: 0, approved: 0, rejected: 0, kinds: [], proposals: [] }) as unknown as Loop;

  it('drops single-lap topics — one proposal is not a loop', () => {
    const out = naggingLoops([
      loop('maintenance', 2),
      loop('roborock', 1),
      loop('vacuum-maintenance', 1),
    ]);
    expect(out.map((l) => l.topic)).toEqual(['maintenance']);
  });

  it('is empty rather than null when nothing qualifies, so the section hides', () => {
    expect(naggingLoops([loop('roborock', 1)])).toEqual([]);
    expect(naggingLoops(null)).toEqual([]);
  });

  it('puts the loudest loop first and caps the list', () => {
    const out = naggingLoops([loop('a', 2), loop('b', 9), loop('c', 4)], 2, 2);
    expect(out.map((l) => l.topic)).toEqual(['b', 'c']);
  });
});

describe('usefulShare', () => {
  it('is null for a loop that has never run', () => {
    expect(usefulShare({ total: 0, nothing: 0, note: 0, real: 0, repeat: 0 })).toBeNull();
  });

  it('counts notes and real work as useful', () => {
    expect(usefulShare({ total: 4, nothing: 2, note: 1, real: 1, repeat: 0 })).toBe(0.5);
  });
});

// --------------------------------------------------------------- ledger

describe('activeLedgerKeys', () => {
  const key = (over: Partial<LedgerKey>): LedgerKey => ({
    key: 'k',
    requests_day: 0,
    tokens_day: 0,
    requests_limit: 1_000_000,
    tokens_limit: 1_000_000,
    requests_remaining: 1,
    tokens_remaining: 1,
    consecutive_429: 0,
    blocked_until: null,
    disabled_until: null,
    recent_requests: 0,
    ...over,
  });

  it('drops the zero-traffic keys that wrapped the machine line', () => {
    const { active, silent } = activeLedgerKeys([
      key({ key: 'gemini:gemini-2.5-flash', requests_day: 12 }),
      key({ key: 'qwen3.8:27b-mlx' }),
      key({ key: 'qwen3-coder:30b' }),
    ]);
    expect(active.map((k) => k.key)).toEqual(['gemini:gemini-2.5-flash']);
    expect(silent).toBe(2);
  });

  it('keeps a silent key that is blocked or has been 429ing', () => {
    const { active } = activeLedgerKeys([
      key({ key: 'blocked', blocked_until: 123 }),
      key({ key: 'angry', consecutive_429: 3 }),
      key({ key: 'quiet' }),
    ]);
    expect(active.map((k) => k.key)).toEqual(['blocked', 'angry']);
  });
});

describe('freshnessTone', () => {
  const now = 1_000_000;
  it('greens a fact written today and reddens one older than a week', () => {
    expect(freshnessTone(now - 60, now)).toBe('ok');
    expect(freshnessTone(now - 3 * 86_400, now)).toBe('warn');
    expect(freshnessTone(now - 30 * 86_400, now)).toBe('error');
    expect(freshnessTone(null, now)).toBe('idle');
  });
});
