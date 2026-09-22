/** Typed client for the ai-brain introspection API, reached through the
 *  read-only proxy at /api/ai-brain. Types mirror the JSON field-for-field —
 *  keep them in sync with ai-brain/src/ai_brain/api.py. */

const BASE = '/api/ai-brain';

// ---------------------------------------------------------------- types

export type ToolCall = { name: string; args: Record<string, string> };

export type ToolResult = { name: string; result_preview: string };

export type RoundTrace = {
  at: number;
  text: string;
  tool_calls: ToolCall[];
  tool_results: ToolResult[];
};

export type CycleTrace = {
  started_at: number;
  /** null while the cycle is still running. */
  finished_at: number | null;
  /** null while the cycle is still running; a status string once it ends. */
  status: string | null;
  /** True while the cycle is still running. Served by `_trace_json`. */
  in_progress: boolean;
  /** "" before a model has been picked — never null. */
  model: string;
  rounds: RoundTrace[];
  /** "" until the cycle writes one — never null. */
  summary: string;
};

export type LastCycle = {
  status: string;
  /** "" when the cycle never reached a model — never null. */
  model: string;
  rounds: number;
  next_wake_s: number;
};

export type AgentSummary = {
  name: string;
  /** From the persona's own frontmatter; "" when it has none (e.g. seeded
   *  before this field existed and never backfilled). */
  emoji: string;
  priority: 'brain' | 'expert';
  heartbeat_s: number;
  last_cycle: LastCycle | null;
  last_cycle_at: number | null;
  /** last_cycle_at + next_wake_s, or null when either is unknown. */
  next_wake_at: number | null;
  cycle_counts: Record<string, number>;
  in_progress: boolean;
  needs_compaction: boolean;
  facts: number;
  unread_notes: number;
};

export type Note = {
  sender: string;
  body: string;
  created: string;
  /** Basename only — the API never exposes a path. */
  file: string;
};

export type AgentDetail = AgentSummary & {
  identity: string;
  goals: string;
  is_brain: boolean;
  fact_names: string[];
  notes: Note[];
  journal_days: string[];
  trace: CycleTrace | null;
};

export type LedgerKey = {
  key: string;
  /** Spent so far today. */
  requests_day: number;
  tokens_day: number;
  /** The day's budget — the denominator `*_day` is counted against. */
  requests_limit: number;
  tokens_limit: number;
  /** Fractions in 0..1, not counts. */
  requests_remaining: number;
  tokens_remaining: number;
  consecutive_429: number;
  blocked_until: number | null;
  disabled_until: number | null;
  recent_requests: number;
};

export type SlackState = {
  configured: boolean;
  connected: boolean;
  queued: number;
  sessions: number;
};

export type BrainSettings = {
  llm_chain: string[];
  experts: string[];
  brain_heartbeat_s: number;
  expert_heartbeat_s: number;
  call_timeout_s: number;
  dry_run: boolean;
  rpm: number;
  tpm: number;
  rpd: number;
  memory_root: string;
};

export type LanHost = {
  model: string;
  /** null when the finder has not located the host. */
  host: string | null;
  found_at: number | null;
  subnets: string[];
};

/** `lan_host` on `/api/status`: `{enabled:false}` when no LAN model is set up. */
export type LanState = { enabled: boolean; hosts?: LanHost[] };

export type Status = {
  uptime_s: number;
  now: number;
  paused: boolean;
  pause_file: string;
  slack: SlackState;
  ledger: { day: string; keys: LedgerKey[] };
  proposals: { pending: number; total: number };
  settings: BrainSettings;
  /** Which LAN model host the chain found, if LAN models are configured. */
  lan_host?: LanState;
};

export type Proposal = {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  reason: string;
  topic: string;
  created: string;
  status: string;
  /** Not served by `/api/proposals` today — optional so nothing renders
   *  `undefined` when it is absent. */
  slack_ts?: string;
  result: string;
};

export type JournalEntry = { date: string; lines: string[] };

export type SlackSession = {
  topic: string;
  thread_ts: string;
  channel: string;
  status: string;
};

export type Health = { ok: boolean; uptime_s: number; loops: string[] };

// ---------------------------------------------------------------- fetching

