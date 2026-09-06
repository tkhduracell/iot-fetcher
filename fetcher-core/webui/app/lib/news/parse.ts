import { XMLParser } from 'fast-xml-parser';
import { NewsItem } from './types';

const parser = new XMLParser({ ignoreAttributes: false, trimValues: true });

const ENTITIES: Record<string, string> = {
  '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"',
  '&apos;': "'", '&nbsp;': ' ', '&hellip;': '…',
  '&ndash;': '–', '&mdash;': '—', '&laquo;': '«', '&raquo;': '»',
  // Swedish feeds emit the accented names, and these reach the TTS verbatim
  // if they are not decoded.
  '&aring;': 'å', '&auml;': 'ä', '&ouml;': 'ö',
  '&aelig;': 'æ', '&oslash;': 'ø', '&eacute;': 'é', '&egrave;': 'è',
  '&uuml;': 'ü', '&adiaeresis;': 'ä',
};

/**
 * A feed can carry a numeric entity outside the Unicode range, and
 * String.fromCodePoint throws RangeError on those. Left unguarded that
 * exception escapes parseRss and discards every item from the feed, so an
 * unusable code point falls back to the raw text instead.
 */
function codePoint(value: number, raw: string): string {
  if (!Number.isInteger(value) || value < 0 || value > 0x10ffff) return raw;
  // Lone surrogates are valid code points to fromCodePoint but not to XML.
  try {
    return String.fromCodePoint(value);
  } catch {
    return raw;
  }
}

/** Strips tags and decodes the entities that actually show up in these feeds. */
export function stripHtml(input: string): string {
  return input
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x([0-9a-f]+);/gi, (m, hex) => codePoint(parseInt(hex, 16), m))
    .replace(/&#(\d+);/g, (m, code) => codePoint(Number(code), m))
    .replace(/&[a-z]+;/gi, (m) => ENTITIES[m.toLowerCase()] ?? m)
    .replace(/\s+/g, ' ')
    .trim();
}

/** A feed with exactly one <item> parses to an object, not an array. */
function asArray<T>(value: T | T[] | undefined): T[] {
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value : [value];
}

function text(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  // fast-xml-parser puts mixed content under #text
  if (value && typeof value === 'object' && '#text' in value) {
    return String((value as { '#text': unknown })['#text']);
  }
  return '';
}

function parseDate(raw: string): Date | null {
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Builds an RSS 2.0 / Atom parser bound to a source name. */
export function parseRss(source: string) {
  return (body: string): NewsItem[] => {
    let doc: Record<string, any>;
    try {
      doc = parser.parse(body);
    } catch {
      // Malformed XML is a source failure, not a crash — return nothing.
      return [];
    }

    const rssItems = asArray(doc?.rss?.channel?.item);
    const atomItems = asArray(doc?.feed?.entry);
    const raw = rssItems.length > 0 ? rssItems : atomItems;

    return raw
      .map((it: Record<string, any>): NewsItem => {
        const link = it.link;
        const url = typeof link === 'object' && link?.['@_href'] ? String(link['@_href']) : text(link);
        const categories = asArray(it.category)
          .map((c) => text(c).toLowerCase().trim())
          .filter(Boolean);
        return {
          title: stripHtml(text(it.title)),
          summary: stripHtml(text(it.description ?? it.summary ?? it.content)),
          url: url.trim(),
          publishedAt: parseDate(text(it.pubDate ?? it.published ?? it.updated)),
          sources: [source],
          categories: [...new Set(categories)],
        };
      })
      .filter((it) => it.title.length > 0);
  };
}

const SYDSVENSKAN_BASE = 'https://www.sydsvenskan.se';

/**
 * Sydsvenskan publishes no RSS for /malmo, so scrape the teaser attributes.
 * Attribute order is not guaranteed by HTML — the live page emits path before
 * title — so match both orders and merge by path.
 */
export function parseSydsvenskan(body: string): NewsItem[] {
  const byPath = new Map<string, string>();

  const patterns = [
    /data-article-path="([^"]+)"[^>]*?data-article-title="([^"]+)"/g,
    /data-article-title="([^"]+)"[^>]*?data-article-path="([^"]+)"/g,
  ];

  patterns.forEach((re, idx) => {
    for (const m of body.matchAll(re)) {
      const [path, title] = idx === 0 ? [m[1], m[2]] : [m[2], m[1]];
      const clean = stripHtml(title);
      if (clean && !byPath.has(path)) byPath.set(path, clean);
    }
  });

  return [...byPath.entries()].map(([path, title]) => ({
    title,
    summary: '',
    url: path.startsWith('http') ? path : `${SYDSVENSKAN_BASE}${path}`,
    // The <time> elements are absent on lead teasers and the rest are relative
    // strings ("I förrgår"), so timing is genuinely unknown here.
    publishedAt: null,
    sources: ['Sydsvenskan'],
    categories: [],
  }));
}
