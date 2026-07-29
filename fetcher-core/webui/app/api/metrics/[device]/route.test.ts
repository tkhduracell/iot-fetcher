import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';

// Captures the PromQL of every query the route issues, keyed by nothing —
// order matches the route's fan-out, which is not guaranteed, so assert by match.
let queries: string[] = [];

/** Stub VM: returns `values[metricName]`, or an empty result if absent. */
function stubVictoriaMetrics(values: Record<string, number>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const query = new URL(url).searchParams.get('query') ?? '';
    queries.push(query);
    const name = Object.keys(values).find((m) => query.includes(m));
    const result = name === undefined ? [] : [{
      metric: { __name__: name },
      value: [1753447000, String(values[name])],
    }];
    return {
      ok: true,
      json: async () => ({ status: 'success', data: { resultType: 'vector', result } }),
    };
  }));
}

function call(device = 'garmin') {
  return GET(
    new NextRequest(`http://localhost/api/metrics/${device}`),
    { params: Promise.resolve({ device }) },
  );
}

const ALL_METRICS = {
  sigenergy_battery_soc_percent: 64,
  sigenergy_pv_power_power_kw: 2.5,
  sigenergy_grid_power_net_power_kw: -1.25,
  ha_volvo_xc40_battery_value: 82,
};

describe('GET /api/metrics/garmin', () => {
  beforeEach(() => {
    queries = [];
    process.env.INFLUX_HOST = 'http://vm.test';
    process.env.INFLUX_TOKEN = 'test-token';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('includes the car SoC tile', async () => {
    stubVictoriaMetrics(ALL_METRICS);
    const body = await (await call()).json();

    expect(body).toContainEqual({
      title: 'Bil',
      unit: '%',
      decimals: 0,
      key: 'car_soc',
      data: 82,
    });
  });

  it('reads car SoC as the last sample over a long window, since the car reports sparsely', async () => {
    stubVictoriaMetrics(ALL_METRICS);
    await call();

    const carQuery = queries.find((q) => q.includes('ha_volvo_xc40_battery_value'));
    expect(carQuery).toBe('last_over_time(ha_volvo_xc40_battery_value[24h])');
  });

  it('keeps the existing 5m average for the frequently-sampled sigenergy metrics', async () => {
    stubVictoriaMetrics(ALL_METRICS);
    await call();

    expect(queries).toContain('avg_over_time(sigenergy_battery_soc_percent[5m])');
    expect(queries).toContain('avg_over_time(sigenergy_pv_power_power_kw{string="total"}[5m])');
    expect(queries).toContain('avg_over_time(sigenergy_grid_power_net_power_kw[5m])');
  });

  it('does not leak query internals into the payload', async () => {
    stubVictoriaMetrics(ALL_METRICS);
    const body = await (await call()).json();

    for (const tile of body) {
      expect(Object.keys(tile).sort()).toEqual(
        ['data', 'decimals', 'key', 'title', 'unit'],
      );
    }
  });

  it('returns tiles in a stable order so the watch layout does not shuffle', async () => {
    stubVictoriaMetrics(ALL_METRICS);
    const body = await (await call()).json();

    expect(body.map((t: { key: string }) => t.key)).toEqual(
      ['battery_soc', 'solar_power', 'grid_power', 'car_soc'],
    );
  });

  it('omits a tile when the car has not reported, keeping the others in order', async () => {
    const { ha_volvo_xc40_battery_value: _absent, ...rest } = ALL_METRICS;
    stubVictoriaMetrics(rest);
    const body = await (await call()).json();

    expect(body.map((t: { key: string }) => t.key)).toEqual(
      ['battery_soc', 'solar_power', 'grid_power'],
    );
  });

  it('rejects unsupported devices', async () => {
    stubVictoriaMetrics(ALL_METRICS);
    expect((await call('fitbit')).status).toBe(400);
  });
});
