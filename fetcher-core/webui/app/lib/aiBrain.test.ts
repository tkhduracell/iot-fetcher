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
  modelAvailability,
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
  bodyEqualsTitle,
  factQualityScore,
  verdictLabel,
  verdictTone,
  isDistrustedReview,
  modelStateLabel,
  lastRunLabel,
  normaliseSessionTopic,
  groupSlackSessions,
  summarizeArgs,
  decodeEscapes,
  splitPreviewSuffix,
  splitLongFields,
  parseResultPreview,
  summarizeResult,
  type AgentSummary,
  type FactStat,
  type LanState,
  type LedgerKey,
  type Loop,
  type Proposal,
  type Review,
  type SlackSession,
  type Status,
  type ToolResult,
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

describe('modelAvailability', () => {
  const now = 1_000;

  it('is available for a cloud key with no ledger entry', () => {
    expect(modelAvailability('gemini:gemini-3.8-flash', [], null, now)).toBe('available');
  });

  it('is blocked when the ledger entry is blocked_until in the future', () => {
    const keys: LedgerKey[] = [
      {
        key: 'gemini:gemini-3.8-flash',
        requests_day: 3,
        tokens_day: 300,
        requests_limit: 5,
        tokens_limit: 5000,
        requests_remaining: 0.4,
        tokens_remaining: 0.9,
        consecutive_429: 3,
        blocked_until: now + 3600,
        disabled_until: null,
        recent_requests: 0,
      },
    ];
    expect(modelAvailability('gemini:gemini-3.8-flash', keys, null, now)).toBe('blocked');
  });

  it('is available once blocked_until has passed', () => {
    const keys: LedgerKey[] = [
      {
        key: 'gemini:gemini-3.8-flash',
        requests_day: 3,
        tokens_day: 300,
        requests_limit: 5,
        tokens_limit: 5000,
        requests_remaining: 0.4,
        tokens_remaining: 0.9,
        consecutive_429: 3,
        blocked_until: now - 1,
        disabled_until: null,
        recent_requests: 0,
      },
    ];
    expect(modelAvailability('gemini:gemini-3.8-flash', keys, null, now)).toBe('available');
  });

  it('is no_lan_host for a lan: entry when the finder found nothing', () => {
    const lanHost: LanState = {
      enabled: true,
      hosts: [{ model: 'qwen3-coder:30b', host: null, found_at: null, subnets: [] }],
    };
    expect(modelAvailability('lan:qwen3-coder:30b', [], lanHost, now)).toBe('no_lan_host');
  });

  it('is no_lan_host for a lan: entry with no lan_host data at all', () => {
    expect(modelAvailability('lan:qwen3-coder:30b', [], null, now)).toBe('no_lan_host');
  });

  it('is available for a lan: entry the finder actually found', () => {
    const lanHost: LanState = {
      enabled: true,
      hosts: [
        {
          model: 'qwen3-coder:30b',
          host: 'http://192.168.68.69:11434',
          found_at: now - 60,
          subnets: [],
        },
      ],
    };
    expect(modelAvailability('lan:qwen3-coder:30b', [], lanHost, now)).toBe('available');
  });

  it('is unaffected by an unmetered lan: key that the ledger never blocks', () => {
    // lan: providers are UNMETERED (limits_from_settings), so a ledger check
    // alone would call this "available" even with no host -- the bug this
    // helper exists to fix.
    const keys: LedgerKey[] = [
      {
        key: 'lan:qwen3-coder:30b',
        requests_day: 40,
        tokens_day: 40000,
        requests_limit: 10000,
        tokens_limit: 100000000,
        requests_remaining: 0.996,
        tokens_remaining: 0.9996,
        consecutive_429: 0,
        blocked_until: null,
        disabled_until: null,
        recent_requests: 2,
      },
    ];
    expect(modelAvailability('lan:qwen3-coder:30b', keys, null, now)).toBe('no_lan_host');
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

  it('drops the denominator entirely for an unmetered key regardless of its limit', () => {
    // A lan: key carries UNMETERED's real (huge) Limits, not 0 -- unmetered
    // must win over the numeric limit or this would still print "2/1000000",
    // and even "2/∞" reads as a budget when there isn't one.
    expect(quotaCounts(2, 1_000_000, true)).toBe('2');
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

  it('collapses the three brush variants the deployed brain actually wrote', () => {
    // Verbatim from the wall on the tablet. They differ by a parenthetical
    // countdown and by word order, which an exact-match key does not survive --
    // the first version of this grouping shipped green and still printed three
    // lines here.
    const groups = groupProposals([
      p('c', 'Replace main brush on Roborock S6 MaxV vacuum cleaner (time left: 6.1 hours)'),
      p('b', 'Replace Roborock S6 MaxV main brush'),
      p('a', 'Replace Roborock S6 MaxV main brush (time left: 6.1 hours)'),
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].count).toBe(3);
  });

  it('keeps two different parts of the same machine apart', () => {
    // The near-miss that decides the threshold: these share every word but one,
    // and they are two different errands.
    const groups = groupProposals([
      p('a', 'Replace Roborock S6 MaxV main brush'),
      p('b', 'Replace Roborock S6 MaxV side brush'),
      p('c', 'Empty the Roborock S6 MaxV dust bin'),
    ]);
    expect(groups).toHaveLength(3);
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

// ------------------------------------------------------ wall fact ranking

describe('bodyEqualsTitle', () => {
  it('drops a fact whose body is just its title restated', () => {
    expect(bodyEqualsTitle('Pooltemperatur', 'Pooltemperatur')).toBe(true);
    expect(bodyEqualsTitle('Pooltemperatur', 'pooltemperatur.')).toBe(true);
    expect(bodyEqualsTitle('Pooltemperatur', '  Pooltemperatur  ')).toBe(true);
  });

  it('keeps a fact whose body says something beyond the title', () => {
    expect(bodyEqualsTitle('Pooltemperatur', 'Pooltemperatur är 26 grader.')).toBe(false);
  });

  it('is false with no body yet, rather than treating absence as a match', () => {
    expect(bodyEqualsTitle('Pooltemperatur', undefined)).toBe(false);
    expect(bodyEqualsTitle('Pooltemperatur', '')).toBe(false);
  });
});

describe('factQualityScore', () => {
  const now = 1_000_000;
  const stat = (over: Partial<FactStat>): FactStat => ({
    name: 'f',
    title: 'F',
    written_at: now,
    first_written_at: now,
    writes: 1,
    ...over,
  });

  it('ranks a fact reaffirmed many times over a fresh single write', () => {
    const oftenWritten = factQualityScore(stat({ writes: 8, written_at: now - 5 * 86_400 }), now, null);
    const freshOnce = factQualityScore(stat({ writes: 1, written_at: now }), now, null);
    expect(oftenWritten).toBeGreaterThan(freshOnce);
  });

  it('scores a fresher fact above an equally-written stale one', () => {
    const fresh = factQualityScore(stat({ writes: 2, written_at: now }), now, null);
    const stale = factQualityScore(stat({ writes: 2, written_at: now - 20 * 86_400 }), now, null);
    expect(fresh).toBeGreaterThan(stale);
  });

  it('demotes a fact whose expert was last reviewed wrong or stale', () => {
    const trusted = factQualityScore(stat({ writes: 3 }), now, { ts: now, verdict: 'good', findings: '' });
    const distrusted = factQualityScore(stat({ writes: 3 }), now, { ts: now, verdict: 'wrong', findings: '' });
    expect(distrusted).toBeLessThan(trusted);
  });

  it('does not zero out a distrusted expert entirely', () => {
    const distrusted = factQualityScore(stat({ writes: 3 }), now, { ts: now, verdict: 'stale', findings: '' });
    expect(distrusted).toBeGreaterThan(0);
  });
});

// ------------------------------------------------------- review verdicts

describe('verdictLabel / verdictTone / isDistrustedReview', () => {
  it('labels every verdict in Swedish', () => {
    expect(verdictLabel('good')).toBe('bra');
    expect(verdictLabel('wrong')).toBe('fel');
    expect(verdictLabel(undefined)).toBe('ogranskad');
  });

  it('tones good as ok, wrong as error, and the rest as warn', () => {
    expect(verdictTone('good')).toBe('ok');
    expect(verdictTone('wrong')).toBe('error');
    expect(verdictTone('stale')).toBe('warn');
    expect(verdictTone('repetitive')).toBe('warn');
    expect(verdictTone('off_goal')).toBe('warn');
    expect(verdictTone(undefined)).toBe('idle');
  });

  it('treats only wrong and stale as distrusted', () => {
    expect(isDistrustedReview({ ts: 0, verdict: 'wrong', findings: '' })).toBe(true);
    expect(isDistrustedReview({ ts: 0, verdict: 'stale', findings: '' })).toBe(true);
    expect(isDistrustedReview({ ts: 0, verdict: 'good', findings: '' })).toBe(false);
    expect(isDistrustedReview({ ts: 0, verdict: 'repetitive', findings: '' })).toBe(false);
    expect(isDistrustedReview(null)).toBe(false);
    expect(isDistrustedReview(undefined)).toBe(false);
  });
});

// -------------------------------------------------- agent header labels

describe('modelStateLabel', () => {
  it('prefers the last cycle model whenever one exists', () => {
    expect(modelStateLabel('gemini:gemini-2.5-flash', false, null, true)).toBe('2.5-flash');
    expect(modelStateLabel('gemini:gemini-2.5-flash', true, 'other', true)).toBe('2.5-flash');
  });

  it('says a cycle is in progress, with its model when the trace has one', () => {
    expect(modelStateLabel(null, true, 'gemini:gemini-2.5-flash', true)).toBe('pågår · 2.5-flash');
    expect(modelStateLabel(null, true, null, true)).toBe('pågår');
  });

  it('says "okänt sedan omstart" rather than "ingen modell" when the journal has history', () => {
    expect(modelStateLabel(null, false, null, true)).toBe('okänt sedan omstart');
  });

  it('only claims no model at all when nothing backs up any history', () => {
    expect(modelStateLabel(null, false, null, false)).toBe('ingen modell');
  });
});

describe('lastRunLabel', () => {
  const now = 1_000_000;

  it('prefers a real last_cycle_at over every other case', () => {
    expect(lastRunLabel(now - 120, now, true, true)).toBe('senast 2 min sedan');
  });

  it('says a cycle is running now rather than "aldrig kört"', () => {
    expect(lastRunLabel(null, now, true, true)).toBe('pågår nu');
    expect(lastRunLabel(undefined, now, true, false)).toBe('pågår nu');
  });

  it('says "okänt sedan omstart" for a loop with journal history but no last_cycle_at', () => {
    expect(lastRunLabel(null, now, false, true)).toBe('okänt sedan omstart');
  });

  it('only says "aldrig kört" when the journal backs up that claim', () => {
    expect(lastRunLabel(null, now, false, false)).toBe('aldrig kört');
  });
});

// ---------------------------------------------------------- slack sessions

describe('normaliseSessionTopic', () => {
  it('lowercases and dash-separates', () => {
    expect(normaliseSessionTopic('Pool Pump Schedule')).toBe('pool-pump-schedule');
    expect(normaliseSessionTopic('pool_pump_schedule')).toBe('pool-pump-schedule');
  });

  it('strips a trailing date so daily re-asks collapse to one topic', () => {
    expect(normaliseSessionTopic('pool-pump-schedule-2026-09-20')).toBe('pool-pump-schedule');
    expect(normaliseSessionTopic('pool-pump-schedule-20260920')).toBe('pool-pump-schedule');
  });

  it('strips more than one trailing date', () => {
    expect(normaliseSessionTopic('roborock-brush-2026-09-19-2026-09-20')).toBe('roborock-brush');
  });

  it('falls back to a placeholder for an empty topic', () => {
    expect(normaliseSessionTopic('')).toBe('okänt-ämne');
  });
});

describe('groupSlackSessions', () => {
  const session = (over: Partial<SlackSession>): SlackSession => ({
    topic: 't',
    thread_ts: '1.1',
    channel: 'C1',
    status: 'closed',
    ...over,
  });

  it('folds same-topic sessions (even re-dated ones) into one group', () => {
    const groups = groupSlackSessions([
      session({ topic: 'pool-pump-schedule-2026-09-19', channel: 'C1', thread_ts: '1' }),
      session({ topic: 'pool-pump-schedule-2026-09-20', channel: 'C2', thread_ts: '2' }),
      session({ topic: 'roborock-brush', channel: 'C3', thread_ts: '3' }),
    ]);
    expect(groups).toHaveLength(2);
    const pool = groups.find((g) => g.topic === 'pool-pump-schedule');
    expect(pool?.sessions).toHaveLength(2);
  });

  it('orders groups by member count, largest first', () => {
    const groups = groupSlackSessions([
      session({ topic: 'a' }),
      session({ topic: 'b' }),
      session({ topic: 'b' }),
    ]);
    expect(groups.map((g) => g.topic)).toEqual(['b', 'a']);
  });

  it('marks a group open when any member session is still open', () => {
    const groups = groupSlackSessions([
      session({ topic: 'x', status: 'closed' }),
      session({ topic: 'x', status: 'open' }),
    ]);
    expect(groups[0].open).toBe(true);
  });

  it('handles an empty or missing session list', () => {
    expect(groupSlackSessions([])).toEqual([]);
    expect(groupSlackSessions(null)).toEqual([]);
    expect(groupSlackSessions(undefined)).toEqual([]);
  });
});

// ---------------------------------------------------------- summarizeArgs

describe('summarizeArgs', () => {
  it('formats code_read as path:start–end', () => {
    expect(summarizeArgs('code_read', { path: 'pool-pump-planner/vm.go', start: '200', end: '250' })).toBe(
      'pool-pump-planner/vm.go:200–250',
    );
  });

  it('formats code_read with only start as path:start', () => {
    expect(summarizeArgs('code_read', { path: 'a.py', start: '10' })).toBe('a.py:10');
  });

  it('formats code_read with no start as bare path', () => {
    expect(summarizeArgs('code_read', { path: 'a.py' })).toBe('a.py');
  });

  it('formats code_read with end <= start as path:start', () => {
    expect(summarizeArgs('code_read', { path: 'a.py', start: '10', end: '10' })).toBe('a.py:10');
    expect(summarizeArgs('code_read', { path: 'a.py', start: '10', end: '0' })).toBe('a.py:10');
  });

  it('formats code_grep as /pattern/ in path', () => {
    expect(summarizeArgs('code_grep', { pattern: 'TODO', path: 'fetcher-core' })).toBe(
      '/TODO/ in fetcher-core',
    );
  });

  it('formats code_grep with no path as bare /pattern/', () => {
    expect(summarizeArgs('code_grep', { pattern: 'TODO' })).toBe('/TODO/');
  });

  it('formats vm_query as promql plus range', () => {
    expect(summarizeArgs('vm_query', { promql: 'up', range_minutes: '60' })).toBe('up 60min');
  });

  it('formats vm_query with no range as bare promql', () => {
    expect(summarizeArgs('vm_query', { promql: 'up' })).toBe('up');
  });

  it('formats read_expert as name/what/fact', () => {
    expect(summarizeArgs('read_expert', { name: 'pool', what: 'fact', fact: 'wattage' })).toBe(
      'pool/fact/wattage',
    );
    expect(summarizeArgs('read_expert', { name: 'pool', what: 'journal' })).toBe('pool/journal');
  });

  it('formats ha_context as name', () => {
    expect(summarizeArgs('ha_context', { name: 'climate.spa' })).toBe('climate.spa');
  });

  it('formats write_fact as name', () => {
    expect(summarizeArgs('write_fact', { name: 'pool.wattage', title: 't', body: 'b' })).toBe(
      'pool.wattage',
    );
  });

  it('formats send_note as to', () => {
    expect(summarizeArgs('send_note', { to: 'pool-pump-expert', body: 'hej' })).toBe('pool-pump-expert');
  });

  it('formats end_cycle as minutes', () => {
    expect(summarizeArgs('end_cycle', { next_wake_minutes: '30', summary: 'done' })).toBe('30 min');
  });

  it('falls back to k=v pairs for an unknown tool', () => {
    expect(summarizeArgs('web_search', { query: 'iot fetcher' })).toBe('query=iot fetcher');
  });

  it('truncates a long fallback value with an ellipsis', () => {
    const long = 'x'.repeat(50);
    expect(summarizeArgs('web_search', { query: long })).toContain('…');
    expect(summarizeArgs('web_search', { query: long }).length).toBeLessThanOrEqual(72);
  });

  it('handles missing/undefined args without throwing', () => {
    expect(summarizeArgs('code_read', undefined)).toBe('');
    expect(summarizeArgs('unknown_tool', {})).toBe('');
  });
});

// ---------------------------------------------------------- decodeEscapes

describe('decodeEscapes', () => {
  it('turns literal \\n into a real newline', () => {
    expect(decodeEscapes('line1\\nline2')).toBe('line1\nline2');
  });

  it('turns literal \\t into a real tab', () => {
    expect(decodeEscapes('a\\tb')).toBe('a\tb');
  });

  it('turns literal \\r\\n into a single newline', () => {
    expect(decodeEscapes('a\\r\\nb')).toBe('a\nb');
  });

  it('unescapes quotes and backslashes', () => {
    expect(decodeEscapes('say \\"hi\\"')).toBe('say "hi"');
    expect(decodeEscapes('a\\\\b')).toBe('a\\b');
  });

  it('leaves text with no escapes unchanged', () => {
    expect(decodeEscapes('plain text')).toBe('plain text');
  });

  it('handles an empty string', () => {
    expect(decodeEscapes('')).toBe('');
  });

  it('keeps an escaped backslash followed by a literal n as \\n, not a real newline', () => {
    // Source code containing the two characters `\` and `n` (e.g. a string
    // literal `"\n"` inside a code_read body) comes back from json.dumps as
    // four characters: \, \, \, n -- i.e. the JS string '\\\\n'. A naive
    // sequential-replace decoder resolves the \n half first and turns this
    // into a backslash plus a real newline; the single-pass version must
    // instead consume the \\ as one escape and leave the following n alone.
    expect(decodeEscapes('a\\\\nb')).toBe('a\\nb');
  });

  it('decodes a \\uXXXX escape to its character', () => {
    expect(decodeEscapes('sm\\u00e5 \\u00e4pplen')).toBe('små äpplen');
  });

  it('decodes a mixed code snippet: real newlines, an escaped backslash-n, and non-ASCII', () => {
    const input = 'def f():\\n    s = "\\\\n"  # kommentar om \\u00e5\\u00e4\\u00f6\\n    return s';
    const expected = 'def f():\n    s = "\\n"  # kommentar om åäö\n    return s';
    expect(decodeEscapes(input)).toBe(expected);
  });
});

// ------------------------------------------------------ splitPreviewSuffix

describe('splitPreviewSuffix', () => {
  it('splits a truncated preview from its …[+N] suffix', () => {
    const { body, droppedChars } = splitPreviewSuffix('{"ok":true,"body":"abc"…[+1479]');
    expect(body).toBe('{"ok":true,"body":"abc"');
    expect(droppedChars).toBe(1479);
  });

  it('returns the whole string with null droppedChars when not truncated', () => {
    const { body, droppedChars } = splitPreviewSuffix('{"ok":true}');
    expect(body).toBe('{"ok":true}');
    expect(droppedChars).toBeNull();
  });

  it('handles an empty string', () => {
    expect(splitPreviewSuffix('')).toEqual({ body: '', droppedChars: null });
  });
});

// ------------------------------------------------------ parseResultPreview

describe('parseResultPreview', () => {
  it('parses a well-formed JSON object result', () => {
    const r = parseResultPreview('{"ok":true,"result":"hej"}');
    expect(r.parsed).toBe(true);
    expect(r.json).toEqual({ ok: true, result: 'hej' });
    expect(r.droppedChars).toBeNull();
  });

  it('parses an error result', () => {
    const r = parseResultPreview('{"error":"unknown tool: foo"}');
    expect(r.parsed).toBe(true);
    expect(r.json).toEqual({ error: 'unknown tool: foo' });
  });

  it('falls back to unparsed when truncation cuts mid-token', () => {
    const r = parseResultPreview('{"ok":true,"body":"abc…[+50]');
    expect(r.parsed).toBe(false);
    expect(r.json).toBeNull();
    expect(r.droppedChars).toBe(50);
  });

  it('falls back to unparsed for non-JSON text', () => {
    const r = parseResultPreview('not json at all');
    expect(r.parsed).toBe(false);
    expect(r.json).toBeNull();
  });

  it('handles an empty string', () => {
    const r = parseResultPreview('');
    expect(r.parsed).toBe(false);
    expect(r.json).toBeNull();
  });
});

// ------------------------------------------------------------ splitLongFields

describe('splitLongFields', () => {
  it('treats a real newline in a parsed string as long, not the two-char sequence', () => {
    // This is the exact shape JSON.parse leaves behind: a genuine multi-line
    // string, one real newline character, never a literal backslash-n pair.
    const { long, short } = splitLongFields({ output: 'line1\nline2', name: 'ok' });
    expect(long).toEqual([['output', 'line1\nline2']]);
    expect(short).toEqual([['name', 'ok']]);
  });

  it('does not match a literal two-character \\n sequence as a newline', () => {
    // A string that (unusually) contains the literal characters backslash
    // and n, but no real newline -- must NOT be treated as long by name
    // alone (it is not one of LONG_TEXT_FIELDS).
    const { long, short } = splitLongFields({ note: 'a\\nb' });
    expect(long).toEqual([]);
    expect(short).toEqual([['note', 'a\\nb']]);
  });

  it('treats known long field names as long even without a newline', () => {
    const { long, short } = splitLongFields({ body: 'short one-liner', start: 1 });
    expect(long).toEqual([['body', 'short one-liner']]);
    expect(short).toEqual([['start', 1]]);
  });

  it('keeps original key order within each group', () => {
    const { short } = splitLongFields({ path: 'a.py', start: 1, end: 51, total_lines: 51 });
    expect(short.map(([k]) => k)).toEqual(['path', 'start', 'end', 'total_lines']);
  });

  it('handles an object with no long fields', () => {
    const { long, short } = splitLongFields({ written: 'pool.wattage' });
    expect(long).toEqual([]);
    expect(short).toEqual([['written', 'pool.wattage']]);
  });

  it('handles an empty object', () => {
    expect(splitLongFields({})).toEqual({ long: [], short: [] });
  });
});

// ----------------------------------------------------------- summarizeResult

function result(name: string, previewObj: unknown, suffix = ''): ToolResult {
  return { name, result_preview: JSON.stringify(previewObj) + suffix };
}

describe('summarizeResult', () => {
  it('reads code_read as a line count', () => {
    const r = summarizeResult(
      'code_read',
      result('code_read', { path: 'a.py', start: 1, end: 51, total_lines: 51, body: Array(51).fill('x').join('\n') }),
    );
    expect(r.ok).toBe(true);
    expect(r.size).toBe('51 rader');
  });

  it('reads code_grep as a hit count', () => {
    const r = summarizeResult(
      'code_grep',
      result('code_grep', { hits: [{ path: 'a.py', line: 1, text: 'x' }, { path: 'b.py', line: 2, text: 'y' }], truncated: false }),
    );
    expect(r.ok).toBe(true);
    expect(r.size).toBe('2 träffar');
  });

  it('reads vm_query as a series count', () => {
    const r = summarizeResult('vm_query', result('vm_query', { series: [{ metric: {}, values: [] }, { metric: {}, values: [] }, { metric: {}, values: [] }], truncated: false }));
    expect(r.ok).toBe(true);
    expect(r.size).toBe('3 serier');
  });

  it('reads an error result as not ok, with the message as status', () => {
    const r = summarizeResult('code_read', result('code_read', { error: 'code_read: no such file: x.py' }));
    expect(r.ok).toBe(false);
    expect(r.status).toContain('no such file');
  });

  it('falls back to a byte-size hint for a tool with no dedicated shape', () => {
    const r = summarizeResult('write_fact', result('write_fact', { written: 'pool.wattage' }));
    expect(r.ok).toBe(true);
    expect(r.size).toMatch(/tecken|kB/);
  });

  it('mentions dropped characters when the preview was truncated', () => {
    const preview = '{"ok":true,"body":"' + 'x'.repeat(30) + '…[+1479]';
    const r = summarizeResult('write_fact', { name: 'write_fact', result_preview: preview });
    expect(r.size).toContain('1479 tecken trunkerade');
  });

  it('renders a large body as kB rather than raw character count', () => {
    const big = 'x'.repeat(2000);
    const r = summarizeResult('ha_context', result('ha_context', { context: big, truncated: false }));
    expect(r.size).toMatch(/kB$/);
  });

  it('reads a truncated error result as not ok, best-effort extracting the message', () => {
    // A real err() result cut mid-message by the 500-char preview cap: the
    // JSON never closes, so this does not JSON.parse, but it still starts
    // with the same {"error": "..." prefix every err() writes.
    const preview = '{"error": "code_read: ' + 'x'.repeat(500) + '…[+37]';
    const r = summarizeResult('code_read', { name: 'code_read', result_preview: preview });
    expect(r.ok).toBe(false);
    expect(r.status).toContain('code_read:');
  });

  it('does not misread a normal truncated success body as an error', () => {
    const preview = '{"ok":true,"body":"' + 'x'.repeat(500) + '…[+37]';
    const r = summarizeResult('code_read', { name: 'code_read', result_preview: preview });
    expect(r.ok).toBe(true);
  });

  it('prefers stats.lines over parsing the preview when present', () => {
    const r = summarizeResult('code_read', {
      name: 'code_read',
      result_preview: result('code_read', { path: 'a.py', body: 'irrelevant' }).result_preview,
      stats: { ok: true, lines: 999 },
    });
    expect(r.ok).toBe(true);
    expect(r.size).toBe('999 rader');
  });

  it('prefers stats.hits and stats.series the same way', () => {
    const hitsResult = summarizeResult('code_grep', {
      name: 'code_grep',
      result_preview: '{}',
      stats: { ok: true, hits: 12 },
    });
    expect(hitsResult.size).toBe('12 träffar');

    const seriesResult = summarizeResult('vm_query', {
      name: 'vm_query',
      result_preview: '{}',
      stats: { ok: true, series: 3 },
    });
    expect(seriesResult.size).toBe('3 serier');
  });

  it('reads stats.ok for the ✓/✗ status when stats are present', () => {
    const r = summarizeResult('write_fact', {
      name: 'write_fact',
      result_preview: '{}',
      stats: { ok: false, chars: 12 },
    });
    expect(r.ok).toBe(false);
  });

  it('falls back to parsing the preview when stats are absent', () => {
    const r = summarizeResult(
      'code_read',
      result('code_read', { path: 'a.py', start: 1, end: 51, total_lines: 51, body: Array(51).fill('x').join('\n') }),
    );
    expect(r.size).toBe('51 rader');
  });
});
