import { NextRequest, NextResponse } from 'next/server';

const EXCLUDED_HEADERS = new Set([
  'content-encoding', 'content-length', 'transfer-encoding', 'connection'
]);

async function handleRequest(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  // Next.js decodes each path segment, so rejoining raw would let a message
  // containing '#' or '?' truncate the URL (a '#...' message reached
  // sonos-http-api as an empty phrase). Re-encode every segment.
  const pathStr = path.map(encodeURIComponent).join('/');
  const sonosHost = process.env.SONOS_HOST;

  if (!sonosHost) {
    if (pathStr === 'zones') {
      return NextResponse.json([]);
    }
    return NextResponse.json({});
  }

  const isSay = path.length > 1 && path[1] === 'say';
  const base = /^https?:\/\//.test(sonosHost) ? sonosHost : `http://${sonosHost}`;
  const url = `${base}/${pathStr}`;

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
      // /say has to synthesize speech on first use of a phrase, which can take
      // well over 15s; cached phrases return immediately. A short timeout made
      // the UI report failure while the announcement actually played.
      signal: AbortSignal.timeout(isSay ? 120000 : 15000),
      cache: 'no-store',
    });

    const body = await resp.arrayBuffer();
    if (!resp.ok) {
      const text = new TextDecoder().decode(body);
      console.error(`Sonos API ${resp.status} for ${fullUrl}: ${text}`);
    }
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
