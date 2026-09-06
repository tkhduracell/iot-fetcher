import { NewsItem, SourceResult } from './types';

export const MAX_STORIES = 8;
export const WINDOW_HOURS = 24;
/** Undated items can't be proven fresh, so they never dominate the briefing. */
export const MAX_UNDATED = 2;

/**
 * Malmö place names, not just "malmö" — a story headlined "Bråk i Rosengård"
 * is exactly what we want and never says the city's name.
 */
const MALMO_TERMS = [
  'malmö', 'malmo', 'malmöbo', 'limhamn', 'rosengård', 'hyllie', 'västra hamnen',
  'möllevången', 'möllan', 'kirseberg', 'oxie', 'husie', 'bunkeflo', 'sofielund',
  'triangeln', 'värnhem', 'davidshall', 'lindängen', 'holma', 'kroksbäck',
  'augustenborg', 'södervärn', 'öresundsbron', 'hyllievång',
  'mff', 'redhawks', 'kockum', 'turning torso', 'katrinelund',
];

export function isMalmoRelevant(item: NewsItem): boolean {
  const hay = `${item.title} ${item.summary}`.toLowerCase();
  return MALMO_TERMS.some((t) => hay.includes(t));
}

/**
 * Malmödirekt's feed mixes an events calendar in with the news. Those entries
 * are recently *published* but describe something that has not happened yet, so
 * a briefing built from them announces future concerts as if they were news.
 */
const EVENT_CATEGORIES = ['evenemang', 'event', 'kalender', 'på gång'];
const EVENT_URL_SEGMENTS = ['/evenemang/', '/event/', '/kalender/'];

export function isEvent(item: NewsItem): boolean {
  if (item.categories.some((c) => EVENT_CATEGORIES.includes(c))) return true;
  const url = item.url.toLowerCase();
  return EVENT_URL_SEGMENTS.some((seg) => url.includes(seg));
}

const STOPWORDS = new Set([
  'i', 'på', 'för', 'med', 'om', 'och', 'att', 'den', 'det', 'som', 'till',
  'av', 'en', 'ett', 'är', 'har', 'blir', 'efter', 'vid', 'från', 'de',
]);

function tokenize(title: string): string[] {
  return title
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 1 && !STOPWORDS.has(w));
}

/** Two headlines describe the same story if the shorter is mostly inside the longer. */
export function isSameStory(a: NewsItem, b: NewsItem): boolean {
  if (a.url && a.url === b.url) return true;
  const ta = tokenize(a.title);
  const tb = tokenize(b.title);
  if (ta.length === 0 || tb.length === 0) return false;
  const [short, long] = ta.length <= tb.length ? [ta, tb] : [tb, ta];
  const longSet = new Set(long);
  const overlap = short.filter((w) => longSet.has(w)).length;
  return overlap / short.length >= 0.6;
}

/** Keeps the richer copy but remembers every outlet that carried the story. */
function merge(a: NewsItem, b: NewsItem): NewsItem {
  const primary = b.summary.length > a.summary.length ? b : a;
  const other = primary === a ? b : a;
  return {
    ...primary,
    publishedAt: primary.publishedAt ?? other.publishedAt,
    sources: [...new Set([...a.sources, ...b.sources])],
  };
}

export function dedupe(items: NewsItem[]): NewsItem[] {
  const out: NewsItem[] = [];
  for (const item of items) {
    const idx = out.findIndex((existing) => isSameStory(existing, item));
    if (idx === -1) out.push(item);
    else out[idx] = merge(out[idx], item);
  }
  return out;
}

const SOURCE_PRIORITY = ['Malmödirekt', 'SVT Skåne', 'Sydsvenskan'];

function priority(item: NewsItem): number {
  const best = Math.min(
    ...item.sources.map((s) => {
      const i = SOURCE_PRIORITY.indexOf(s);
      return i === -1 ? SOURCE_PRIORITY.length : i;
    }),
  );
  return best;
}

export type SelectOptions = { now?: Date; maxStories?: number };

/**
 * 24h window → Malmö relevance (only where the source is region-wide) → dedup
 * → rank → interleave so the top of the briefing isn't all one outlet.
 */
export function selectStories(
  results: SourceResult[],
  relevanceFilter: Record<string, boolean>,
  { now = new Date(), maxStories = MAX_STORIES }: SelectOptions = {},
): NewsItem[] {
  const cutoff = now.getTime() - WINDOW_HOURS * 3600_000;

  const fresh = results.flatMap((r) =>
    r.items.filter((item) => {
      // Listings for upcoming events are not news from the last 24 hours.
      if (isEvent(item)) return false;
      if (item.publishedAt && item.publishedAt.getTime() < cutoff) return false;
      // Future-dated items are a feed bug; keep them rather than guess.
      if (relevanceFilter[r.source] && !isMalmoRelevant(item)) return false;
      return true;
    }),
  );

  const unique = dedupe(fresh);

  const sorted = [...unique].sort((a, b) => {
    // Dated stories always outrank ones we can't prove are recent.
    if (!!a.publishedAt !== !!b.publishedAt) return a.publishedAt ? -1 : 1;
    if (a.publishedAt && b.publishedAt) {
      const diff = b.publishedAt.getTime() - a.publishedAt.getTime();
      if (diff !== 0) return diff;
    }
    return priority(a) - priority(b);
  });

  const dated = sorted.filter((i) => i.publishedAt);
  const undated = sorted.filter((i) => !i.publishedAt).slice(0, MAX_UNDATED);

  // Interleaving keeps one prolific outlet from filling the briefing, but it
  // must not outrank recency: a story materially newer than another belongs
  // first whoever ran it. So only stories within the same age band are
  // round-robined against each other.
  return bandedInterleave(dated).concat(undated).slice(0, maxStories);
}

/** Stories this many hours apart are not interchangeable, so the newer one
 *  wins outright rather than being round-robined behind an older outlet. */
export const FRESHNESS_BAND_HOURS = 4;

/**
 * Groups the (already recency-sorted) stories into age bands and interleaves
 * sources only within a band, so variety never promotes a stale story above a
 * materially fresher one.
 */
function bandedInterleave(items: NewsItem[]): NewsItem[] {
  if (items.length === 0) return [];
  const bandMs = FRESHNESS_BAND_HOURS * 3600_000;
  const newest = items[0].publishedAt!.getTime();

  const bands = new Map<number, NewsItem[]>();
  for (const item of items) {
    const band = Math.floor((newest - item.publishedAt!.getTime()) / bandMs);
    if (!bands.has(band)) bands.set(band, []);
    bands.get(band)!.push(item);
  }

  return [...bands.keys()]
    .sort((a, b) => a - b)
    .flatMap((band) => interleaveBySource(bands.get(band)!));
}

/** Round-robins across outlets while preserving recency order within each. */
function interleaveBySource(items: NewsItem[]): NewsItem[] {
  const buckets = new Map<string, NewsItem[]>();
  for (const item of items) {
    const key = item.sources[0] ?? 'okänd';
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key)!.push(item);
  }
  const queues = [...buckets.values()];
  const out: NewsItem[] = [];
  while (out.length < items.length) {
    let moved = false;
    for (const q of queues) {
      const next = q.shift();
      if (next) {
        out.push(next);
        moved = true;
      }
    }
    if (!moved) break;
  }
  return out;
}
