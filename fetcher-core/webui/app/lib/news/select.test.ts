import { describe, it, expect } from 'vitest';
import { selectStories, isMalmoRelevant, dedupe, isSameStory, isEvent, MAX_UNDATED } from './select';
import { NewsItem, SourceResult } from './types';

const NOW = new Date('2026-09-06T21:00:00+02:00');
const hoursAgo = (h: number) => new Date(NOW.getTime() - h * 3600_000);

const item = (over: Partial<NewsItem> = {}): NewsItem => ({
  title: 'En nyhet i Malmö',
  summary: '',
  url: `https://example.se/${Math.random()}`,
  publishedAt: hoursAgo(1),
  sources: ['Malmödirekt'],
  categories: [],
  ...over,
});

const result = (source: string, items: NewsItem[]): SourceResult => ({ source, items, error: null });
const FILTER = { 'SVT Skåne': true, 'Malmödirekt': false, Sydsvenskan: false };

describe('isMalmoRelevant', () => {
  it('matches neighbourhoods, not just the city name', () => {
    expect(isMalmoRelevant(item({ title: 'Bråk i Rosengård' }))).toBe(true);
    expect(isMalmoRelevant(item({ title: 'Nytt bygge i Hyllie' }))).toBe(true);
    expect(isMalmoRelevant(item({ title: 'Seger för MFF' }))).toBe(true);
  });

  it('rejects other Skåne towns', () => {
    expect(isMalmoRelevant(item({ title: 'Nytt bygge i Lund' }))).toBe(false);
    expect(isMalmoRelevant(item({ title: 'Brand i Hässleholm' }))).toBe(false);
  });

  it('matches on the summary too', () => {
    expect(isMalmoRelevant(item({ title: 'Stor brand', summary: 'Branden i Limhamn' }))).toBe(true);
  });
});

describe('event listings', () => {
  it('detects events by feed category', () => {
    expect(isEvent(item({ categories: ['evenemang'] }))).toBe(true);
    expect(isEvent(item({ categories: ['kommunalt'] }))).toBe(false);
  });

  it('detects events by url path when the category is missing', () => {
    expect(isEvent(item({ url: 'https://malmodirekt.se/evenemang/sagostund-mini-121871' }))).toBe(true);
    expect(isEvent(item({ url: 'https://malmodirekt.se/sport/vinst-for-rosengard' }))).toBe(false);
  });

  it('keeps upcoming events out of the briefing entirely', () => {
    // These are published recently but describe something that has not
    // happened — a news sweep must not announce them as news.
    const out = selectStories([result('Malmödirekt', [
      item({ title: 'David Urwitz på Victoriateatern', categories: ['evenemang'],
             url: 'https://malmodirekt.se/evenemang/david-urwitz-121870' }),
      item({ title: 'Slagsmål vid Amiralsgatan i Malmö', categories: ['blåljus'] }),
    ])], FILTER, { now: NOW });
    expect(out.map((i) => i.title)).toEqual(['Slagsmål vid Amiralsgatan i Malmö']);
  });
});

describe('the 24h window', () => {
  it('drops items older than 24h', () => {
    const out = selectStories(
      [result('Malmödirekt', [item({ title: 'Gammal', publishedAt: hoursAgo(30) }),
                              item({ title: 'Färsk i Malmö', publishedAt: hoursAgo(2) })])],
      FILTER, { now: NOW },
    );
    expect(out.map((i) => i.title)).toEqual(['Färsk i Malmö']);
  });

  it('keeps an item exactly inside the window', () => {
    const out = selectStories(
      [result('Malmödirekt', [item({ publishedAt: hoursAgo(23.9) })])], FILTER, { now: NOW });
    expect(out).toHaveLength(1);
  });
});

describe('relevance filtering', () => {
  it('applies to SVT but not to Malmödirekt', () => {
    const out = selectStories([
      result('SVT Skåne', [item({ title: 'Brand i Lund', sources: ['SVT Skåne'] })]),
      result('Malmödirekt', [item({ title: 'Sagostund mini', sources: ['Malmödirekt'] })]),
    ], FILTER, { now: NOW });
    expect(out.map((i) => i.title)).toEqual(['Sagostund mini']);
  });
});

