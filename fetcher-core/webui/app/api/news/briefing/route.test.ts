import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';

const FIXTURES = join(__dirname, '../../../lib/news/__fixtures__');
const fixture = (n: string) => readFileSync(join(FIXTURES, n), 'utf-8');

const SCRIPT = 'God kväll Malmö. ' + 'Det här är en helt vanlig mening om staden. '.repeat(10);

/** Routes mocked fetch by URL: the three sources plus Gemini. */
function mockFetch(over: {
  malmodirekt?: () => Promise<any>;
  svt?: () => Promise<any>;
  sydsvenskan?: () => Promise<any>;
  gemini?: () => Promise<any>;
} = {}) {
  const ok = (bodyText: string) => async () => ({ ok: true, status: 200, text: async () => bodyText });
  const geminiOk = async () => ({
    ok: true, status: 200,
    json: async () => ({ candidates: [{ content: { parts: [{ text: SCRIPT }] } }] }),
  });

  const fn = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.includes('malmodirekt')) return (over.malmodirekt ?? ok(freshFeed()))();
    if (url.includes('svt.se')) return (over.svt ?? ok(fixture('svt-skane.xml')))();
    if (url.includes('sydsvenskan')) return (over.sydsvenskan ?? ok(fixture('sydsvenskan.html')))();
    if (url.includes('generativelanguage')) return (over.gemini ?? geminiOk)();
    throw new Error(`unexpected url ${url}`);
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

/** The captured fixtures age out of the 24h window, so build a fresh feed. */
function freshFeed(): string {
  const now = new Date();
  const items = [
    ['Stort bygge startar i Rosengård', 'Arbetet inleds efter beslut i nämnden.'],
    ['Cykelbanan vid Ribersborg byggs om', 'Den nya sträckningen blir bredare.'],
    ['Saluhallen i Malmö får nya öppettider', 'Förändringen gäller från oktober.'],
  ].map(([t, d], i) => `<item><title>${t}</title><link>https://malmodirekt.se/${i}</link>
    <description>${d}</description>
    <pubDate>${new Date(now.getTime() - (i + 1) * 3600_000).toUTCString()}</pubDate></item>`);
  return `<?xml version="1.0"?><rss><channel><title>Malmödirekt</title>${items.join('')}</channel></rss>`;
}

async function callRoute() {
  const { POST } = await import('./route');
  return POST();
}

beforeEach(() => {
  vi.resetModules();
  process.env.GEMINI_API_KEY = 'test-key';
  delete process.env.NEWS_ROOM;
  delete process.env.NEWS_VOLUME;
  delete process.env.NEWS_MODEL;
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('POST /api/news/briefing', () => {
  it('returns 503 when the Gemini key is unset', async () => {
    delete process.env.GEMINI_API_KEY;
    mockFetch();
    const resp = await callRoute();
    expect(resp.status).toBe(503);
    expect((await resp.json()).error).toMatch(/not configured/i);
  });

  it('returns a transcript, chunks and the target speaker', async () => {
    mockFetch();
    const resp = await callRoute();
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.transcript).toContain('Malmö');
    // One /say call: the transcript must be short enough to send whole.
    expect(encodeURIComponent(body.transcript).length).toBeLessThan(3000);
    expect(body.room).toBe('Kontor');
    expect(body.volume).toBe('30');
    expect(body.stories.length).toBeGreaterThanOrEqual(2);
    expect(body.sources.every((s: { error: null }) => s.error === null)).toBe(true);
  });

  it('still returns a briefing when one source fails', async () => {
    mockFetch({ svt: async () => ({ ok: false, status: 500, text: async () => 'boom' }) });
    const resp = await callRoute();
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.transcript).toBeTruthy();
    const svt = body.sources.find((s: { source: string }) => s.source === 'SVT Skåne');
    expect(svt.error).toMatch(/500/);
    expect(svt.count).toBe(0);
  });

  it('survives a source that times out', async () => {
    mockFetch({ sydsvenskan: async () => { throw new Error('The operation was aborted'); } });
    const resp = await callRoute();
    expect(resp.status).toBe(200);
    expect((await resp.json()).transcript).toBeTruthy();
  });

  it('reports no-news and never calls Gemini when nothing is fresh', async () => {
    const empty = async () => ({ ok: true, status: 200, text: async () => '<rss><channel></channel></rss>' });
    const fn = mockFetch({ malmodirekt: empty, svt: empty, sydsvenskan: async () => ({ ok: true, status: 200, text: async () => '<html></html>' }) });
    const resp = await callRoute();
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.transcript).toBeNull();
    expect(body.reason).toBe('no-news');
    expect(fn.mock.calls.some(([u]) => String(u).includes('generativelanguage'))).toBe(false);
  });

  it('returns 502 with detail when Gemini fails', async () => {
    mockFetch({ gemini: async () => ({ ok: false, status: 429, text: async () => 'quota exceeded' }) });
    const resp = await callRoute();
    expect(resp.status).toBe(502);
    expect((await resp.json()).error).toMatch(/429|quota/);
  });

  it('sends the real story titles to Gemini', async () => {
    const fn = mockFetch();
    await callRoute();
    const call = fn.mock.calls.find(([u]) => String(u).includes('generativelanguage'));
    const sent = JSON.parse(String((call as unknown as [string, RequestInit])[1].body));
    expect(sent.contents[0].parts[0].text).toContain('Rosengård');
    expect(sent.systemInstruction.parts[0].text).toContain('Malmökollen');
  });

  it('honours NEWS_ROOM, NEWS_VOLUME and NEWS_MODEL overrides', async () => {
    process.env.NEWS_ROOM = 'Vardagsrum';
    process.env.NEWS_VOLUME = '35';
    process.env.NEWS_MODEL = 'gemini-test-model';
    const fn = mockFetch();
    const body = await (await callRoute()).json();
    expect(body.room).toBe('Vardagsrum');
    expect(body.volume).toBe('35');
    expect(fn.mock.calls.some(([u]) => String(u).includes('gemini-test-model'))).toBe(true);
  });
});
