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
  /** The model's own reasoning, when it hands it over: a summary from Gemini,
   *  the real thing from a thinking model on the LAN. "" for every model that
   *  does not think out loud, and missing entirely from a round served by an
   *  ai-brain older than the field — hence optional. */
  thinking?: string;
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

/** Why a chain entry cannot answer right now, or that it can.
 *
 *  "available" only means nothing here is known to block it -- an unmetered
 *  `lan:` host can still fail if reached (never discovered again after a
 *  restart, network down), and a "blocked" cloud key can still be tried once
 *  its cooldown lapses. This is a status label, not a promise. */
export type ModelAvailability = 'available' | 'no_lan_host' | 'blocked';

/** Whether one `llm_chain` entry can actually be reached right now.
 *
 *  A `lan:` entry is served by an `OllamaFinder` that must have located a
 *  host on the network -- and that provider is unmetered (see
 *  `limits_from_settings` in ai_brain/llm/__init__.py), so its ledger bucket
 *  is almost never `blocked_until`. Reading only the ledger, as the model
 *  chain list used to, renders a `lan:` model with no host found as plain
 *  "available": the ledger genuinely has nothing against it, but there is
 *  nowhere for a call to go. `lan_host.hosts` is the only source for that. */
export function modelAvailability(
  model: string,
  ledgerKeys: LedgerKey[] | null | undefined,
  lanHost: LanState | null | undefined,
  now: number,
): ModelAvailability {
  const colon = model.indexOf(':');
  const provider = colon === -1 ? model : model.slice(0, colon);
  if (provider === 'lan') {
    const bareModel = colon === -1 ? '' : model.slice(colon + 1);
    const host = (lanHost?.hosts ?? []).find((h) => h.model === bareModel);
    if (!host || !host.host) return 'no_lan_host';
    return 'available';
  }
  const entry = (ledgerKeys ?? []).find((k) => k.key === model);
  const blocked = Boolean(
    entry && ((entry.blocked_until ?? 0) > now || (entry.disabled_until ?? 0) > now),
  );
  return blocked ? 'blocked' : 'available';
}

/** "11/200" — the compact counts beside a single quota bar, or a bare "11"
 *  for a key with no real budget to be counted against.
 *
 *  `unmetered` is for a `lan:` key: it still carries a real (huge) `Limits`
 *  value -- the ledger needs some bucket to record against -- so `limit` here
 *  is never 0 or missing. Printing it anyway, even as "2/∞", still reads as a
 *  budget with a denominator; an unlimited local model has no budget at all,
 *  so this drops the slash and shows the count on its own. */
