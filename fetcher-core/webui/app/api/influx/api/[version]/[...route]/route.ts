import { NextRequest, NextResponse } from 'next/server';
import { mockInstantResult, mockRangeResult, shouldMockOnError } from '../../../../../lib/influxMock';

const ALLOWED_ROUTES: Record<string, string[]> = {
  v1: ['query', 'query_range', 'labels', 'label'],
  v2: ['query', 'health'],
  v3: ['query_sql', 'query_influxql', 'health'],
};

const EXCLUDED_HEADERS = new Set([
  'content-encoding', 'content-length', 'transfer-encoding', 'connection'
]);

function fallbackResponse(version: string, route: string): NextResponse {
  if (route === 'health') {
    return NextResponse.json({ status: 'pass' });
  }
  if (version === 'v2' && route === 'query') {
    return new NextResponse('', { status: 200, headers: { 'content-type': 'text/csv' } });
  }
  if (version === 'v3' && (route === 'query_sql' || route === 'query_influxql')) {
    return NextResponse.json([]);
  }
  return new NextResponse('', { status: 200 });
}

function mockResponse(version: string, route: string, searchParams: URLSearchParams): NextResponse {
  if (version === 'v1' && route === 'query') {
    return NextResponse.json(mockInstantResult(searchParams.get('query') ?? ''));
  }
  if (version === 'v1' && route === 'query_range') {
    const nowIso = new Date().toISOString();
    return NextResponse.json(mockRangeResult(
      searchParams.get('query') ?? '',
      searchParams.get('start') ?? nowIso,
      searchParams.get('end') ?? nowIso,
      searchParams.get('step') ?? '5m',
    ));
  }
  return fallbackResponse(version, route);
}

async function handleRequest(
  request: NextRequest,
  { params }: { params: Promise<{ version: string; route: string[] }> }
) {
  const { version, route: routeParts } = await params;
  const route = routeParts[0];

  if (!ALLOWED_ROUTES[version]) {
    return new NextResponse('Unsupported API version', { status: 400 });
  }

  const allowedRoutes = ALLOWED_ROUTES[version] ?? [];
  if (!allowedRoutes.includes(route)) {
    return new NextResponse('Not authorized', { status: 403 });
  }

  const influxHost = process.env.INFLUX_HOST;
  const influxToken = process.env.INFLUX_TOKEN;

  if (!influxHost || !influxToken) {
    console.warn('influx/api: missing env vars', { influxHost: !!influxHost, influxToken: !!influxToken });
    if (shouldMockOnError()) {
      return mockResponse(version, route, request.nextUrl.searchParams);
    }
    return fallbackResponse(version, route);
  }

  const fullRoute = routeParts.join('/');
  const url = route === 'health'
    ? `${influxHost}/health`
    : `${influxHost}/api/${version}/${fullRoute}`;

  const headers: Record<string, string> = {};
  request.headers.forEach((value, key) => {
    headers[key] = value;
  });

  if (version === 'v2') {
    headers['Authorization'] = `Token ${influxToken}`;
  } else {
    headers['Authorization'] = `Bearer ${influxToken}`;
  }

  // Remove host header to avoid conflicts
  delete headers['host'];

  const searchParams = request.nextUrl.searchParams.toString();
  const fullUrl = searchParams ? `${url}?${searchParams}` : url;

  try {
    const resp = await fetch(fullUrl, {
      method: request.method,
      headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? await request.arrayBuffer() : undefined,
      signal: AbortSignal.timeout(30000),
      cache: 'no-store',
    });

    const body = await resp.arrayBuffer();
    const responseHeaders = new Headers();
    resp.headers.forEach((value, key) => {
      if (!EXCLUDED_HEADERS.has(key.toLowerCase())) {
        responseHeaders.set(key, value);
      }
    });

    return new NextResponse(body, {
      status: resp.status,
      headers: responseHeaders,
    });
  } catch (e) {
    console.warn(`influx/api: request to ${fullUrl} failed`, e);
    if (shouldMockOnError()) {
      return mockResponse(version, route, request.nextUrl.searchParams);
    }
    return new NextResponse('Upstream Influx/VictoriaMetrics request failed', { status: 502 });
  }
}

export const GET = handleRequest;
export const POST = handleRequest;
