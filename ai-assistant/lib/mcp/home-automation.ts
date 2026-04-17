import { FunctionTool, MCPToolset } from "@google/adk";
import { z } from "zod";

const SONOS_HOST = process.env.SONOS_HOST ?? "host.docker.internal";
const INFLUXDB_URL = process.env.INFLUXDB_V3_URL ?? "";
const INFLUXDB_TOKEN = process.env.INFLUXDB_V3_ACCESS_TOKEN ?? "";

async function fetchJson(url: string, options?: RequestInit) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      return { error: `HTTP ${res.status}: ${text}` };
    }
    return await res.json();
  } catch (err) {
    return { error: String(err) };
  } finally {
    clearTimeout(timeout);
  }
}

// --- Sonos MCP toolset ---

export const sonosToolset = new MCPToolset({
  type: "StreamableHTTPConnectionParams",
  url: `http://${SONOS_HOST}:5005/mcp`,
});

// --- Metrics tools ---

const listMetrics = new FunctionTool({
  name: "list_metrics",
  description: "List all available metric names from VictoriaMetrics",
  parameters: z.object({}),
  execute: async () => {
    if (!INFLUXDB_URL)
      return { error: "INFLUXDB_V3_URL not configured" };
    const data = await fetchJson(
      `${INFLUXDB_URL}/api/v1/label/__name__/values`,
      { headers: { Authorization: `Bearer ${INFLUXDB_TOKEN}` } }
    );
    if (data.error) return { error: data.error };
    return { metrics: data.data ?? data };
  },
});

const queryMetrics = new FunctionTool({
  name: "query_metrics",
  description:
    "Query VictoriaMetrics with a PromQL expression. Returns current values for the given metric/query.",
  parameters: z.object({
    query: z
      .string()
      .describe(
        "PromQL query expression, e.g. 'sigenergy_battery_soc' or 'rate(tapo_device_power[5m])'"
      ),
  }),
  execute: async ({ query }) => {
    if (!INFLUXDB_URL)
      return { error: "INFLUXDB_V3_URL not configured" };
    const url = `${INFLUXDB_URL}/api/v1/query?query=${encodeURIComponent(query)}`;
    const data = await fetchJson(url, {
      headers: { Authorization: `Bearer ${INFLUXDB_TOKEN}` },
    });
    if (data.error) return { error: data.error };
    return data;
  },
});

const listLabels = new FunctionTool({
  name: "list_metric_labels",
  description:
    "List all label names or values for a specific label in VictoriaMetrics. Useful for discovering metric dimensions.",
  parameters: z.object({
    label: z
      .string()
      .optional()
      .describe(
        "If provided, list values for this label. If omitted, list all label names."
      ),
  }),
  execute: async ({ label }) => {
    if (!INFLUXDB_URL)
      return { error: "INFLUXDB_V3_URL not configured" };
    const path = label
      ? `/api/v1/label/${encodeURIComponent(label)}/values`
      : "/api/v1/labels";
    const data = await fetchJson(`${INFLUXDB_URL}${path}`, {
      headers: { Authorization: `Bearer ${INFLUXDB_TOKEN}` },
    });
    if (data.error) return { error: data.error };
    return { labels: data.data ?? data };
  },
});

// --- Export metrics tools ---

export const metricsTools = [
  listMetrics,
  queryMetrics,
  listLabels,
];
