import { NextResponse } from 'next/server';
import { haConfig, haTemplate } from '../../../lib/haClient';
import { TARGETS_TEMPLATE, parseTargets, RoborockTargetsResponse } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

const UNCONFIGURED: RoborockTargetsResponse = { floors: [], rooms: [], configured: false };

export async function GET() {
  const cfg = haConfig();
  // Deliberately not configured: 200/empty so the button stays hidden. `configured`
  // tells the client this is intentional, so it won't retry forever.
  if (!cfg) return NextResponse.json(UNCONFIGURED);

  try {
    const raw = await haTemplate(cfg, TARGETS_TEMPLATE);
    // An empty set here is NOT the same as unconfigured — HA may still be starting
    // up, so the client keeps retrying until its labelled automations register.
    return NextResponse.json({ ...parseTargets(raw), configured: true });
  } catch (e) {
    // Configured but unreachable: a real failure, not "unconfigured" — don't
    // let the client mistake this for the deliberate empty-set case.
    console.error('Failed to load Roborock targets from Home Assistant:', e);
    return NextResponse.json({ error: 'Home Assistant unavailable' }, { status: 503 });
  }
}
