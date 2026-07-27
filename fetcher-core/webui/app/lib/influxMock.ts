/**
 * Generates plausible-looking PromQL results so the dashboard can be
 * exercised locally without a reachable InfluxDB/VictoriaMetrics backend.
 * Values are deterministic per metric name with a slow drift over time, so
 * a running dashboard still looks "alive" across refreshes.
 */

function hashSeed(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Rough value ranges per metric-name pattern, so mocked tiles land in the
// same ballpark as the real thing (temperatures look like temperatures, a
// percentage stays within 0-100, etc). Not meant to be exhaustive.
const RANGE_HINTS: Array<{ test: RegExp; min: number; max: number }> = [
  { test: /soc_percent|battery.*value/i, min: 20, max: 95 },
  { test: /aqi/i, min: 20, max: 90 },
  { test: /temperature/i, min: 15, max: 26 },
  { test: /speed|rpm/i, min: 1800, max: 2800 },
  { test: /charging_power/i, min: 0, max: 2000 },
  { test: /power_kw|_kw$/i, min: 0, max: 4 },
  { test: /cost/i, min: 5, max: 40 },
  { test: /consumption/i, min: 5, max: 35 },
];
const DEFAULT_RANGE = { min: 0, max: 100 };

function rangeFor(name: string) {
  return RANGE_HINTS.find(h => h.test.test(name)) ?? DEFAULT_RANGE;
}

const PROMQL_FUNCTIONS = new Set([
  'avg_over_time', 'last_over_time', 'sum_over_time', 'max_over_time', 'min_over_time',
  'avg', 'sum', 'max', 'min', 'clamp_min', 'clamp_max', 'rate', 'on', 'by', 'without',
]);

/** Best-effort extraction of the metric name a PromQL expression reads,
 * so the mock value can be shaped (roughly) like the real thing. */
function extractMetricName(query: string): string {
  const tokens = query.match(/[a-zA-Z_:][a-zA-Z0-9_:]*(?=[{[]|\s|$)/g) ?? [];
  const candidates = tokens.filter(t => t.includes('_') && !PROMQL_FUNCTIONS.has(t));
  candidates.sort((a, b) => b.length - a.length);
  return candidates[0] ?? query;
}

function parseStepMs(step: string): number {
  const match = step.match(/^(\d+)([smhd])$/);
  if (!match) return 5 * 60000;
  const unitMs: Record<string, number> = { s: 1000, m: 60000, h: 3600000, d: 86400000 };
  return parseInt(match[1], 10) * unitMs[match[2]];
}

function valueAt(name: string, timeMs: number): number {
  const { min, max } = rangeFor(name);
  const mid = (min + max) / 2;
  const amp = (max - min) / 2;
  const phase = mulberry32(hashSeed(name))() * Math.PI * 2;
  // One full slow cycle every ~6 hours, so a running dashboard drifts visibly.
  const wave = Math.sin((timeMs / (6 * 3600000)) * Math.PI * 2 + phase);
  const noise = mulberry32(hashSeed(`${name}:${Math.floor(timeMs / 60000)}`))() - 0.5;
  return mid + wave * amp * 0.7 + noise * amp * 0.15;
}

export function mockInstantResult(query: string) {
  const name = extractMetricName(query);
  const nowMs = Date.now();
  return {
    status: 'success',
    data: {
      resultType: 'vector',
      result: [{
        metric: { __name__: name },
        value: [Math.floor(nowMs / 1000), valueAt(name, nowMs).toFixed(3)],
      }],
    },
  };
}

export function mockRangeResult(query: string, start: string, end: string, step: string) {
  const name = extractMetricName(query);
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  const stepMs = parseStepMs(step);
  const values: Array<[number, string]> = [];
  if (Number.isFinite(startMs) && Number.isFinite(endMs) && stepMs > 0) {
    for (let t = startMs; t <= endMs && values.length < 1000; t += stepMs) {
      values.push([Math.floor(t / 1000), valueAt(name, t).toFixed(3)]);
    }
  }
  return {
    status: 'success',
    data: {
      resultType: 'matrix',
      result: [{ metric: { __name__: name }, values }],
    },
  };
}

/**
 * Mock fallback only kicks in outside production (or with an explicit
 * override) — a broken INFLUX_HOST in the deployed app should surface as a
 * real error, not silently paper over an outage.
 */
export function shouldMockOnError(): boolean {
  return process.env.NODE_ENV !== 'production' || process.env.INFLUX_MOCK === '1';
}
