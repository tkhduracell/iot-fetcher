import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';

function call(path: string) {
  const [routePath] = path.split('?');
  const segments = routePath.split('/');
  return GET(
    new NextRequest(`http://localhost/api/influx/${path}`),
    { params: Promise.resolve({ version: segments[0], route: segments.slice(1) }) },
  );
}

describe('GET /api/influx/api/[version]/[...route]', () => {
  const originalNodeEnv = process.env.NODE_ENV;

  beforeEach(() => {
    delete process.env.INFLUX_HOST;
    delete process.env.INFLUX_TOKEN;
    delete process.env.INFLUX_MOCK;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.stubEnv('NODE_ENV', originalNodeEnv ?? 'test');
  });

  it('mocks an instant query when INFLUX_HOST is unset (local dev)', async () => {
    vi.stubEnv('NODE_ENV', 'development');

    const resp = await call('v1/query?query=sigenergy_battery_soc_percent');
    const body = await resp.json();

    expect(resp.status).toBe(200);
    expect(body.status).toBe('success');
    expect(body.data.resultType).toBe('vector');
    const [, value] = body.data.result[0].value;
    expect(Number(value)).toBeGreaterThanOrEqual(0);
  });

  it('mocks a range query when INFLUX_HOST is unset (local dev)', async () => {
    vi.stubEnv('NODE_ENV', 'development');

    const start = new Date(Date.now() - 3600_000).toISOString();
    const end = new Date().toISOString();
    const resp = await call(`v1/query_range?query=air_quality_aqi&start=${start}&end=${end}&step=5m`);
    const body = await resp.json();

    expect(body.data.resultType).toBe('matrix');
    expect(body.data.result[0].values.length).toBeGreaterThan(1);
  });

  it('does not mock in production without an explicit override', async () => {
    vi.stubEnv('NODE_ENV', 'production');

    const resp = await call('v1/query?query=up');
    expect(resp.status).toBe(200);
    const body = await resp.text();
    expect(body).toBe('');
  });

  it('falls back to mock data when the upstream request fails, in dev', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    process.env.INFLUX_HOST = 'http://invalid.example.invalid';
    process.env.INFLUX_TOKEN = 'test-token';
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed'); }));

    const resp = await call('v1/query?query=sigenergy_battery_soc_percent');
    const body = await resp.json();

    expect(resp.status).toBe(200);
    expect(body.data.resultType).toBe('vector');
  });

  it('returns 502 when the upstream request fails in production', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    process.env.INFLUX_HOST = 'http://invalid.example.invalid';
    process.env.INFLUX_TOKEN = 'test-token';
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed'); }));

    const resp = await call('v1/query?query=up');
    expect(resp.status).toBe(502);
  });

  it('proxies a successful upstream response through unchanged', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    process.env.INFLUX_HOST = 'http://vm.test';
    process.env.INFLUX_TOKEN = 'test-token';
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ status: 'success', data: { resultType: 'vector', result: [] } }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    )));

    const resp = await call('v1/query?query=up');
    const body = await resp.json();

    expect(resp.status).toBe(200);
    expect(body).toEqual({ status: 'success', data: { resultType: 'vector', result: [] } });
  });
});
