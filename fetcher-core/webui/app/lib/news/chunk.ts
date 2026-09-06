/**
 * The transcript travels to Sonos as a URL *path segment*, through two Node
 * servers that each cap the request line at 16KB by default. Swedish encodes
 * badly — every space becomes %20 and each å/ä/ö becomes six bytes — so the
 * text is split into chunks that stay far inside that budget.
 */
/**
 * Also bounds wall-clock per request: a /say call returns only when the clip
 * has finished playing, and the shared Sonos proxy aborts at 120s. Measured on
 * Kontor, ~900 chars took 86s — too close to that ceiling — so chunks are kept
 * near half of it, which also makes the on-screen progress move more often.
 */
export const MAX_SAY_CHARS = 500;

/** Hard ceiling on one encoded segment; well under Node's 16KB request line. */
export const MAX_ENCODED_BYTES = 3000;

/**
 * Splits on sentence boundaries only. A chunk never ends mid-sentence, and a
 * bracketed cue is never separated from the sentence it modifies, because the
 * split points are the sentence terminators themselves.
 */
export function splitSentences(text: string): string[] {
  const out: string[] = [];
  // Terminator followed by whitespace and something that starts a new sentence
  // (capital, digit, quote or an opening bracketed cue).
  const re = /([.!?…]+)(\s+)(?=[A-ZÅÄÖ0-9"“«[])/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const end = m.index! + m[1].length;
    const piece = text.slice(last, end).trim();
    if (piece) out.push(piece);
    last = end + m[2].length;
  }
  const tail = text.slice(last).trim();
  if (tail) out.push(tail);
  return out;
}

/**
 * Greedily packs whole sentences up to `max`. A single sentence longer than
 * `max` is emitted alone rather than dropped or split — it still has to be
 * spoken, and one over-long chunk is survivable where an infinite loop is not.
 */
export function chunkTranscript(text: string, max: number = MAX_SAY_CHARS): string[] {
  const sentences = splitSentences(text.replace(/\s+/g, ' ').trim());
  const chunks: string[] = [];
  let current = '';

  for (const sentence of sentences) {
    if (!current) {
      current = sentence;
    } else if (current.length + 1 + sentence.length <= max) {
      current += ` ${sentence}`;
    } else {
      chunks.push(current);
      current = sentence;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}
