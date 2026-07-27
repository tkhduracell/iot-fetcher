import { NextResponse } from 'next/server';
import { haConfig, haTemplate } from '../../../lib/haClient';
import { TARGETS_TEMPLATE, parseTargets, RoborockTargets } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

const EMPTY: RoborockTargets = { floors: [], rooms: [] };

export async function GET() {
  const cfg = haConfig();
  // Deliberately not configured: keep returning 200/empty so the button stays hidden.
  if (!cfg) return NextResponse.json(EMPTY);

  try {
    const raw = await haTemplate(cfg, TARGETS_TEMPLATE);
    return NextResponse.json(parseTargets(raw));
  } catch (e) {
    // Configured but unreachable: a real failure, not "unconfigured" — don't
    // let the client mistake this for the deliberate empty-set case.
    console.error('Failed to load Roborock targets from Home Assistant:', e);
    return NextResponse.json({ error: 'Home Assistant unavailable' }, { status: 503 });
  }
}
