import { NextRequest, NextResponse } from 'next/server';
import { haConfig, haTemplate, haService } from '../../../lib/haClient';
import { TARGETS_TEMPLATE, parseTargets, isAllowedTarget } from '../../../lib/roborock';

export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  const cfg = haConfig();
  if (!cfg) return NextResponse.json({ error: 'Home Assistant not configured' }, { status: 503 });

  let entityId: unknown;
  try {
    ({ entity_id: entityId } = await request.json());
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }
  if (typeof entityId !== 'string' || !entityId) {
    return NextResponse.json({ error: 'entity_id is required' }, { status: 400 });
  }

  try {
    // Re-resolve the labelled set server-side; never trust the client's entity_id.
    const targets = parseTargets(await haTemplate(cfg, TARGETS_TEMPLATE));
    if (!isAllowedTarget(targets, entityId)) {
      console.warn(`Rejected Roborock trigger for unlabelled entity: ${entityId}`);
      return NextResponse.json({ error: 'Not a Roborock cleaning automation' }, { status: 403 });
    }

    await haService(cfg, 'automation', 'trigger', { entity_id: entityId });
    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error(`Failed to trigger ${entityId}:`, e);
    return NextResponse.json({ error: 'Home Assistant unavailable' }, { status: 503 });
  }
}
