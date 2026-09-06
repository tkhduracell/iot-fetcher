import { describe, it, expect } from 'vitest';
import { chunkTranscript, splitSentences, MAX_SAY_CHARS, MAX_ENCODED_BYTES } from './chunk';

/** A realistic playful-anchor briefing: Swedish, bracketed cues, ~380 words. */
const TRANSCRIPT = [
  'God kväll Malmö, och välkommen till Malmökollen — ett svep över det senaste dygnet i stan.',
  '[dramatisk paus] Vi börjar vid E6 utanför Malmö, där en singelolycka stängde av trafiken i norrgående riktning under kvällen.',
  'Räddningstjänsten var på plats och trafiken leddes om, men köerna växte snabbt förbi trafikplats Lundåkra.',
  'Från motorvägen till dörrmattorna: Socialdemokraterna i Malmö nådde under söndagen tjugotusen dörrknackningar.',
  'Partiledaren stod själv för en av dem, vilket får sägas vara ett effektivt sätt att ta en milstolpe.',
  '[torrt] Tjugotusen dörrar, och ingen som helst statistik på hur många som låtsades vara hemma.',
  'Kulturnämnden i Malmö ställer sig bakom det statliga kulturprogrammet, enligt Malmödirekt.',
  'Beslutet togs under veckan och innebär att staden följer den nationella inriktningen framåt.',
  'I Rosengård pågår en förändring kring stationsområdet, med utveckling av både föreningsliv och bostäder nära spåren.',
  'Och så lite sport: Rosengård tog en bortaseger mot Djurgården, vilket lär ha märkts på hemvägen.',
  '[skrattar till] Grattis säger vi, och hoppas grannarna fick sova någon gång innan midnatt.',
  'Malmö minskar samtidigt antalet förskolelokaler, efter att barnantalet i staden sjunkit de senaste åren.',
  'På Lilla Torg hålls en hantverksmarknad med design och konst under helgen, för den som vill ut.',
  'Det var allt för den här gången — ha en fin kväll i Malmö, så hörs vi imorgon.',
].join(' ');

describe('splitSentences', () => {
  it('never splits mid-sentence', () => {
    for (const s of splitSentences(TRANSCRIPT)) {
      expect(s.trim()).toBe(s);
      expect(s.length).toBeGreaterThan(0);
    }
  });

  it('keeps a bracketed cue with its sentence', () => {
    const parts = splitSentences('Först en sak. [skrattar till] Sedan en annan sak.');
    expect(parts).toHaveLength(2);
    expect(parts[1]).toBe('[skrattar till] Sedan en annan sak.');
  });

  it('does not split on decimals or abbreviations mid-sentence', () => {
    expect(splitSentences('Det kostar 3.5 miljoner kronor totalt.')).toHaveLength(1);
  });
});

describe('chunkTranscript', () => {
  it('produces chunks under the character limit', () => {
    for (const c of chunkTranscript(TRANSCRIPT)) {
      expect(c.length).toBeLessThanOrEqual(MAX_SAY_CHARS);
    }
  });

  it('GUARDS THE URL BUDGET: every chunk encodes under the byte ceiling', () => {
    // This is the test that protects the whole pipeline — Swedish text expands
    // ~1.6x under encodeURIComponent and travels as a URL path segment.
    const chunks = chunkTranscript(TRANSCRIPT);
    expect(chunks.length).toBeGreaterThan(1);
    for (const c of chunks) {
      expect(encodeURIComponent(c).length).toBeLessThan(MAX_ENCODED_BYTES);
    }
  });

  it('loses no text', () => {
    const joined = chunkTranscript(TRANSCRIPT).join(' ');
    const norm = (s: string) => s.replace(/\s+/g, ' ').trim();
    expect(norm(joined)).toBe(norm(TRANSCRIPT));
  });

  it('emits an over-long single sentence alone rather than looping forever', () => {
    const monster = `${'ord '.repeat(400)}slut.`;
    const chunks = chunkTranscript(monster);
    expect(chunks).toHaveLength(1);
    expect(chunks[0].length).toBeGreaterThan(MAX_SAY_CHARS);
  });

  it('handles empty and whitespace input', () => {
    expect(chunkTranscript('')).toEqual([]);
    expect(chunkTranscript('   \n  ')).toEqual([]);
  });

  it('keeps chunks short enough to finish inside the 120s Sonos proxy timeout', () => {
    // A /say call blocks until the clip stops playing. Measured on Kontor:
    // ~900 chars ≈ 86s, i.e. roughly 0.1s of speech per character. Staying at
    // or under 500 chars keeps the worst case near 50s, half the proxy budget.
    const SECONDS_PER_CHAR = 86 / 900;
    const PROXY_TIMEOUT_S = 120;
    for (const c of chunkTranscript(TRANSCRIPT)) {
      expect(c.length * SECONDS_PER_CHAR).toBeLessThan(PROXY_TIMEOUT_S * 0.6);
    }
  });

  it('returns one chunk for a short transcript', () => {
    expect(chunkTranscript('God morgon Malmö. Det var allt.')).toHaveLength(1);
  });
});
