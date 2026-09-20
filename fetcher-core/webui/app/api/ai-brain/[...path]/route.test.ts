import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { NextRequest } from 'next/server';
import * as route from './route';

const { GET } = route;

/** Stub upstream ai-brain; records every URL the route fetches. */
function stubAiBrain(
  resp: { status?: number; body?: string; contentType?: string } = {},
) {
  const fetchMock = vi.fn(async (_url: string) => ({
    status: resp.status ?? 200,
    headers: new Headers({ 'content-type': resp.contentType ?? 'application/json' }),
    text: async () => resp.body ?? '{"ok":true}',
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function call(path: string) {
  const [routePath] = path.split('?');
  return GET(
    new NextRequest(`http://localhost/api/ai-brain/${path}`),
    { params: Promise.resolve({ path: routePath.split('/') }) },
  );
}

describe('GET /api/ai-brain/[...path]', () => {
  beforeEach(() => {
    delete process.env.AI_BRAIN_URL;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it('proxies to the compose service name by default', async () => {
    const fetchMock = stubAiBrain({ body: '{"ok":true,"uptime_s":12}' });

    const resp = await call('healthz');

    expect(resp.status).toBe(200);
    expect(await resp.json()).toEqual({ ok: true, uptime_s: 12 });
    expect(fetchMock.mock.calls[0][0]).toBe('http://ai-brain:8091/healthz');
  });

  it('honours a custom AI_BRAIN_URL, trimming a trailing slash', async () => {
    vi.stubEnv('AI_BRAIN_URL', 'http://192.168.68.87:8091/');
    const fetchMock = stubAiBrain();

    await call('api/status');

    expect(fetchMock.mock.calls[0][0]).toBe('http://192.168.68.87:8091/api/status');
  });

  it('reports 503 without calling upstream when AI_BRAIN_URL is emptied', async () => {
    vi.stubEnv('AI_BRAIN_URL', '');
    const fetchMock = stubAiBrain();

    const resp = await call('api/status');

    expect(resp.status).toBe(503);
    expect(await resp.json()).toEqual({ error: 'ai-brain not configured' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('forwards the query string', async () => {
    const fetchMock = stubAiBrain();

    await GET(
      new NextRequest('http://localhost/api/ai-brain/api/agents/brain/journal?days=7'),
      { params: Promise.resolve({ path: ['api', 'agents', 'brain', 'journal'] }) },
    );

    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://ai-brain:8091/api/agents/brain/journal?days=7',
    );
  });

  it('passes an upstream 404 through with its own body', async () => {
    stubAiBrain({ status: 404, body: '{"error":"unknown agent: nope"}' });

    const resp = await call('api/agents/nope');

    expect(resp.status).toBe(404);
    expect(await resp.json()).toEqual({ error: 'unknown agent: nope' });
  });

  it('reports 502 when ai-brain is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED'); }));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    const resp = await call('api/status');

    expect(resp.status).toBe(502);
    expect(await resp.json()).toEqual({ error: 'ai-brain unavailable' });
  });

  it.each([
    ['api/../admin', ['api', '..', 'admin']],
    ['admin', ['admin']],
    ['..', ['..']],
    ['metrics', ['metrics']],
  ])('blocks %s without touching upstream', async (_label, segments) => {
    const fetchMock = stubAiBrain();

    const resp = await GET(
      new NextRequest('http://localhost/api/ai-brain/x'),
      { params: Promise.resolve({ path: segments }) },
    );

    expect(resp.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('marks every response no-store so the dashboard never reads a cached brain', async () => {
    stubAiBrain();

    const resp = await call('api/agents');

    expect(resp.headers.get('cache-control')).toBe('no-store');
  });

  it('is read-only: no write verbs are exported', () => {
    expect(route).not.toHaveProperty('POST');
    expect(route).not.toHaveProperty('PUT');
    expect(route).not.toHaveProperty('DELETE');
  });

  describe('api/feed (SSE)', () => {
    /** Unlike `stubAiBrain`, this stub answers with a real `ReadableStream`
     *  body -- the one thing the JSON stub above has no reason to carry, and
     *  the one thing this branch actually forwards untouched. */
    function stubAiBrainStream(chunks: string[]) {
      const stream = new ReadableStream({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
          controller.close();
        },
      });
      const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
        status: 200,
        body: stream,
      }));
      vi.stubGlobal('fetch', fetchMock);
      return fetchMock;
    }

    it('streams the upstream body through untouched, as text/event-stream', async () => {
      stubAiBrainStream(['data: {"type":"round_started"}\n\n']);

      const resp = await call('api/feed');

      expect(resp.status).toBe(200);
      expect(resp.headers.get('content-type')).toBe('text/event-stream');
      expect(resp.headers.get('cache-control')).toBe('no-store');
      const reader = resp.body!.getReader();
      const { value } = await reader.read();
      expect(new TextDecoder().decode(value)).toBe('data: {"type":"round_started"}\n\n');
    });

    it('does not set the 10s JSON-route timeout on the upstream fetch', async () => {
      const fetchMock = stubAiBrainStream([]);

      await call('api/feed');

      const [, init] = fetchMock.mock.calls[0];
      expect(init?.signal).toBeUndefined();
    });

    it('reports 502 without a body if the upstream stream has none', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, body: null })));

      const resp = await call('api/feed');

      expect(resp.status).toBe(502);
      expect(await resp.json()).toEqual({ error: 'ai-brain returned no stream body' });
    });

    it('reports 502 when ai-brain is unreachable', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED'); }));
      vi.spyOn(console, 'error').mockImplementation(() => {});

      const resp = await call('api/feed');

      expect(resp.status).toBe(502);
      expect(await resp.json()).toEqual({ error: 'ai-brain unavailable' });
    });
  });
});
