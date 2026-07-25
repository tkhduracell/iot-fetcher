import { NextResponse } from 'next/server';
import { haConfig, haTemplate } from '../../../lib/haClient';
import { STATUS_TEMPLATE, parseStatus } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

export async function GET() {
  const cfg = haConfig();
  if (!cfg) return NextResponse.json({ error: 'Home Assistant not configured' }, { status: 503 });

  try {
    const raw = await haTemplate(cfg, STATUS_TEMPLATE);
    return NextResponse.json(parseStatus(raw));
  } catch (e) {
    console.error('Failed to load Roborock status from Home Assistant:', e);
    return NextResponse.json({ error: 'Home Assistant unavailable' }, { status: 503 });
  }
}
