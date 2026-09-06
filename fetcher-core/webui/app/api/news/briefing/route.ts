import { NextResponse } from 'next/server';
import { geminiConfig } from '../../../lib/news/gemini';
import { fetchAllSources, RELEVANCE_FILTER } from '../../../lib/news/sources';
import { selectStories } from '../../../lib/news/select';
import { generateScript } from '../../../lib/news/script';

export const dynamic = 'force-dynamic';

/** Defaults match the wall dashboard; overridable without a rebuild. */
const DEFAULT_ROOM = 'Kontor';
const DEFAULT_VOLUME = '20';
const MIN_STORIES = 2;
/** The script is one short spoken sweep, so feeding more just gets trimmed. */
const MAX_STORIES = 5;

export async function POST() {
  const cfg = geminiConfig();
  if (!cfg) {
    return NextResponse.json({ error: 'Gemini not configured' }, { status: 503 });
  }

  const room = process.env.NEWS_ROOM || DEFAULT_ROOM;
  const volume = process.env.NEWS_VOLUME || DEFAULT_VOLUME;
  const now = new Date();

  const results = await fetchAllSources();
  const sources = results.map((r) => ({ source: r.source, count: r.items.length, error: r.error }));
  const stories = selectStories(results, RELEVANCE_FILTER, { now, maxStories: MAX_STORIES });

  if (stories.length < MIN_STORIES) {
    // Never let the model invent a briefing out of nothing.
    console.warn(`[news] only ${stories.length} stories in window, skipping generation`);
    return NextResponse.json({ transcript: null, reason: 'no-news', stories: [], sources, room, volume });
  }

  try {
    console.info(`[news] generating briefing from ${stories.length} stories with ${cfg.model}`);
    const transcript = await generateScript(cfg, stories, now);
    return NextResponse.json({
      transcript,
      stories: stories.map((s) => ({
        title: s.title,
        url: s.url,
        sources: s.sources,
        publishedAt: s.publishedAt?.toISOString() ?? null,
      })),
      sources,
      model: cfg.model,
      room,
      volume,
    });
  } catch (e) {
    const detail = e instanceof Error ? e.message : 'unknown error';
    console.error(`[news] script generation failed: ${detail}`);
    return NextResponse.json({ error: `Kunde inte skriva manus: ${detail}` }, { status: 502 });
  }
}
