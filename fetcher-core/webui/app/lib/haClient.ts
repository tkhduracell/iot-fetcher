// Server-side only. Never import this from a 'use client' component — it reads
// the long-lived Home Assistant token from the environment.

export type HaConfig = {
  url: string;
  token: string;
};

export function haConfig(): HaConfig | null {
  const url = process.env.HOMEASSISTANT_URL;
  const token = process.env.HOMEASSISTANT_TOKEN;
  if (!url || !token) return null;
  return { url: url.replace(/\/$/, ''), token };
}

async function haFetch(cfg: HaConfig, path: string, body: unknown): Promise<Response> {
  const resp = await fetch(`${cfg.url}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${cfg.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
    cache: 'no-store',
  });
  if (!resp.ok) {
    throw new Error(`HA ${path} returned ${resp.status}: ${await resp.text()}`);
  }
  return resp;
}

/** Renders a Jinja template through HA and returns the raw rendered string. */
export async function haTemplate(cfg: HaConfig, template: string): Promise<string> {
  const resp = await haFetch(cfg, '/api/template', { template });
  return resp.text();
}

/** Calls an HA service, e.g. haService(cfg, 'automation', 'trigger', {...}). */
export async function haService(
  cfg: HaConfig,
  domain: string,
  service: string,
  data: Record<string, unknown>,
): Promise<void> {
  await haFetch(cfg, `/api/services/${domain}/${service}`, data);
}
