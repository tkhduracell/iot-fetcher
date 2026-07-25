import { NextResponse } from 'next/server';
import { haConfig, haService } from '../../../lib/haClient';
import { VACUUM_ENTITY } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

export async function POST() {
  const cfg = haConfig();
  if (!cfg) return NextResponse.json({ error: 'Home Assistant not configured' }, { status: 503 });

  try {
    await haService(cfg, 'vacuum', 'return_to_base', { entity_id: VACUUM_ENTITY });
    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error('Failed to send Roborock back to dock:', e);
    return NextResponse.json({ error: 'Home Assistant unavailable' }, { status: 503 });
  }
}
