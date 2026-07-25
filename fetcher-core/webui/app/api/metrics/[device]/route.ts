import { NextRequest, NextResponse } from 'next/server';

type MetricDef = {
  title: string;
  unit: string;
  decimals: number;
  key: string;
  metric: string;
  labels: Record<string, string>;
  /** Range-vector aggregation applied to the selector. Defaults to avg_over_time. */
  agg?: string;
  /** Range-vector window. Defaults to 5m. */
  window?: string;
};

// Titles are rendered by the Garmin watch app, whose font has no Swedish
// diacritics — keep them ASCII ("Inkop", not "Inköp").
const METRIC_DEFS: MetricDef[] = [
  {
    title: "Batteri",
    unit: "%",
    decimals: 0,
    key: "battery_soc",
    metric: "sigenergy_battery_soc_percent",
    labels: {},
  },
  {
    title: "Solceller",
    unit: "kW",
    decimals: 1,
    key: "solar_power",
    metric: "sigenergy_pv_power_power_kw",
    labels: { string: "total" },
  },
  {
    title: "Inkop",
    unit: "kW",
    decimals: 1,
    key: "grid_power",
    metric: "sigenergy_grid_power_net_power_kw",
    labels: {},
  },
  {
    title: "Bil",
    unit: "%",
    decimals: 0,
    key: "car_soc",
    metric: "ha_volvo_xc40_battery_value",
    labels: {},
    // The car only reports while awake/connected, so samples are sparse and
    // averaging a 5m window usually yields nothing. Take the last known SoC
    // over a long window instead.
    agg: "last_over_time",
    window: "24h",
  },
];

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ device: string }> }
) {
  const { device } = await params;

  if (device !== 'garmin') {
    return new NextResponse('Device not supported', { status: 400 });
  }

  const influxHost = process.env.INFLUX_HOST;
  const influxToken = process.env.INFLUX_TOKEN;

  if (!influxHost) {
    return new NextResponse('Missing INFLUX_HOST', { status: 500 });
  }
  if (!influxToken) {
    return new NextResponse('Missing INFLUX_TOKEN', { status: 500 });
  }

  const settled = await Promise.all(METRIC_DEFS.map(async (d) => {
    const labelSelector = Object.entries(d.labels)
      .map(([k, v]) => `${k}="${v}"`)
      .join(',');
    let selector = d.metric;
    if (labelSelector) {
      selector += '{' + labelSelector + '}';
    }
    const query = `${d.agg ?? 'avg_over_time'}(${selector}[${d.window ?? '5m'}])`;

    try {
      const resp = await fetch(
        `${influxHost}/api/v1/query?${new URLSearchParams({ query })}`,
        {
          headers: {
            'Authorization': `Bearer ${influxToken}`,
            'Accept': 'application/json',
          },
          signal: AbortSignal.timeout(10000),
          cache: 'no-store',
        }
      );

      if (!resp.ok) {
        console.warn(`PromQL query failed for ${d.key}: ${await resp.text()}`);
        return null;
      }

      const data = await resp.json();
      const promResult = data?.data?.result ?? [];

      if (promResult.length > 0) {
        const value = parseFloat(promResult[0].value[1]);
        const { metric: _m, labels: _l, agg: _a, window: _w, ...meta } = d;
        return { ...meta, data: value };
      }

      console.warn(`No data returned for ${d.key}`);
      return null;
    } catch (e) {
      console.error(`Error querying for ${d.key}:`, e);
      return null;
    }
  }));

  // Preserves METRIC_DEFS order, so the watch app's tile layout stays stable.
  const results = settled.filter((r) => r !== null);

  if (results.length === 0) {
    return NextResponse.json({ error: "No data available" }, { status: 500 });
  }

  return NextResponse.json(results);
}

export async function HEAD(
  request: NextRequest,
  context: { params: Promise<{ device: string }> }
) {
  return GET(request, context);
}