export function quotaCounts(used: number, limit: number, unmetered = false): string {
  const u = Number.isFinite(used) ? used : 0;
  if (unmetered) return `${u}`;
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

// ------------------------------------------------- compact tool call line
//
// A round's tool calls used to render as one `→ tool` block per argument and
// one `← tool` block dumping raw JSON — several screens for what should be a
// scannable line. Everything below turns a `ToolCall`/`ToolResult` pair into
// that one line (name, a short args summary, a result status and size hint),
// plus the pretty-printed view behind the expander. Mirrors
// `ai_brain/loop.py`'s `_trunc`/`_safe_args`/`ok`/`err` shapes field-for-field
// — keep in sync if those change.

/** How many characters an inline one-line summary (args or result) may use
 *  before it is cut with an ellipsis. Generous enough for a path:range or a
 *  short promql, tight enough that the line never wraps. */
const SUMMARY_MAX = 72;

/** Cut `s` to `max` chars, ellipsis-terminated, never longer than `max`. */
function ellipsize(s: string, max: number = SUMMARY_MAX): string {
  const t = s.trim();
  if (t.length <= max) return t;
  return `${t.slice(0, Math.max(0, max - 1))}…`;
}

/** `code_read`/`code_grep`'s line ranges are strings on the wire (every
 *  `ToolCall.args` value is `_safe_args`-coerced to `str` server-side) — this
 *  reads one back as a finite integer, or `null` for anything else (missing,
 *  "", non-numeric). */
function argInt(args: Record<string, string>, key: string): number | null {
  const raw = args[key];
  if (raw === undefined || raw === '') return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** One `key=value value2=…` fallback for a tool with no dedicated formatter
 *  below — every argument, short values bare, longer ones quoted, the whole
 *  line capped so it still fits one row. */
function genericArgsSummary(args: Record<string, string>): string {
  const parts = Object.entries(args).map(([k, v]) => {
    const val = String(v ?? '');
    const short = val.length > 24 ? `${val.slice(0, 23)}…` : val;
    return `${k}=${short}`;
  });
  return ellipsize(parts.join(' '));
}

/** Per-tool inline summaries for the calls common enough to earn one. Each
 *  entry mirrors that tool's actual argument names in
 *  `ai_brain/tools/*.py`'s `ToolSpec.parameters`. Anything not listed here
 *  falls back to `genericArgsSummary`. */
const ARG_FORMATTERS: Record<string, (args: Record<string, string>) => string> = {
  code_read: (args) => {
    const path = args.path ?? '';
    const start = argInt(args, 'start');
    const end = argInt(args, 'end');
    if (start === null) return ellipsize(path);
    if (end === null || end <= 0 || end === start) return ellipsize(`${path}:${start}`);
    return ellipsize(`${path}:${start}–${end}`);
  },
  code_grep: (args) => {
    const pattern = args.pattern ?? '';
    const path = args.path;
    return ellipsize(path ? `/${pattern}/ in ${path}` : `/${pattern}/`);
  },
  vm_query: (args) => {
    const promql = args.promql ?? '';
    const range = argInt(args, 'range_minutes');
    return ellipsize(range && range > 0 ? `${promql} ${range}min` : promql);
  },
  read_expert: (args) => {
    const bits = [args.name, args.what, args.fact].filter(Boolean);
    return ellipsize(bits.join('/'));
  },
  ha_context: (args) => ellipsize(args.name || args.domain || args.area || ''),
  write_fact: (args) => ellipsize(args.name ?? ''),
  send_note: (args) => ellipsize(args.to ?? ''),
  end_cycle: (args) => {
    const minutes = args.next_wake_minutes ?? '';
    return ellipsize(minutes ? `${minutes} min` : '');
  },
};

/** The one-line args summary shown next to `→ toolName` in the collapsed
 *  row, e.g. `pool-pump-planner/vm.go:200–250`. */
export function summarizeArgs(name: string, args: Record<string, string> | undefined): string {
  const a = args ?? {};
  const formatter = ARG_FORMATTERS[name];
  const summary = formatter ? formatter(a) : genericArgsSummary(a);
  return summary || '';
}

/** Undo the literal backslash-escapes left behind when a JSON string
 *  (already valid JSON text, with `\n` etc. as two source characters) is
 *  shown as plain text instead of being parsed — the wall-of-text bug this
 *  whole module exists to fix. Only used for the raw-text fallback path;
 *  `JSON.parse` already does this correctly for anything that parses. */
export function decodeEscapes(text: string): string {
  // One pass, left to right, consuming each backslash escape as it is found
  // -- not a chain of sequential .replace() calls. Sequential replaces are
  // wrong here: running \n before \\ would turn source code's literal `"\n"`
  // (already escaped once by json.dumps, so `\\n` in this text) into a
  // backslash plus a real newline instead of the literal `\n` it should stay.
  // A single regex with no overlap between alternatives has no such ordering
  // to get wrong. Also decodes `\uXXXX`, since a 500-char-capped preview cuts
  // off before json.dumps's ensure_ascii-escaped non-ASCII (e.g. Swedish
  // å/ä/ö) has a chance to matter less.
  // `\r\n` (two escapes in the source JSON, a Windows line ending) must match
  // as one unit ahead of the single-escape alternatives below, or `\r` alone
  // would consume the first half and leave a stray blank line behind.
  return text.replace(/\\(r\\n|u[0-9a-fA-F]{4}|[nrt"\\/bf])/g, (_, code: string) => {
    switch (code) {
      case 'r\\n':
      case 'n':
        return '\n';
      case 'r':
        return '\n';
      case 't':
        return '\t';
      case '"':
        return '"';
      case '\\':
        return '\\';
      case '/':
        return '/';
      case 'b':
        return '\b';
      case 'f':
        return '\f';
      default:
        return String.fromCharCode(parseInt(code.slice(1), 16));
    }
  });
}

/** `result_preview` is `_trunc(json.dumps(...), 500)` server-side: valid JSON
 *  text, optionally cut mid-string with a literal `…[+N]` suffix saying how
 *  many characters were dropped. Splits the two apart so the JSON half can be
 *  parsed on its own. */
export function splitPreviewSuffix(preview: string): { body: string; droppedChars: number | null } {
  const m = /…\[\+(\d+)\]$/.exec(preview);
  if (!m) return { body: preview, droppedChars: null };
  return { body: preview.slice(0, m.index), droppedChars: Number(m[1]) };
}

/** The parsed form of a tool result, used by both the one-line status and the
 *  expanded pretty-print. `json` is `null` when the (possibly truncated) body
 *  did not parse — a `…[+N]` cut can land mid-token, which is expected and
 *  handled by falling back to the raw text rather than treated as an error. */
export type ParsedResult = {
  json: Record<string, unknown> | unknown[] | string | number | boolean | null;
  parsed: boolean;
  raw: string;
  droppedChars: number | null;
};

export function parseResultPreview(preview: string): ParsedResult {
  const { body, droppedChars } = splitPreviewSuffix(preview ?? '');
  try {
    const json = JSON.parse(body);
    return { json, parsed: true, raw: preview, droppedChars };
  } catch {
    return { json: null, parsed: false, raw: preview, droppedChars };
  }
}

/** A byte/row/series count rendered the way a reader wants it, not the way
 *  the API named it — "51 rader", "3 serier", "12 träffar", "1.5 kB". Falls
 *  back to a byte-size hint (`raw.length`, which is UTF-16 code units, close
 *  enough for a rough "how much text is this" hint) when the tool has no
 *  countable shape of its own. */
function sizeHint(name: string, json: ParsedResult['json'], raw: string): string {
  if (json && typeof json === 'object' && !Array.isArray(json)) {
    const obj = json as Record<string, unknown>;
    if (name === 'code_read') {
      const body = typeof obj.body === 'string' ? obj.body : '';
      const lines = body.length ? body.split('\n').length : 0;
      return `${lines} ${lines === 1 ? 'rad' : 'rader'}`;
    }
    if (name === 'code_grep' && Array.isArray(obj.hits)) {
      const n = obj.hits.length;
      return `${n} ${n === 1 ? 'träff' : 'träffar'}`;
    }
    if (name === 'vm_query' && Array.isArray(obj.series)) {
      const n = obj.series.length;
      return `${n} ${n === 1 ? 'serie' : 'serier'}`;
    }
    if (typeof obj.context === 'string') return byteHint(obj.context.length);
    if (typeof obj.journal === 'string') return byteHint(obj.journal.length);
    if (typeof obj.persona === 'string') return byteHint(obj.persona.length);
    if (typeof obj.body === 'string') return byteHint(obj.body.length);
    if (Array.isArray(obj.gaps)) return `${obj.gaps.length} luckor`;
  }
  if (Array.isArray(json)) return `${json.length} rader`;
  return byteHint(raw.length);
}

function byteHint(chars: number): string {
  if (chars < 1000) return `${chars} tecken`;
  return `${(chars / 1000).toFixed(1).replace(/\.0$/, '')} kB`;
}

/** The `✓`/`✗` status and size hint shown after `←` on the collapsed line,
 *  e.g. `✓ 51 rader` or `✗ unknown tool: foo`. An `{"error": …}` result (see
 *  `err()` in `ai_brain/tools/__init__.py`) always reads as `✗`; anything
 *  else — including a result that failed to parse at all, since only a
 *  truncated *success* body is expected to do that — reads as `✓`. */
export function summarizeResult(
  name: string,
  result: ToolResult,
): { ok: boolean; status: string; size: string } {
  const { json, parsed, raw, droppedChars } = parseResultPreview(result.result_preview ?? '');

  if (parsed && json && typeof json === 'object' && !Array.isArray(json) && 'error' in json) {
    const msg = String((json as Record<string, unknown>).error ?? '');
    return { ok: false, status: ellipsize(msg, 96), size: byteHint(raw.length) };
  }

  const size = sizeHint(name, json, raw);
  const withDrop = droppedChars ? `${size} (${droppedChars} tecken trunkerade)` : size;
  return { ok: true, status: '', size: withDrop };
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

/** The brain's own verdict on one expert (`review_expert` in
 *  `ai_brain/tools/introspect.py`), from `brain/reviews/<name>.jsonl`. Served
 *  as `last_review` on `/api/agents/{name}` for an expert; absent for the
 *  brain itself, since it has no standing to review its own memory. */
export type ReviewVerdict = 'good' | 'stale' | 'wrong' | 'repetitive' | 'off_goal';

export type Review = {
  ts: number;
  verdict: ReviewVerdict;
  findings: string;
};

/** Agent detail once the backend carries the memory-introspection fields.
 *  Every added field is optional: today's API omits them all. */
export type AgentDetailPlus = AgentDetail & {
  fact_stats?: FactStat[];
  gaps?: Gap[];
  identity_history?: Revision[];
  goals_history?: Revision[];
  /** Only present for an expert; `undefined` for the brain and for an
   *  ai-brain older than this field. `null` means the expert has never been
   *  reviewed — distinct from "not served at all". */
  last_review?: Review | null;
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

/** The header's "what has this loop last done" line, honest about a cycle
 *  that is running right now and about a model that a restart forgot.
 *
 *  The deployed agent page said "ingen modell · aldrig kört" in two cases that
 *  are not that: a cycle genuinely in progress (there is a model, it just has
 *  not finished long enough to land in `last_cycle` yet), and a brain that was
 *  restarted after running for weeks (`last_cycle` is gone, but the journal
 *  remembers it ran). Neither is "never ran" and one of them is "running right
 *  now", so both get their own words instead of the same false claim.
 *
 *  `hasJournalHistory` is whether the journal has any entries at all — the
 *  signal this function has no other way to get, since `last_cycle` is silent
 *  about pre-restart history by construction. */
export function modelStateLabel(
  lastCycleModel: string | null | undefined,
  inProgress: boolean,
  traceModel: string | null | undefined,
  hasJournalHistory: boolean,
): string {
  if (lastCycleModel) return shortModel(lastCycleModel);
  if (inProgress) return traceModel ? `pågår · ${shortModel(traceModel)}` : 'pågår';
  if (hasJournalHistory) return 'okänt sedan omstart';
  return 'ingen modell';
}

/** The header's "when did this loop last run" line, honest about the same two
 *  cases `modelStateLabel` is. A cycle in progress has not finished, so
 *  "senast …" would be about the *previous* cycle at best; a restart erases
 *  `last_cycle_at` along with `last_cycle`, so "aldrig kört" would claim a
 *  loop with weeks of journal history had never run once. */
export function lastRunLabel(
  lastCycleAt: number | null | undefined,
  now: number,
  inProgress: boolean,
  hasJournalHistory: boolean,
): string {
  if (typeof lastCycleAt === 'number' && Number.isFinite(lastCycleAt)) {
    return `senast ${formatAgo(lastCycleAt, now)}`;
  }
  if (inProgress) return 'pågår nu';
  if (hasJournalHistory) return 'okänt sedan omstart';
  return 'aldrig kört';
}

// ------------------------------------------------- review verdicts

/** Swedish label for a review verdict, short enough for a badge. */
const VERDICT_LABEL: Record<ReviewVerdict, string> = {
  good: 'bra',
  stale: 'inaktuell',
  wrong: 'fel',
  repetitive: 'upprepar sig',
  off_goal: 'fel spår',
};

export function verdictLabel(verdict: ReviewVerdict | null | undefined): string {
  if (!verdict) return 'ogranskad';
  return VERDICT_LABEL[verdict] ?? verdict;
}

/** Colour family for a verdict badge: only `good` reads as healthy, and a
 *  missing review is neutral rather than a silent failure. */
export function verdictTone(verdict: ReviewVerdict | null | undefined): Tone {
  switch (verdict) {
    case 'good':
      return 'ok';
    case 'stale':
    case 'repetitive':
    case 'off_goal':
      return 'warn';
    case 'wrong':
      return 'error';
    default:
      return 'idle';
  }
}

/** Verdicts bad enough that the expert's facts should be hidden or
 *  de-emphasised on the wall — the review said the memory is actively
 *  unreliable, not merely due for a look. */
const DISTRUSTED_VERDICTS = new Set<ReviewVerdict>(['wrong', 'stale']);

export function isDistrustedReview(review: Review | null | undefined): boolean {
  return Boolean(review && DISTRUSTED_VERDICTS.has(review.verdict));
}

// ------------------------------------------------- facts

/** A fact whose body is just its own title restated — `write_fact` with
 *  nothing underneath. Compares case- and whitespace-insensitively so
 *  "Pooltemp" / "pooltemp." are still recognised as the same non-statement. */
export function bodyEqualsTitle(title: string, body: string | undefined): boolean {
  if (!body) return false;
  const norm = (s: string) => s.trim().toLowerCase().replace(/[.!?\s]+$/, '');
  return norm(title) === norm(body);
}

/** How much a fact is worth showing on the wall's "Vet om huset" section,
 *  highest first.
 *
 *  Recency alone let a fact written seconds ago outrank one the brain has
 *  confirmed a dozen times over weeks — "fresh junk outranks solid facts".
 *  This scores three things instead: how many times it has been reaffirmed
 *  (`writes`, log-scaled so the 2nd write matters more than the 20th), how
 *  fresh it still is (a soft decay over a week, not a cliff), and whether the
 *  owning expert's last review can be trusted at all. A `wrong`/`stale`
 *  verdict does not zero a fact out — a demoted fact can still be the least
 *  bad thing on a thin wall — but it costs enough that a trusted expert's
 *  facts win whenever there is a real choice. */
export function factQualityScore(
  stat: Pick<FactStat, 'writes' | 'written_at'> | null | undefined,
  now: number,
  review: Review | null | undefined,
): number {
  const writes = Math.max(1, stat?.writes ?? 1);
  const writeScore = Math.log2(writes + 1); // 1 write -> 1, 3 writes -> 2, 7 -> 3, ...

  const writtenAt = stat?.written_at;
  const ageDays =
    typeof writtenAt === 'number' && Number.isFinite(writtenAt) && now
      ? Math.max(0, (now - writtenAt) / 86_400)
      : 0;
  // 1.0 fresh, decaying to ~0.13 by day 14 — old but confirmed facts still
  // place, they just no longer win over something newer.
  const freshness = 1 / (1 + ageDays / 3);

  const trust = isDistrustedReview(review) ? 0.35 : 1;

  return writeScore * (0.5 + 0.5 * freshness) * trust;
}

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

// ------------------------------------------------- slack sessions

/** "pool-pump-schedule-2026-09-20" → "pool-pump-schedule" — the topic with a
 *  trailing date stripped, lowercased and `-`-separated.
 *
 *  `/api/slack/sessions` served 42 raw sessions on the deployed system page,
 *  each carrying its own channel ID and thread timestamp baked into nothing
 *  the reader can use to see they are the same conversation continued daily.
 *  Normalising the topic is what makes the grouping in `groupSlackSessions`
 *  possible; exported on its own so the same rule can be tested and reused. */
export function normaliseSessionTopic(topic: string): string {
  const s = String(topic ?? '')
    .trim()
    .toLowerCase()
    .replace(/[\s_]+/g, '-')
    // A trailing ISO-ish date ("-2026-09-20" or "-20260920"), possibly more
    // than one in a row (a topic re-dated on consecutive days).
    .replace(/(?:-\d{4}-\d{2}-\d{2}|-\d{8})+$/g, '')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '');
  return s || 'okänt-ämne';
}

/** One normalised topic, with every raw session folded into it. */
export type SlackSessionGroup = {
  /** The normalised topic — the group's identity. */
  topic: string;
  sessions: SlackSession[];
  /** Any member with status 'open' makes the whole group open — a topic that
   *  is still being talked about should not read as settled. */
  open: boolean;
};

/** Group raw Slack sessions by normalised topic, most sessions first.
 *
 *  Within a group, channel IDs and thread timestamps stay on each member —
 *  they are not thrown away, just not the thing shown by default (see the
 *  System screen's raw-id toggle). */
export function groupSlackSessions(sessions: SlackSession[] | null | undefined): SlackSessionGroup[] {
  const byTopic = new Map<string, SlackSession[]>();
  for (const s of sessions ?? []) {
    const key = normaliseSessionTopic(s?.topic ?? '');
    const list = byTopic.get(key) ?? [];
    list.push(s);
    byTopic.set(key, list);
  }
  return [...byTopic.entries()]
    .map(([topic, members]) => ({
      topic,
      sessions: members,
      open: members.some((m) => m.status === 'open'),
    }))
    .sort((a, b) => b.sessions.length - a.sessions.length || a.topic.localeCompare(b.topic, 'sv'));
}
