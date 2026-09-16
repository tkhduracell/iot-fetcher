/**
 * On-screen formatting only — the spoken transcript is never modified, since
 * anything inserted here would be URL-encoded into the /say call and read out.
 */

/** One rendered paragraph: its leading cue (if any) and the text that follows. */
export type CueParagraph = { cue: string | null; text: string };

/**
 * The anchor prompt opens every paragraph with a bracketed cue, so a cue marks
 * where a new item starts. Splitting there gives the panel one paragraph per
 * item instead of a single wall of text.
 */
export function cueParagraphs(transcript: string): CueParagraph[] {
  const out: CueParagraph[] = [];
  // Split on cues but keep them: odd indices are the cues themselves.
  const parts = transcript.split(/(\[[^\]]+\])/g);
  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    if (/^\[[^\]]+\]$/.test(trimmed)) {
      out.push({ cue: trimmed, text: '' });
      continue;
    }
    // Text following a cue belongs to that cue's paragraph; text with no cue
    // before it (the model skipped one) becomes a paragraph of its own.
    const prev = out[out.length - 1];
    if (prev && prev.cue && !prev.text) prev.text = trimmed;
    else out.push({ cue: null, text: trimmed });
  }
  return out;
}
