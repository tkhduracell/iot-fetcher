import { NewsItem, SourceResult } from './types';
import { parseRss, parseSydsvenskan } from './parse';

export type NewsSource = {
  name: string;
  url: string;
  /** Skåne-wide sources carry non-Malmö stories; hyperlocal ones do not. */
  needsRelevanceFilter: boolean;
  parse: (body: string) => NewsItem[];
};

/**
 * Three sources, chosen after testing six. Dropped: swedenherald (403s bots),
 * thelocal (national feed, no Malmö dates), malmocity (stale shop listings).
 * More sources add failure surface without adding stories after dedup.
 */
export const SOURCES: NewsSource[] = [
  {
    name: 'Malmödirekt',
    url: 'https://malmodirekt.se/feed/',
    needsRelevanceFilter: false,
    parse: parseRss('Malmödirekt'),
  },
  {
    name: 'SVT Skåne',
    url: 'https://www.svt.se/nyheter/lokalt/skane/rss.xml',
    needsRelevanceFilter: true,
    parse: parseRss('SVT Skåne'),
  },
  {
    name: 'Sydsvenskan',
    url: 'https://www.sydsvenskan.se/malmo',
    needsRelevanceFilter: false,
    parse: parseSydsvenskan,
  },
];

export const RELEVANCE_FILTER: Record<string, boolean> = Object.fromEntries(
  SOURCES.map((s) => [s.name, s.needsRelevanceFilter]),
);

const USER_AGENT = 'iot-fetcher-news/1.0 (+https://github.com/tkhduracell/iot-fetcher)';

/**
 * Fetches every source in parallel. A failing source returns its error rather
 * than throwing, so one dead feed degrades the briefing instead of killing it —
 * but the error still reaches the response so it cannot fail silently.
 */
export async function fetchAllSources(sources: NewsSource[] = SOURCES): Promise<SourceResult[]> {
  return Promise.all(
    sources.map(async (src): Promise<SourceResult> => {
      try {
        const resp = await fetch(src.url, {
          headers: { 'User-Agent': USER_AGENT, Accept: 'application/rss+xml, application/xml, text/xml, text/html' },
          signal: AbortSignal.timeout(10_000),
          cache: 'no-store',
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const items = src.parse(await resp.text());
        if (items.length === 0) {
          // A scrape that silently yields nothing looks identical to a healthy
          // quiet feed, so surface it as a warning rather than a clean success.
          console.warn(`[news] source ${src.name} returned 0 items`);
        }
        return { source: src.name, items, error: null };
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'unknown error';
        console.warn(`[news] source ${src.name} failed: ${msg}`);
        return { source: src.name, items: [], error: msg };
      }
    }),
  );
}
