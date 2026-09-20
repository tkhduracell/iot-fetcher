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

export type Status = {
  uptime_s: number;
  now: number;
  paused: boolean;
  pause_file: string;
  slack: SlackState;
  ledger: { day: string; keys: LedgerKey[] };
  proposals: { pending: number; total: number };
  settings: BrainSettings;
};

export type Proposal = {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  reason: string;
  topic: string;
  created: string;
  status: string;
  slack_ts: string;
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
  getJson<{ agent: string; name: string; body: string }>(
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
  /** epoch seconds of the last write. */
  written_at: number;
  /** epoch seconds of the first write; equals `written_at` when unknown. */
  first_written_at: number;
  /** times this fact has been written; >= 1. */
  writes: number;
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
export type Revision = { at: number; body: string };

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
 *  The wall shows beliefs as sentences, and the sentence lives in the fact's
 *  body — there is no bulk endpoint, so this fans out over the per-fact one.
 *  Individual failures are dropped rather than failing the batch: one unreadable
 *  fact should cost one line, not the column. Keep `names` short. */
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

/** The one sentence a fact body says.
 *
 *  Facts are markdown notes; the wall has room for a line, so take the first
 *  line that is prose — skipping headings, bullets markers and blank lines —
 *  and cut it at `max`. Returns "" when the body carries nothing usable, which
 *  is the caller's cue to fall back to the humanized name. */
export function factSentence(body: string | undefined, max: number = 180): string {
  if (!body) return '';
  for (const raw of String(body).split('\n')) {
    const line = raw.replace(/^[#>\s*-]+/, '').trim();
    if (!line) continue;
    return line.length > max ? `${line.slice(0, max).trimEnd()}…` : line;
  }
  return '';
}