describe('dedup', () => {
  it('collapses near-duplicate headlines across sources', () => {
    const a = item({ title: 'S når 20 000 dörrknackningar i Malmö', summary: 'kort', sources: ['SVT Skåne'] });
    const b = item({ title: 'Andersson når 20 000 dörrknackningar i Malmö', summary: 'en mycket längre sammanfattning', sources: ['Malmödirekt'] });
    const out = dedupe([a, b]);
    expect(out).toHaveLength(1);
    expect(out[0].summary).toBe('en mycket längre sammanfattning');
    expect(out[0].sources.sort()).toEqual(['Malmödirekt', 'SVT Skåne']);
  });

  it('does not merge genuinely different stories', () => {
    expect(dedupe([
      item({ title: 'Brand i Rosengård' }),
      item({ title: 'Nytt badhus i Hyllie' }),
    ])).toHaveLength(2);
  });

  it('treats an identical url as the same story', () => {
    const url = 'https://svt.se/a';
    expect(isSameStory(item({ title: 'Helt olika ord här', url }), item({ title: 'Inget gemensamt alls', url }))).toBe(true);
  });

  it('inherits a date from the dated copy when merging', () => {
    const out = dedupe([
      item({ title: 'Råttlarm på hamburgerställe i Malmö', publishedAt: null, sources: ['Sydsvenskan'] }),
      item({ title: 'Råttlarm på hamburgerställe i Malmö', publishedAt: hoursAgo(3), sources: ['SVT Skåne'] }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].publishedAt).not.toBeNull();
  });
});

describe('ranking and caps', () => {
  it('ranks undated items last and caps them', () => {
    const headlines = [
      'Råttlarm på hyllat hamburgerställe i Malmö',
      'Cykelbanan vid Ribersborg byggs om nästa vår',
      'Skolstarten flyttas fram för elever i Rosengård',
      'Hamnkranen monteras ned efter fyrtio år',
      'Nytt badhus invigs i Hyllie under hösten',
    ];
    const undated = headlines.map((title) =>
      item({ title, publishedAt: null, sources: ['Sydsvenskan'] }));
    const dated = [item({ title: 'Daterad i Malmö', publishedAt: hoursAgo(5) })];
    const out = selectStories([result('Sydsvenskan', undated), result('Malmödirekt', dated)], FILTER, { now: NOW });
    expect(out[0].title).toBe('Daterad i Malmö');
    expect(out.filter((i) => i.publishedAt === null)).toHaveLength(MAX_UNDATED);
  });

  it('sorts dated items newest first', () => {
    const out = selectStories([result('Malmödirekt', [
      item({ title: 'Äldre i Malmö', publishedAt: hoursAgo(10) }),
      item({ title: 'Nyare i Malmö', publishedAt: hoursAgo(1) }),
    ])], FILTER, { now: NOW });
    expect(out.map((i) => i.title)).toEqual(['Nyare i Malmö', 'Äldre i Malmö']);
  });

  it('caps at maxStories', () => {
    const headlines = [
      'Råttlarm på hyllat hamburgerställe i Malmö',
      'Cykelbanan vid Ribersborg byggs om nästa vår',
      'Skolstarten flyttas fram för elever i Rosengård',
      'Hamnkranen monteras ned efter fyrtio år',
      'Nytt badhus invigs i Hyllie under hösten',
      'Spårvagnsplanerna möter kritik från handlare',
      'Saluhallen får nya öppettider i Malmö',
      'Konserthuset säljer slut på premiärkvällen',
      'Vårdcentralen i Limhamn utökar sin bemanning',
      'Biblioteket i Kirseberg firar femtio år',
      'Torghandeln på Möllevången växer igen',
      'Busslinje fyra får tätare turer',
      'Parkeringsavgifterna höjs i Västra hamnen',
      'Idrottsplatsen i Oxie rustas upp',
      'Gatuköket vid Värnhem stänger för gott',
      'Simhallen i Husie öppnar efter renovering',
      'Biografen på Davidshall visar stumfilm',
      'Brofästet inspekteras efter rapport om sprickor',
      'Hamnbadet håller öppet längre i september',
      'Stadsdelen Sofielund får ny mötesplats',
    ];
    const many = headlines.map((title, i) => item({ title, publishedAt: hoursAgo(i % 20) }));
    expect(selectStories([result('Malmödirekt', many)], FILTER, { now: NOW })).toHaveLength(8);
  });

  it('interleaves sources so the top is not all one outlet', () => {
    const md = Array.from({ length: 4 }, (_, i) =>
      item({ title: `Malmödirekt sak ${i}`, publishedAt: hoursAgo(1), sources: ['Malmödirekt'] }));
    const svt = Array.from({ length: 4 }, (_, i) =>
      item({ title: `SVT sak ${i} i Malmö`, publishedAt: hoursAgo(2), sources: ['SVT Skåne'] }));
    const out = selectStories([result('Malmödirekt', md), result('SVT Skåne', svt)], FILTER, { now: NOW });
    expect(new Set(out.slice(0, 4).map((i) => i.sources[0])).size).toBeGreaterThan(1);
  });

  it('returns [] when every source is empty', () => {
    expect(selectStories([result('Malmödirekt', []), result('SVT Skåne', [])], FILTER, { now: NOW })).toEqual([]);
  });

  it('still works when a source failed', () => {
    const failed: SourceResult = { source: 'SVT Skåne', items: [], error: 'HTTP 500' };
    const out = selectStories([failed, result('Malmödirekt', [item()])], FILTER, { now: NOW });
    expect(out).toHaveLength(1);
  });
});
