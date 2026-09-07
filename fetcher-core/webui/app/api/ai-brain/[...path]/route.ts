import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** Only the read-only introspection surface is reachable through this proxy.
 *  Anchored so a segment like `..` or an unrelated `admin` path never reaches
 *  ai-brain — the API is unauthenticated, so the allowlist is the whole guard. */
const ALLOWED_PATH = /^(healthz|api\/[A-Za-z0-9/_.-]*)$/;

const DEFAULT_AI_BRAIN_URL = 'http://ai-brain:8091';

const NO_STORE = { 'Cache-Control': 'no-store' };

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;

  // The decoded form is what the allowlist must judge: an encoded '%2e%2e'
  // would otherwise slip past here and be decoded again upstream. The dot
  // check is separate because ALLOWED_PATH permits '.' and '/' inside the
  // `api/` branch (fact and agent names contain dots), so `api/../admin`
  // matches the pattern — and fetch() normalizes the '..' away, landing the
  // request outside /api/ entirely.
  const joined = path.join('/');
  const traverses = path.some((seg) => seg === '.' || seg === '..');
  if (traverses || !ALLOWED_PATH.test(joined)) {
    return NextResponse.json({ error: 'not found' }, { status: 404, headers: NO_STORE });
  }

  // Next.js decodes each segment; re-encode so a segment containing '?' or '#'
  // cannot truncate or rewrite the upstream URL.
  const pathStr = path.map(encodeURIComponent).join('/');

  // Read at request time, not module load: the container gets its env from
  // compose, and the tests stub it per case.
  const base = process.env.AI_BRAIN_URL ?? DEFAULT_AI_BRAIN_URL;
  if (!base) {
    return NextResponse.json(
      { error: 'ai-brain not configured' },
      { status: 503, headers: NO_STORE },
    );
  }

  const search = request.nextUrl.searchParams.toString();
  const url = `${base.replace(/\/+$/, '')}/${pathStr}${search ? `?${search}` : ''}`;

  try {
    const resp = await fetch(url, {
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
      headers: { Accept: 'application/json' },
    });

    // Status and body pass through untouched so the client sees ai-brain's own
    // 400/404 payloads instead of a proxy-invented error.
    const body = await resp.text();
    return new NextResponse(body, {
      status: resp.status,
      headers: {
        'Content-Type': resp.headers.get('content-type') ?? 'application/json',
        ...NO_STORE,
      },
    });
  } catch (e) {
    console.error(`Error proxying ai-brain request to ${url}:`, e);
    return NextResponse.json(
      { error: 'ai-brain unavailable' },
      { status: 502, headers: NO_STORE },
    );
  }
}