/** GET `path` through the proxy. Throws with the upstream `error` field when
 *  present, so the UI can show ai-brain's own message rather than a status code. */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const resp = await fetch(`${BASE}/${path}`, {
    signal,
    headers: { Accept: 'application/json' },
  });

  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    // Non-JSON body (proxy or gateway error page) — fall through to status.
  }

  if (!resp.ok) {
    const message =
      body && typeof body === 'object' && typeof (body as { error?: unknown }).error === 'string'
        ? (body as { error: string }).error
        : `HTTP ${resp.status}`;
    throw new Error(message);
  }

  if (body === null) throw new Error('Ogiltigt svar från ai-brain');
  return body as T;
}

export const fetchHealth = (signal?: AbortSignal) => getJson<Health>('healthz', signal);

export const fetchStatus = (signal?: AbortSignal) => getJson<Status>('api/status', signal);

export const fetchAgents = (signal?: AbortSignal) =>
  getJson<{ agents: AgentSummary[] }>('api/agents', signal);

export const fetchAgent = (name: string, signal?: AbortSignal) =>
  getJson<AgentDetail>(`api/agents/${encodeURIComponent(name)}`, signal);

export const fetchTrace = (name: string, signal?: AbortSignal) =>
  getJson<{ agent: string; trace: CycleTrace | null }>(
    `api/agents/${encodeURIComponent(name)}/trace`,
    signal,
  );

export const fetchJournal = (name: string, days: number, signal?: AbortSignal) =>
  getJson<{ agent: string; days: number; entries: JournalEntry[] }>(
    `api/agents/${encodeURIComponent(name)}/journal?days=${days}`,
    signal,
  );

export const fetchFact = (name: string, fact: string, signal?: AbortSignal) =>
  getJson<Fact>(
    `api/agents/${encodeURIComponent(name)}/facts/${encodeURIComponent(fact)}`,
    signal,
  );

export const fetchProposals = (signal?: AbortSignal) =>
  getJson<{ proposals: Proposal[]; pending: number; total: number }>('api/proposals', signal);

export const fetchSlackSessions = (signal?: AbortSignal) =>
  getJson<{ configured: boolean; queued: number; sessions: SlackSession[] }>(
    'api/slack/sessions',
    signal,
  );

// ---------------------------------------------------------------- helpers

export type Tone = 'ok' | 'warn' | 'error' | 'busy' | 'idle';

/** Maps a cycle status onto the colour family the pills and dots use. */
export function statusTone(status: string | null | undefined): Tone {
  switch (status) {
    case 'ok':
    case 'done':
    case 'completed':
      return 'ok';
    case 'paused':
    case 'skipped':
    case 'no_key':
      return 'warn';
    case 'error':
    case 'failed':
    case 'timeout':
      return 'error';
    case 'running':
    case 'in_progress':
      return 'busy';
    default:
      return 'idle';
  }
}

/** "3 min sedan" for a past unix timestamp (seconds). */
export function formatAgo(at: number | null | undefined, now: number = Date.now() / 1000): string {
  if (at === null || at === undefined || !Number.isFinite(at)) return '–';
  const seconds = Math.floor(now - at);
  if (seconds < 0) return 'nyss';
  if (seconds < 60) return `${seconds} s sedan`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min sedan`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h sedan`;
  return `${Math.floor(hours / 24)} d sedan`;
}

/** "om 2 min" for a future unix timestamp (seconds); "nu" once it has passed. */
export function formatIn(at: number | null | undefined, now: number = Date.now() / 1000): string {
  if (at === null || at === undefined || !Number.isFinite(at)) return '–';
  const seconds = Math.ceil(at - now);
  if (seconds <= 0) return 'nu';
  if (seconds < 60) return `om ${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `om ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `om ${hours} h`;
  return `om ${Math.floor(hours / 24)} d`;
}

/** Journal lines are "HH:MM:SS  text" — split on the first double space so a
 *  single space inside the timestamp-less remainder is preserved. */
export function parseJournalLine(line: string): { time: string | null; text: string } {
  const idx = line.indexOf('  ');
  if (idx === -1) return { time: null, text: line.trim() };
  return { time: line.slice(0, idx).trim(), text: line.slice(idx + 2).trim() };
}

/** Clock-time label for a unix timestamp (seconds). */
export function formatClock(at: number): string {
  return new Date(at * 1000).toLocaleTimeString('sv-SE');
}

