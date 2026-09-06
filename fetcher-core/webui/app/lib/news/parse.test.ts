import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';
import { parseRss, parseSydsvenskan, stripHtml } from './parse';

const fixture = (name: string) =>
  readFileSync(join(__dirname, '__fixtures__', name), 'utf-8');

describe('stripHtml', () => {
  it('removes tags and decodes entities', () => {
    expect(stripHtml('<p>Caf&eacute; &amp; bar</p>')).toBe('Café & bar');
    expect(stripHtml('R&aring;ttlarm &#039;i&#039; Malm&ouml;')).toBe("Råttlarm 'i' Malmö");
  });

  it('leaves an out-of-range numeric entity alone instead of throwing', () => {
    // String.fromCodePoint throws RangeError above 0x10FFFF; unguarded that
    // exception escapes parseRss and discards the whole feed.
    expect(stripHtml('X &#99999999999; Y')).toBe('X &#99999999999; Y');
    expect(stripHtml('X &#xFFFFFFFF; Y')).toBe('X &#xFFFFFFFF; Y');
    expect(stripHtml('R&#229;tt')).toBe('Rått');
  });

  it('collapses whitespace', () => {
    expect(stripHtml('a\n\n   b')).toBe('a b');
  });
});

describe('parseRss', () => {
  it('extracts fields from the Malmödirekt feed', () => {
    const items = parseRss('Malmödirekt')(fixture('malmodirekt.xml'));
    expect(items.length).toBe(3);
    const first = items[0];
    expect(first.title.length).toBeGreaterThan(0);
    expect(first.url).toMatch(/^https?:\/\//);
    expect(first.publishedAt).toBeInstanceOf(Date);
    expect(first.sources).toEqual(['Malmödirekt']);
  });

  it('extracts summaries from the SVT feed', () => {
    const items = parseRss('SVT Skåne')(fixture('svt-skane.xml'));
    expect(items.length).toBe(3);
    expect(items[0].summary.length).toBeGreaterThan(10);
    // Summaries must not carry markup through to the TTS prompt.
    expect(items[0].summary).not.toMatch(/<[a-z]/i);
  });

  it('handles a feed with exactly one item (object, not array)', () => {
    const single = `<?xml version="1.0"?><rss><channel>
      <item><title>Ensam nyhet</title><link>https://x.se/1</link>
      <pubDate>Sun, 06 Sep 2026 12:00:00 +0200</pubDate>
      <description>Bara en.</description></item>
    </channel></rss>`;
    const items = parseRss('Test')(single);
    expect(items).toHaveLength(1);
    expect(items[0].title).toBe('Ensam nyhet');
  });

  it('keeps the other items when one has a bad numeric entity', () => {
    const feed = `<rss><channel>
      <item><title>Bra nyhet ett</title><link>https://x/1</link></item>
      <item><title>Trasig &#99999999999; nyhet</title><link>https://x/2</link></item>
      <item><title>Bra nyhet tre</title><link>https://x/3</link></item>
    </channel></rss>`;
    expect(parseRss('Test')(feed)).toHaveLength(3);
  });

  it('returns [] for malformed XML rather than throwing', () => {
    expect(parseRss('Test')('<rss><channel><item>broken')).toEqual([]);
    expect(parseRss('Test')('not xml at all')).toEqual([]);
  });

  it('drops entries with no title', () => {
    const feed = `<rss><channel>
      <item><title>Bra</title><link>https://x.se/1</link></item>
      <item><link>https://x.se/2</link></item>
    </channel></rss>`;
    expect(parseRss('Test')(feed)).toHaveLength(1);
  });
});

describe('parseSydsvenskan', () => {
  it('extracts titles and absolute urls from real teaser markup', () => {
    const items = parseSydsvenskan(fixture('sydsvenskan.html'));
    expect(items.length).toBeGreaterThan(0);
    expect(items[0].title).toContain('Malmö');
    expect(items[0].url).toMatch(/^https:\/\/www\.sydsvenskan\.se\//);
    expect(items[0].sources).toEqual(['Sydsvenskan']);
  });

  it('marks publishedAt as null — the page has no usable timestamps', () => {
    const items = parseSydsvenskan(fixture('sydsvenskan.html'));
    expect(items.every((i) => i.publishedAt === null)).toBe(true);
  });

  it('handles both attribute orders', () => {
    const pathFirst = '<div data-article-path="/malmo/a/" data-article-title="Alfa"></div>';
    const titleFirst = '<div data-article-title="Beta" data-article-path="/malmo/b/"></div>';
    expect(parseSydsvenskan(pathFirst)[0].title).toBe('Alfa');
    expect(parseSydsvenskan(titleFirst)[0].title).toBe('Beta');
    expect(parseSydsvenskan(pathFirst + titleFirst)).toHaveLength(2);
  });

  it('deduplicates repeated teasers for the same article', () => {
    const dup = '<div data-article-path="/malmo/a/" data-article-title="Alfa"></div>'.repeat(3);
    expect(parseSydsvenskan(dup)).toHaveLength(1);
  });

  it('returns [] when the markup has no teasers', () => {
    expect(parseSydsvenskan('<html><body><p>inget</p></body></html>')).toEqual([]);
  });
});
