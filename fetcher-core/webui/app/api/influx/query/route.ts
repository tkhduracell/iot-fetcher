import { NextRequest, NextResponse } from 'next/server';

const EXCLUDED_HEADERS = new Set([
  'content-encoding', 'content-length', 'transfer-encoding', 'connection'
]);

export async function GET(request: NextRequest) {
  const influxHost = process.env.INFLUX_HOST;
  const influxToken = process.env.INFLUX_TOKEN;

  if (!influxHost || !influxToken) {
    return NextResponse.json({ results: [] });
  }

  const searchParams = request.nextUrl.searchParams;
  const url = `${influxHost}/query?${searchParams.toString()}`;

  const resp = await fetch(url, {
    headers: {
      'Authorization': `Bearer ${influxToken}`,
      'Accept': 'application/json',
    },
    signal: AbortSignal.timeout(10000),
    cache: 'no-store',
  });

  const body = await resp.arrayBuffer();
  const headers = new Headers();
  resp.headers.forEach((value, key) => {
    if (!EXCLUDED_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });

  return new NextResponse(body, {
    status: resp.status,
    headers,
  });
}
