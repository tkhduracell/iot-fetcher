import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  validateTranscript, buildUserPrompt, relativeTime, generateScript,
  ANCHOR_SYSTEM_PROMPT, ALLOWED_CUES, MAX_TRANSCRIPT_CHARS, MAX_ENCODED_TRANSCRIPT,
} from './script';
import { NewsItem } from './types';

const NOW = new Date('2026-09-06T21:40:00+02:00');
const body = (n = 12) => 'Det här är en helt vanlig mening om Malmö. '.repeat(n);

const story = (over: Partial<NewsItem> = {}): NewsItem => ({
  title: 'Råttlarm på hamburgerställe',
  summary: 'Restaurangen stängde efter inspektion.',
  url: 'https://example.se/a',
  publishedAt: new Date(NOW.getTime() - 3 * 3600_000),
  sources: ['Sydsvenskan'],
  categories: [],
  ...over,
});

afterEach(() => vi.unstubAllGlobals());

describe('relativeTime', () => {
  it('does not call a story inside the 24h window yesterday', () => {
    // Math.round would push 23h40m to 24 and mislabel it as 'igår'.
    expect(relativeTime(new Date(NOW.getTime() - (23 * 60 + 40) * 60_000), NOW))
      .toBe('publicerad för 23 timmar sedan');
  });

  it('treats a future timestamp as unknown, not as breaking news', () => {
    expect(relativeTime(new Date(NOW.getTime() + 2 * 3600_000), NOW)).toBe('publiceringstid okänd');
  });

  it('uses singular Swedish for one minute', () => {
    expect(relativeTime(new Date(NOW.getTime() - 60_000), NOW)).toBe('publicerad för en minut sedan');
  });

  it('renders hours, minutes and unknown timing', () => {
    expect(relativeTime(new Date(NOW.getTime() - 3 * 3600_000), NOW)).toBe('publicerad för 3 timmar sedan');
    expect(relativeTime(new Date(NOW.getTime() - 20 * 60_000), NOW)).toBe('publicerad för 20 minuter sedan');
    expect(relativeTime(new Date(NOW.getTime() - 3600_000), NOW)).toBe('publicerad för en timme sedan');
    expect(relativeTime(null, NOW)).toBe('publiceringstid okänd');
  });
});

describe('buildUserPrompt', () => {
  it('includes titles, summaries, sources and a Swedish timestamp', () => {
    const p = buildUserPrompt([story()], NOW);
    expect(p).toContain('Råttlarm på hamburgerställe');
    expect(p).toContain('Restaurangen stängde efter inspektion.');
    expect(p).toContain('Sydsvenskan');
    expect(p).toContain('september 2026');
  });

  it('marks undated stories so the model cannot claim they are from today', () => {
    expect(buildUserPrompt([story({ publishedAt: null })], NOW)).toContain('publiceringstid okänd');
  });

  it('says "saknas" when a summary is missing', () => {
    expect(buildUserPrompt([story({ summary: '' })], NOW)).toContain('Sammanfattning: saknas');
  });

  it('credits both outlets after a dedup merge', () => {
    expect(buildUserPrompt([story({ sources: ['SVT Skåne', 'Sydsvenskan'] })], NOW))
      .toContain('SVT Skåne och Sydsvenskan');
  });
});

describe('the system prompt', () => {
  it('states the tone rule and lists only allowed cues', () => {
    expect(ANCHOR_SYSTEM_PROMPT).toContain('INGA skämt');
    for (const cue of ALLOWED_CUES) expect(ANCHOR_SYSTEM_PROMPT).toContain(`[${cue}]`);
  });

  it('asks for a fast pace and forbids trailing future events', () => {
    expect(ANCHOR_SYSTEM_PROMPT).toContain('TEMPO');
    expect(ANCHOR_SYSTEM_PROMPT).toContain('högt tempo');
    expect(ANCHOR_SYSTEM_PROMPT).toContain('BARA DET SOM HÄNT');
  });

  it('asks for a distinct voice per story and forbids repeating a cue', () => {
    expect(ANCHOR_SYSTEM_PROMPT).toContain('EGEN tydliga röstkaraktär');
    expect(ANCHOR_SYSTEM_PROMPT).toContain('aldrig samma markör två gånger');
  });

  it('offers a wide enough cue palette to keep every story distinct', () => {
    // Six stories each need their own cue, minus the ones a sombre item bans.
    expect(ALLOWED_CUES.length).toBeGreaterThanOrEqual(20);
    expect(new Set(ALLOWED_CUES).size).toBe(ALLOWED_CUES.length);
  });
});

