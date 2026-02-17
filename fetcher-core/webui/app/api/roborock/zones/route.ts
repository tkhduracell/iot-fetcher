import { NextResponse } from 'next/server';

export async function GET() {
  const sidecarUrl = process.env.ROBOROCK_SIDECAR_URL || 'http://localhost:8081';

  try {
    const resp = await fetch(`${sidecarUrl}/roborock/zones`, {
      signal: AbortSignal.timeout(120000),
      cache: 'no-store',
    });

    const body = await resp.text();

    return new NextResponse(body, {
      status: resp.status,
      headers: { 'content-type': resp.headers.get('content-type') || 'application/json' },
    });
  } catch (e) {
    console.error('Error proxying to roborock sidecar:', e);
    return NextResponse.json({ error: 'Roborock sidecar unavailable' }, { status: 502 });
  }
}
