// Server-side only. Never import this from a 'use client' component — it reads
// the Gemini API key from the environment.

export type GeminiConfig = {
  apiKey: string;
  model: string;
};

/**
 * Flash-lite for script writing (not the TTS model, which is configured
 * separately inside sonos-http-api's settings.json). Lite does no thinking,
 * which is what we want here: a radio bulletin gains nothing from reasoning,
 * and the thinking models spend 1000-1700 tokens on it before writing a word.
 */
export const DEFAULT_NEWS_MODEL = 'gemini-3.5-flash-lite';

export function geminiConfig(): GeminiConfig | null {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) return null;
  return { apiKey, model: process.env.NEWS_MODEL || DEFAULT_NEWS_MODEL };
}

export type GenerateArgs = {
  cfg: GeminiConfig;
  systemInstruction: string;
  prompt: string;
  temperature?: number;
  maxOutputTokens?: number;
};

/**
 * One generateContent call over plain fetch — the webui has no Gemini SDK and
 * a single REST call does not justify adding one.
 */
export async function generateContent({
  cfg,
  systemInstruction,
  prompt,
  temperature = 1.0,
  // Generous: thinking models charge 1000-1700 tokens to reasoning before any
  // output, and a low ceiling silently truncates the script mid-sentence
  // (finishReason MAX_TOKENS). The word count is steered by the prompt.
  maxOutputTokens = 8000,
}: GenerateArgs): Promise<string> {
  const resp = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${cfg.model}:generateContent`,
    {
      method: 'POST',
      headers: { 'x-goog-api-key': cfg.apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: systemInstruction }] },
        contents: [{ role: 'user', parts: [{ text: prompt }] }],
        generationConfig: { temperature, maxOutputTokens },
      }),
      signal: AbortSignal.timeout(60_000),
      cache: 'no-store',
    },
  );

  if (!resp.ok) {
    throw new Error(`Gemini ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
  }

  const body = await resp.json();
  const parts = body?.candidates?.[0]?.content?.parts;
  const text = Array.isArray(parts)
    ? parts.map((p: { text?: string }) => p?.text ?? '').join('')
    : '';
  if (!text.trim()) {
    const reason = body?.candidates?.[0]?.finishReason ?? 'unknown';
    throw new Error(`Gemini returned no text (finishReason: ${reason})`);
  }
  return text;
}