/** "12 av 1 500" — what a quota bar says beside itself.
 *
 *  The bar's width is the remaining *fraction*; this is the pair of counts
 *  behind it, so a nearly-full bar still says whether that is 8 requests left
 *  or 8000. A limit of 0 (an unknown key) has no denominator to show. */
export function quotaLabel(used: number, limit: number): string {
  const u = Number.isFinite(used) ? used : 0;
  if (!Number.isFinite(limit) || limit <= 0) return `${u.toLocaleString('sv-SE')} av –`;
  return `${u.toLocaleString('sv-SE')} av ${limit.toLocaleString('sv-SE')}`;
}

/** Colour family for a remaining fraction: green above 40%, yellow above 15%. */
export function quotaTone(remaining: number): 'ok' | 'warn' | 'error' {
  if (!Number.isFinite(remaining)) return 'error';
  if (remaining > 0.4) return 'ok';
  if (remaining > 0.15) return 'warn';
  return 'error';
}

/** Short display name for a ledger key.
 *
 *  Keys arrive provider-qualified ("gemini:gemini-2.5-flash-lite"), which eats
 *  the whole width of a compact quota row on a phone. Strip the provider and
 *  the redundant repeat of it in the model name, then drop the "N." major
 *  version prefix that every model in a chain shares. */
export function shortModel(key: string | null | undefined): string {
  if (!key) return '–';
  let s = String(key).trim();
  const colon = s.indexOf(':');
  if (colon !== -1) {
    const provider = s.slice(0, colon);
    let rest = s.slice(colon + 1);
    if (provider && rest.toLowerCase().startsWith(`${provider.toLowerCase()}-`)) {
      rest = rest.slice(provider.length + 1);
    }
    s = rest || provider;
  }
  return s || '–';
}

/** "11/200" — the compact counts beside a single quota bar. */
export function quotaCounts(used: number, limit: number): string {
  const u = Number.isFinite(used) ? used : 0;
  if (!Number.isFinite(limit) || limit <= 0) return `${u}/–`;
  return `${u}/${limit}`;
}

/** "74k tok" — token spend at a glance, without a denominator. */
export function compactTokens(n: number): string {
  if (!Number.isFinite(n)) return '0';
  const v = Math.round(n);
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (Math.abs(v) >= 1_000) return `${Math.round(v / 1000)}k`;
  return String(v);
}

/** "5 h" / "2 d 3 h" — uptime, short enough for a pill. */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '–';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
}

/** Truncate a tool argument for the trace, keeping whether it was cut. */
export function truncate(value: string, max: number = 160): { text: string; truncated: boolean } {
  const s = value ?? '';
  if (s.length <= max) return { text: s, truncated: false };
  return { text: s.slice(0, max), truncated: true };
}

// ------------------------------------------------- wall view (additive)
//
// Everything below is used by the always-on wall tablet at /ai-brain/wall.
// It is strictly additive: nothing above changes, so the existing /ai-brain
// page keeps its imports. Several of these endpoints and fields are still
// being built in ai-brain — the fetchers here resolve to `null`/`undefined`
// rather than throwing, so a section can go dark without blanking the wall.

/** `fact_stats` entry — per-fact freshness and write count (ai-brain A1). */
export type FactStat = {
  name: string;
  /** Short human-readable label, <=20 words. A fact written before `title`
   *  existed gets one derived from its own body, server-side — never blank. */
  title: string;
  /** epoch seconds of the last write. */
  written_at: number;
  /** epoch seconds of the first write; equals `written_at` when unknown. */
  first_written_at: number;
  /** times this fact has been written; >= 1. */
  writes: number;
};

/** One fact's full content, from `/api/agents/{name}/facts/{fact}`. */
export type Fact = {
  agent: string;
  name: string;
  title: string;
  body: string;
};

/** A known unknown the agent has recorded (ai-brain A3). Only open gaps are
 *  served on agent detail, so `closed_at`/`answer` are not modelled here. */
export type Gap = {
  id: string;
  question: string;
  /** what it blocks; may be "". */
  why: string;
  opened_at: number;
};

/** One superseded identity/goals body (ai-brain A2). */
export type Revision = {
  at: number;
  body: string;
  /** True when `body` was cut to the API's 2000-char cap. */
  truncated?: boolean;
};

/** Agent detail once the backend carries the memory-introspection fields.
 *  Every added field is optional: today's API omits them all. */