describe('validateTranscript', () => {
  it('strips markdown fences and headings', () => {
    const out = validateTranscript('```\n# Rubrik\n**' + body() + '**\n```');
    expect(out).not.toContain('```');
    expect(out).not.toContain('**');
    expect(out).not.toContain('#');
  });

  it('keeps allowed cues but removes invented ones', () => {
    const out = validateTranscript(`[skrattar till] ${body()} [SPELA JINGEL] [random]`);
    expect(out).toContain('[skrattar till]');
    expect(out).not.toContain('[SPELA JINGEL]');
    expect(out).not.toContain('[random]');
  });

  it('collapses newlines into a single URL-safe line', () => {
    expect(validateTranscript(`Först.\n\n\nSedan. ${body()}`)).not.toContain('\n');
  });

  it('throws on output too short to be a real briefing', () => {
    expect(() => validateTranscript('Hej.')).toThrow(/too short/i);
    expect(() => validateTranscript('')).toThrow();
  });

  it('keeps the whole briefing inside one /say request', () => {
    // Spoken duration is ~0.1s per character and the Sonos proxy aborts at
    // 120s, so a single-call briefing has to stay near 1000 chars.
    const out = validateTranscript(body(200));
    expect(out.length * 0.1).toBeLessThan(120);
  });

  it('never ends mid-word when the tail is one long sentence', () => {
    const oneSentence = `${'A'.repeat(500)} och sedan ${'B'.repeat(600)}.`;
    const out = validateTranscript(oneSentence);
    expect(out).toMatch(/[.!?…]$/);
    expect(out.endsWith('B…')).toBe(false);
  });

  it('truncates a runaway script on a sentence boundary', () => {
    const out = validateTranscript(body(200));
    expect(out.length).toBeLessThanOrEqual(MAX_TRANSCRIPT_CHARS);
    expect(out).toMatch(/[.!?]$/);
  });

  it('holds the 14KB encoded ceiling even for a huge generation', () => {
    const out = validateTranscript('Räksmörgås på Möllevången är gott. '.repeat(500));
    expect(encodeURIComponent(out).length).toBeLessThanOrEqual(MAX_ENCODED_TRANSCRIPT);
  });
});

describe('generateScript', () => {
  const cfg = { apiKey: 'k', model: 'gemini-3.5-flash-lite' };

  it('sends the system prompt and story titles, and returns validated text', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ candidates: [{ content: { parts: [{ text: body() }] } }] }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const out = await generateScript(cfg, [story()], NOW);
    expect(out.length).toBeGreaterThan(100);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('gemini-3.5-flash-lite:generateContent');
    expect(init.headers['x-goog-api-key']).toBe('k');
    const sent = JSON.parse(init.body);
    expect(sent.systemInstruction.parts[0].text).toBe(ANCHOR_SYSTEM_PROMPT);
    expect(sent.contents[0].parts[0].text).toContain('Råttlarm på hamburgerställe');
    expect(sent.generationConfig.maxOutputTokens).toBe(8000);
  });

  it('throws with the upstream detail on an API error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 429, text: async () => 'quota exceeded',
    }));
    await expect(generateScript(cfg, [story()], NOW)).rejects.toThrow(/429[\s\S]*quota/);
  });

  it('throws when the model returns no text (e.g. safety block)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ candidates: [{ finishReason: 'SAFETY', content: {} }] }),
    }));
    await expect(generateScript(cfg, [story()], NOW)).rejects.toThrow(/no text[\s\S]*SAFETY/);
  });
});
