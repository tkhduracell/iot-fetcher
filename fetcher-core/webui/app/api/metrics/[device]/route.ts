import { NextRequest, NextResponse } from 'next/server';

const METRIC_DEFS = [
  {
    title: "Batteri",
    unit: "%",
    decimals: 0,
    key: "battery_soc",
    metric: "sigenergy_battery_soc_percent",
    labels: {} as Record<string, string>,
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
    labels: {} as Record<string, string>,
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

  const results: any[] = [];

  for (const d of METRIC_DEFS) {
    const labelSelector = Object.entries(d.labels)
      .map(([k, v]) => `${k}="${v}"`)
      .join(',');
    let selector = d.metric;
    if (labelSelector) {
      selector += '{' + labelSelector + '}';
    }
    const query = `avg_over_time(${selector}[5m])`;

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
        continue;
      }

      const data = await resp.json();
      const promResult = data?.data?.result ?? [];

      if (promResult.length > 0) {
        const value = parseFloat(promResult[0].value[1]);
        const { metric: _m, labels: _l, ...meta } = d;
        results.push({ ...meta, data: value });
      } else {
        console.warn(`No data returned for ${d.key}`);
      }
    } catch (e) {
      console.error(`Error querying for ${d.key}:`, e);
      continue;
    }
  }

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