export type AgentDetailPlus = AgentDetail & {
  fact_stats?: FactStat[];
  gaps?: Gap[];
  identity_history?: Revision[];
  goals_history?: Revision[];
};

export type LoopProposal = {
  id: string;
  kind: string;
  created: string;
  status: string;
  result: string;
};

/** One repeated topic from `/api/loops` — the same subject proposed N times. */
export type Loop = {
  topic: string;
  laps: number;
  first_at: string;
  last_at: string;
  pending: number;
  approved: number;
  rejected: number;
  kinds: string[];
  proposals: LoopProposal[];
};

export type LoopsResponse = { loops: Loop[] };

export type UsefulnessBuckets = {
  total: number;
  nothing: number;
  note: number;
  real: number;
  repeat: number;
};

export type UsefulnessLoop = UsefulnessBuckets & { name: string };

export type UsefulnessResponse = { loops: UsefulnessLoop[]; totals: UsefulnessBuckets };

/** True for the error `fetch` raises when its AbortSignal fires. An aborted
 *  poll must not be mistaken for "endpoint missing" — the caller is unmounting
 *  and the next poll will ask again. */
function isAbortError(e: unknown): boolean {
  return e instanceof Error && (e.name === 'AbortError' || e.name === 'TimeoutError');
}

/** GET `path`, resolving to `null` when ai-brain does not serve it (yet).
 *
 *  The wall calls endpoints that are still landing in ai-brain; a 404 there is
 *  a section that stays dark, not a page error. Aborts still reject so the
 *  polling hook can distinguish teardown from an absent endpoint. */
export async function getJsonOptional<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T | null> {
  try {
    return await getJson<T>(path, signal);
  } catch (e) {
    if (isAbortError(e) || signal?.aborted) throw e;
    return null;
  }
}

/** `/api/loops` — repeated proposal topics. `null` until the endpoint exists. */
export const fetchLoops = (signal?: AbortSignal) =>
  getJsonOptional<LoopsResponse>('api/loops', signal);

/** `/api/usefulness` — cycle outcomes bucketed per loop. `null` until it exists. */
export const fetchUsefulness = (signal?: AbortSignal) =>
  getJsonOptional<UsefulnessResponse>('api/usefulness', signal);

/** Agent detail, typed with the optional memory-introspection fields. Same
 *  endpoint as `fetchAgent` — the extra keys simply appear when ai-brain adds
 *  them, so nothing here needs to change on that day. */
export const fetchAgentPlus = (name: string, signal?: AbortSignal) =>
  getJson<AgentDetailPlus>(`api/agents/${encodeURIComponent(name)}`, signal);

/** Fact bodies for one agent, keyed by fact name.
 *
 *  The wall shows each belief's title (from fact_stats, already in hand) with
 *  its body as the description underneath — the body is the only part not
 *  already on the agent-detail response, and there is no bulk endpoint for
 *  it, so this fans out over the per-fact one. Individual failures are
 *  dropped rather than failing the batch: one unreadable fact should cost one
 *  line, not the column. Keep `names` short. */
export async function fetchFactBodies(
  agent: string,
  names: string[],
  signal?: AbortSignal,
): Promise<Record<string, string>> {
  const results = await Promise.allSettled(
    names.map((fact) => fetchFact(agent, fact, signal)),
  );
  const out: Record<string, string> = {};
  for (const r of results) {
    if (r.status === 'fulfilled' && r.value && typeof r.value.body === 'string') {
      out[r.value.name] = r.value.body;
    }
  }
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  return out;
}

/** "pool_pump_schedule" → "Pool pump schedule" — a readable stand-in for a
 *  fact whose body has not been fetched (or is empty). */
