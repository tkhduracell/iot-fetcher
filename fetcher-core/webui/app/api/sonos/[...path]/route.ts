import { NextRequest, NextResponse } from 'next/server';

const EXCLUDED_HEADERS = new Set([
  'content-encoding', 'content-length', 'transfer-encoding', 'connection'
]);

async function handleRequest(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  const pathStr = path.join('/');
  const sonosHost = process.env.SONOS_HOST;

  if (!sonosHost) {
    if (pathStr === 'zones') {
      return NextResponse.json([]);
    }
    return NextResponse.json({});
  }

  const url = `http://${sonosHost}/${pathStr}`;

  try {
    const headers: Record<string, string> = {};
    request.headers.forEach((value, key) => {
      headers[key] = value;
    });
    delete headers['host'];

    const searchParams = request.nextUrl.searchParams.toString();
    const fullUrl = searchParams ? `${url}?${searchParams}` : url;

    const resp = await fetch(fullUrl, {
      method: request.method,
      headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? await request.arrayBuffer() : undefined,
      signal: AbortSignal.timeout(5000),
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
    console.error(`Error proxying Sonos request to ${url}:`, e);
    return new NextResponse('Sonos API unavailable', { status: 502 });
  }
}

export const GET = handleRequest;
export const POST = handleRequest;