export function humanizeFactName(name: string): string {
  const words = String(name ?? '')
    .replace(/\.md$/i, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!words) return '–';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** True for a line that is a label rather than a statement.
 *
 *  Fact bodies written by the model open with a caption — "Health data from
 *  Garmin Forerunner 245 Music in VictoriaMetrics (Database_Name: irisgatan):"
 *  — and the substance follows underneath. A trailing colon is that caption's
 *  one reliable marker, so it is the only thing tested for; a colon *inside* a
 *  line ("Pooltemp: 26 °C") is part of a perfectly good sentence. */
export function isLabelLine(line: string): boolean {
  return /:\s*$/.test(line);
}

/** Strips the markdown a fact body is written in, leaving the words. */
function stripMarkup(raw: string): string {
  return raw
    // list markers, blockquote carets, heading hashes, leading numbering
    .replace(/^\s*(?:[-*+•]\s+|>\s*|#{1,6}\s+|\d+[.)]\s+)/, '')
    // bold/italic/code fences around the whole line
    .replace(/^\s*[`*_]+|[`*_]+\s*$/g, '')
    .trim();
}

/** Lines that carry no statement whatever they look like. */
function isStructural(line: string): boolean {
  if (!line) return true;
  if (/^[-=_*\s|]+$/.test(line)) return true; // rules, table separators
  if (/^```/.test(line)) return true; // code fence
  if (/^\|/.test(line)) return true; // table row
  return false;
}

/** Cut `text` to at most `max` characters at a boundary a reader recognises.
 *
 *  Prefers the end of a sentence inside the budget (the result then needs no
 *  ellipsis — it *is* the whole statement), else falls back to the last word
 *  boundary and marks the cut. Never splits a word, which is what the wall was
 *  doing: "…in VictoriaMet…". */
export function cutAtBoundary(text: string, max: number): string {
  const s = text.trim();
  if (s.length <= max) return s;

  const head = s.slice(0, max + 1);

  // The last sentence end inside the budget — but only if it leaves enough
  // behind to be worth showing, else a body opening with "Ja. ..." would
  // render as the word "Ja.".
  let lastSentence = -1;
  for (let i = 0; i < head.length; i += 1) {
    if ('.!?'.includes(head[i]) && (i + 1 >= head.length || /\s/.test(head[i + 1]))) {
      lastSentence = i;
    }
  }
  if (lastSentence >= Math.floor(max * 0.4)) return s.slice(0, lastSentence + 1);

  const space = head.lastIndexOf(' ');
  const cut = space > Math.floor(max * 0.4) ? space : max;
  return `${s.slice(0, cut).replace(/[\s,;:–-]+$/, '')}…`;
}

/** The one sentence a fact body says.
 *
 *  Facts are markdown notes and the wall has room for about two lines, so this
 *  looks for the first line that reads as a *statement*: label lines (ending in
 *  `:`), headings, table rows and rules are skipped, and a bullet is only used
 *  when nothing better is on offer. The result is cut at a sentence or word
 *  boundary — never mid-word — so a belief stays a belief rather than becoming
 *  a truncated paste. Returns "" when the body carries nothing usable, which is
 *  the caller's cue to fall back to the humanized fact name. */
export function factSentence(body: string | undefined, max: number = 120): string {
  if (!body) return '';

  const lines = String(body).split('\n');
  let bullet = '';
  let label = '';

  for (const raw of lines) {
    const isBullet = /^\s*(?:[-*+•]\s+|\d+[.)]\s+)/.test(raw);
    // A markdown heading is a title, the same thing a trailing colon is: it
    // names the subject, it does not state anything about it.
    const isHeading = /^\s*#{1,6}\s/.test(raw);
    const line = stripMarkup(raw);
    if (isStructural(line)) continue;
    if (isHeading || isLabelLine(line)) {
      // Remember the caption: a fact whose body is *only* a caption should
      // still say something rather than falling back to its file name.
      if (!label) label = line.replace(/:\s*$/, '');
      continue;
    }
    if (isBullet) {
      if (!bullet) bullet = line;
      continue;
    }
    return cutAtBoundary(line, max);
  }

  if (bullet) return cutAtBoundary(bullet, max);
  if (label) return cutAtBoundary(label, max);
  return '';
}

// ------------------------------------------------- wall semantics
//
// The wall's editorial rules, kept here rather than in the components so they
// are testable without a DOM and so the deeper screens apply the same ones.

/** Cycle statuses and failure phrases that must never become the hero line. */
const STATUS_PHRASES = [
  'no provider budget left',
  'no budget',
  'budget',
  'quota',
  'rate limit',
  'rate-limited',
  'no key',
  'no_key',
  'timeout',
  'timed out',
  'error',
  'failed',
  'failure',
  'exception',
  'traceback',
  'aborted',
  'cancelled',
  'canceled',
  'skipped',
  'paused',
  'unavailable',
  'http 4',
  'http 5',
  '429',
  '503',
];

/** True when a cycle summary is machine status rather than understanding.
 *
 *  The deployed wall put `no provider budget left` in 38px serif as what the
 *  house's brain understands. A cycle that died has a status, not a thought:
 *  anything that looks like one is suppressed here and surfaced instead as a
 *  condition beside the machine line. Fragment detection (short, lowercase, no
 *  terminal punctuation) catches the next such string without a new phrase. */
export function isStatusSummary(summary: string | null | undefined): boolean {
  const s = (summary ?? '').trim();
  if (!s) return true;
  const lower = s.toLowerCase();
  if (STATUS_PHRASES.some((p) => lower.includes(p))) return true;
  // A bare fragment: no sentence end, no capital, few words. A real summary the
  // model wrote is a sentence; `status=ok rounds=3` is not.
  const words = lower.split(/\s+/).length;
  if (words <= 8 && !/[.!?…]$/.test(s) && s[0] === lower[0]) return true;
  if (/^[a-z_]+=[^\s]/.test(lower)) return true;
  return false;
}

/** The hero sentence: what the house's brain currently understands.
 *
 *  SEAM: there is no endpoint that generates a standing "what I understand"
 *  sentence. The best honest source is the brain's last cycle summary — the
 *  model's own words — and when that is absent or is status text (see
 *  `isStatusSummary`) we count what is actually on disk instead. Neither is
 *  invented. When a real understanding field lands, point this at it and delete
 *  the counting fallback. */
export function heroSentence(
  summary: string | undefined,
  agents: AgentSummary[],
  pending: number,
): string {
  const trimmed = (summary ?? '').trim();
  if (trimmed && !isStatusSummary(trimmed)) return trimmed;

  if (agents.length === 0) return 'Hjärnan har inte sagt något ännu.';

  const facts = agents.reduce((sum, a) => sum + (a.facts ?? 0), 0);
  const parts = [
    `Hjärnan håller ${facts} fakta om huset över ${agents.length} ${
      agents.length === 1 ? 'loop' : 'loopar'
    }`,
  ];
  // "förslag" is the same in singular and plural, so no branch is needed.
  if (pending > 0) parts.push(`${pending} förslag väntar på ditt ✅`);
  return `${parts.join(', ')}.`;
}

/** A machine condition worth one word near the machine line. */
export type Condition = { id: string; label: string; tone: Tone };

/** The conditions the wall admits to, in the order they matter.
 *
 *  This is where "tom budget" belongs — beside the telemetry, not in the hero.
 *  Returns [] on a healthy brain, so the strip disappears entirely. */
export function conditions(
  status: Status | null | undefined,
  agents: AgentSummary[],
  offline: boolean = false,
): Condition[] {
  const out: Condition[] = [];
  if (offline) out.push({ id: 'offline', label: 'ingen kontakt', tone: 'error' });
  if (status?.paused) out.push({ id: 'paused', label: 'pausad', tone: 'warn' });

  const keys = status?.ledger?.keys ?? [];
  if (keys.length > 0) {
    const usable = keys.filter(
      (k) => !k.disabled_until && !k.blocked_until && (k.requests_remaining ?? 0) > 0,
    );
    if (usable.length === 0) {
      out.push({ id: 'budget', label: 'tom budget', tone: 'error' });
    } else if (usable.length < keys.length) {
      out.push({
        id: 'budget-partial',
        label: `${keys.length - usable.length} nyckel slut`,
        tone: 'warn',
      });
    }
  }

  if (status?.settings?.dry_run) out.push({ id: 'dry-run', label: 'torrkörning', tone: 'warn' });
  if (status?.slack && status.slack.configured && !status.slack.connected) {
    out.push({ id: 'slack', label: 'slack nere', tone: 'warn' });
  }
  const compacting = agents.filter((a) => a.needs_compaction).length;
  if (compacting > 0) {
    out.push({ id: 'compaction', label: `${compacting} vill kompaktera`, tone: 'warn' });
  }
  return out;
}

// ------------------------------------------------- proposals

const EXECUTED_STATUSES = new Set(['executed', 'executing']);
const REJECTED_STATUSES = new Set(['rejected', 'denied']);

export function isPending(p: Proposal): boolean {
  return p.status === 'pending';
}

export function isExecuted(p: Proposal): boolean {
  return EXECUTED_STATUSES.has(p.status);
}

export function isRejected(p: Proposal): boolean {
  return REJECTED_STATUSES.has(p.status);
}

/** Which executor runs a proposal kind. The kind *is* the executor entry point
 *  in ai-brain (`Executors.run`), so this only gives it a name a room can read;
 *  an unknown kind falls through to the raw identifier rather than being hidden. */
const EXECUTOR_LABEL: Record<string, string> = {
  sonos_say: 'Sonos',
  ha_todo_add: 'Att göra-listan',
  ha_service: 'Home Assistant',
};

export function executorName(kind: string): string {
  return EXECUTOR_LABEL[kind] ?? kind;
}

/** Proposal timestamps are ISO strings; everything else is epoch seconds.
 *  Unparseable input yields null so `formatAgo` shows a dash. */
export function createdSeconds(created: string | undefined): number | null {
  if (!created) return null;
  const ms = Date.parse(created);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

/** The one line a proposal is about: its payload's own text where the kind has
 *  one, else the topic. Keeps the UI showing the thing, not the JSON. */
export function proposalSentence(p: Proposal): string {
  const payload = (p.payload ?? {}) as Record<string, unknown>;
  for (const key of ['text', 'item', 'message', 'title', 'entity_id']) {
    const value = payload[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return p.topic || p.kind || p.id;
}

/** Several executions of the same thing, collapsed into one line with a count. */
export type ProposalGroup = {
  /** Grouping key — the normalised sentence. Stable across polls. */
  key: string;
  /** The newest member; its sentence and reason are what render. */
  latest: Proposal;
  sentence: string;
  count: number;
  /** Newest first — every proposal that collapsed into this line. */
  members: Proposal[];
};

/** Lowercased, punctuation- and whitespace-normalised, so "Replace Roborock S6
 *  MaxV main brush" and "Replace Roborock S6 MaxV main brush." are one thing. */
export function groupKey(p: Proposal): string {
  return `${p.kind}\u0000${proposalSentence(p)
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .replace(/[.!?,;:]+$/, '')
    .trim()}`;
}

/** The words of a proposal that identify *what* it is about.
 *
 *  Parentheticals go first: the deployed brain wrote "Replace Roborock S6 MaxV
 *  main brush (time left: 6.1 hours)" and "Replace Roborock S6 MaxV main brush"
 *  as separate proposals, and a countdown baked into the text must not make two
 *  sayings of one thing look like two things. Stop words go too, so word order
 *  and filler ("main brush on X" vs "X main brush") stop mattering. */
export function contentWords(text: string): Set<string> {
  const STOP = new Set(['the', 'a', 'an', 'on', 'in', 'of', 'to', 'for', 'and', 'with', 'i', 'på', 'och', 'en', 'ett', 'den', 'det']);
  return new Set(
    String(text ?? '')
      .toLowerCase()
      .replace(/\([^)]*\)/g, ' ')
      .replace(/[^\p{L}\p{N}]+/gu, ' ')
      .split(' ')
      .filter((w) => w.length > 1 && !STOP.has(w)),
  );
}

/** Jaccard overlap of two word sets, 0..1. Two empty sets are not "identical" —
 *  a proposal with no words left is unidentifiable, not a match for everything. */
export function wordOverlap(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 || b.size === 0) return 0;
  let shared = 0;
  for (const w of a) if (b.has(w)) shared += 1;
  return shared / (a.size + b.size - shared);
}

/** How alike two proposals have to read before they are one thing said twice.
 *
 *  0.75, and the margin is thin on purpose. The widest real pair -- "Replace
 *  Roborock S6 MaxV main brush" against "Replace main brush on Roborock S6 MaxV
 *  vacuum cleaner" -- shares six of eight content words, exactly 0.75. Lower it
 *  to 0.7 and "main brush" starts merging with "side brush", which are two
 *  different parts and two real errands. So this is the loosest threshold that
 *  still tells those apart, and both cases are pinned by tests. */
const SAME_THING = 0.75;

/** Collapse repeated proposals by what they actually say.
 *
 *  The deployed wall printed "Replace Roborock S6 MaxV main brush" three times
 *  under Verkställt, twice near-identically — which is exactly the repetition
 *  this UI exists to make visible, shown as three separate events instead of
 *  one with a count. Input order is preserved (the API serves newest first) and
 *  a group takes the position of its newest member. */
export function groupProposals(proposals: Proposal[]): ProposalGroup[] {
  // An exact key alone was not enough. The three brush proposals on the real
  // wall differed by a parenthetical countdown and by word order, so they hashed
  // to three keys and printed as three events -- the repetition this view exists
  // to expose, reproduced inside it. So: exact key first (cheap, and it settles
  // the identical ones), then a similarity pass over the groups of the same
  // kind, which is quadratic in the number of *groups* and so bounded by what a
  // screen shows.
  const groups: (ProposalGroup & { words: Set<string> })[] = [];
  for (const p of proposals ?? []) {
    const key = groupKey(p);
    const words = contentWords(proposalSentence(p));
    const match =
      groups.find((g) => g.key === key) ??
      groups.find((g) => g.latest.kind === p.kind && wordOverlap(g.words, words) >= SAME_THING);
    if (match) {
      match.count += 1;
      match.members.push(p);
      continue;
    }
    groups.push({
      key,
      latest: p,
      sentence: proposalSentence(p),
      count: 1,
      members: [p],
      words,
    });
  }
  return groups.map(({ words: _words, ...group }) => group);
}

// ------------------------------------------------- loops

/** A topic proposed once is not a loop.
 *
 *  `/api/loops` groups every proposal by topic, including topics with a single
 *  member — so the deployed wall's "Tjatar om" listed `1× roborock` as
 *  something the brain keeps coming back to. Two laps is the floor for the word
 *  to mean anything; the caller hides the whole section when this is empty. */
export function naggingLoops(
  loops: Loop[] | null | undefined,
  minLaps: number = 2,
  limit: number = Infinity,
): Loop[] {
  return (loops ?? [])
    .filter((l) => (l?.laps ?? 0) >= minLaps)
    .sort((a, b) => b.laps - a.laps || a.topic.localeCompare(b.topic, 'sv'))
    .slice(0, limit === Infinity ? undefined : limit);
}

/** Share of a loop's cycles that produced something real, in 0..1. `null` when
 *  the loop has not run — a 0 % bar for a loop with no cycles is a lie. */
export function usefulShare(b: UsefulnessBuckets | null | undefined): number | null {
  if (!b || !b.total) return null;
  return (b.real + b.note) / b.total;
}

// ------------------------------------------------- ledger

/** Ledger keys split into the ones that have done any work today and a count
 *  of the ones that have not.
 *
 *  The deployed machine line printed `qwen3.8:27b-mlx 0/1000000` and
 *  `qwen3-coder:30b 0/1000000` — two keys that had not been called at all —
 *  and wrapped to three lines because of them. A key with no traffic and no
 *  problem says nothing; a key that is blocked or disabled says a lot, so that
 *  one stays even at zero. */
export function activeLedgerKeys(keys: LedgerKey[] | null | undefined): {
  active: LedgerKey[];
  silent: number;
} {
  const all = keys ?? [];
  const active = all.filter(
    (k) =>
      (k.requests_day ?? 0) > 0 ||
      (k.tokens_day ?? 0) > 0 ||
      Boolean(k.blocked_until) ||
      Boolean(k.disabled_until) ||
      (k.consecutive_429 ?? 0) > 0,
  );
  return { active, silent: all.length - active.length };
}

// ------------------------------------------------- facts

/** Fact stats newest write first — the order the knowledge screens read in. */
export function sortFactStats(stats: FactStat[] | null | undefined): FactStat[] {
  return [...(stats ?? [])].sort((a, b) => (b.written_at ?? 0) - (a.written_at ?? 0));
}

/** How stale a fact is, as a colour family: fresh under a day, warn under a
 *  week, error beyond. The thresholds are the wall's, not the brain's — nothing
 *  in ai-brain declares a fact expired. */
export function freshnessTone(
  writtenAt: number | null | undefined,
  now: number = Date.now() / 1000,
): Tone {
  if (writtenAt === null || writtenAt === undefined || !Number.isFinite(writtenAt)) return 'idle';
  const age = now - writtenAt;
  if (age < 86_400) return 'ok';
  if (age < 7 * 86_400) return 'warn';
  return 'error';
}

/** "2026-09-20" or an ISO timestamp → "20 sep" for a dense list. */
export function formatDay(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '–';
  const ms = typeof value === 'number' ? value * 1000 : Date.parse(value);
  if (!Number.isFinite(ms)) return String(value);
  return new Date(ms).toLocaleDateString('sv-SE', { day: 'numeric', month: 'short' });
}
