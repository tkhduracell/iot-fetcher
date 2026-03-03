import { tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";

const SONOS_HOST = process.env.SONOS_HOST ?? "sonos-http-api";
const ROBOROCK_SIDECAR_URL =
  process.env.ROBOROCK_SIDECAR_URL ?? "http://localhost:8081";
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

function textResult(text: string) {
  return { content: [{ type: "text" as const, text }] };
}

// --- Sonos tools ---

const sonosGetZones = tool(
  "sonos_get_zones",
  "List all Sonos speaker zones/rooms with their current playback state, volume, and what is playing",
  {},
  async () => {
    const data = await fetchJson(`http://${SONOS_HOST}:5005/zones`);
    if (data.error) return textResult(`Error: ${data.error}`);

    const zones = Array.isArray(data)
      ? data.map(
          (z: {
            coordinator: {
              roomName: string;
              state: {
                volume: number;
                playbackState: string;
                currentTrack: {
                  artist: string;
                  title: string;
                  album: string;
                };
              };
            };
            members: unknown[];
          }) => ({
            room: z.coordinator.roomName,
            volume: z.coordinator.state.volume,
            playback: z.coordinator.state.playbackState,
            artist: z.coordinator.state.currentTrack?.artist,
            title: z.coordinator.state.currentTrack?.title,
            album: z.coordinator.state.currentTrack?.album,
            members: z.members?.length ?? 1,
          })
        )
      : data;

    return textResult(JSON.stringify(zones, null, 2));
  }
);

const sonosPlay = tool(
  "sonos_play",
  "Resume playback on a Sonos zone/room",
  {
    room: z
      .string()
      .describe("Room name, e.g. 'Living Room', 'Kitchen'"),
  },
  async ({ room }) => {
    const encoded = encodeURIComponent(room);
    const data = await fetchJson(
      `http://${SONOS_HOST}:5005/${encoded}/play`
    );
    return textResult(JSON.stringify(data, null, 2));
  }
);

const sonosPause = tool(
  "sonos_pause",
  "Pause playback on a Sonos zone/room",
  {
    room: z.string().describe("Room name"),
  },
  async ({ room }) => {
    const encoded = encodeURIComponent(room);
    const data = await fetchJson(
      `http://${SONOS_HOST}:5005/${encoded}/pause`
    );
    return textResult(JSON.stringify(data, null, 2));
  }
);

const sonosVolume = tool(
  "sonos_volume",
  "Set the volume of a Sonos zone/room (0-100)",
  {
    room: z.string().describe("Room name"),
    volume: z.number().min(0).max(100).describe("Volume level 0-100"),
  },
  async ({ room, volume }) => {
    const encoded = encodeURIComponent(room);
    const data = await fetchJson(
      `http://${SONOS_HOST}:5005/${encoded}/volume/${volume}`
    );
    return textResult(JSON.stringify(data, null, 2));
  }
);

const sonosFavourite = tool(
  "sonos_favourite",
  "Play a Sonos favourite/playlist on a zone/room",
  {
    room: z.string().describe("Room name"),
    favourite: z.string().describe("Name of the favourite or playlist"),
  },
  async ({ room, favourite }) => {
    const encodedRoom = encodeURIComponent(room);
    const encodedFav = encodeURIComponent(favourite);
    const data = await fetchJson(
      `http://${SONOS_HOST}:5005/${encodedRoom}/favourite/${encodedFav}`
    );
    return textResult(JSON.stringify(data, null, 2));
  }
);

// --- Roborock tools ---

const roborockListZones = tool(
  "roborock_list_zones",
  "List all available vacuum cleaning zones/rooms across all maps",
  {},
  async () => {
    const data = await fetchJson(`${ROBOROCK_SIDECAR_URL}/roborock/zones`);
    if (data.error) return textResult(`Error: ${data.error}`);
    return textResult(JSON.stringify(data, null, 2));
  }
);

const roborockCleanZone = tool(
  "roborock_clean_zone",
  "Start vacuum cleaning of a specific zone/room. Use 'all' as zone_id for full-house cleaning.",
  {
    device_id: z.string().describe("Device ID from zone listing"),
    map_id: z
      .string()
      .describe("Map flag/ID from zone listing (e.g. map_flag value)"),
    zone_id: z
      .string()
      .describe(
        "Zone segment ID from zone listing, or 'all' for full-house clean"
      ),
  },
  async ({ device_id, map_id, zone_id }) => {
    const data = await fetchJson(
      `${ROBOROCK_SIDECAR_URL}/roborock/${encodeURIComponent(device_id)}/${encodeURIComponent(map_id)}/${encodeURIComponent(zone_id)}/clean`,
      { method: "POST" }
    );
    return textResult(JSON.stringify(data, null, 2));
  }
);

// --- Metrics tools ---

const listMetrics = tool(
  "list_metrics",
  "List all available metric names from VictoriaMetrics",
  {},
  async () => {
    if (!INFLUXDB_URL) return textResult("Error: INFLUXDB_V3_URL not configured");
    const data = await fetchJson(
      `https://${INFLUXDB_URL}/api/v1/label/__name__/values`,
      { headers: { Authorization: `Bearer ${INFLUXDB_TOKEN}` } }
    );
    if (data.error) return textResult(`Error: ${data.error}`);
    return textResult(JSON.stringify(data.data ?? data, null, 2));
  }
);

const queryMetrics = tool(
  "query_metrics",
  "Query VictoriaMetrics with a PromQL expression. Returns current values for the given metric/query.",
  {
    query: z
      .string()
      .describe(
        "PromQL query expression, e.g. 'sigenergy_battery_soc' or 'rate(tapo_device_power[5m])'"
      ),
  },
  async ({ query }) => {
    if (!INFLUXDB_URL) return textResult("Error: INFLUXDB_V3_URL not configured");
    const url = `https://${INFLUXDB_URL}/api/v1/query?query=${encodeURIComponent(query)}`;
    const data = await fetchJson(url, {
      headers: { Authorization: `Bearer ${INFLUXDB_TOKEN}` },
    });
    if (data.error) return textResult(`Error: ${data.error}`);
    return textResult(JSON.stringify(data, null, 2));
  }
);

const listLabels = tool(
  "list_metric_labels",
  "List all label names or values for a specific label in VictoriaMetrics. Useful for discovering metric dimensions.",
  {
    label: z
      .string()
      .optional()
      .describe(
        "If provided, list values for this label. If omitted, list all label names."
      ),
  },
  async ({ label }) => {
    if (!INFLUXDB_URL) return textResult("Error: INFLUXDB_V3_URL not configured");
    const path = label
      ? `/api/v1/label/${encodeURIComponent(label)}/values`
      : "/api/v1/labels";
    const data = await fetchJson(`https://${INFLUXDB_URL}${path}`, {
      headers: { Authorization: `Bearer ${INFLUXDB_TOKEN}` },
    });
    if (data.error) return textResult(`Error: ${data.error}`);
    return textResult(JSON.stringify(data.data ?? data, null, 2));
  }
);

// --- Create the MCP server ---

export function createHomeAutomationServer() {
  return createSdkMcpServer({
    name: "home-automation",
    version: "1.0.0",
    tools: [
      sonosGetZones,
      sonosPlay,
      sonosPause,
      sonosVolume,
      sonosFavourite,
      roborockListZones,
      roborockCleanZone,
      listMetrics,
      queryMetrics,
      listLabels,
    ],
  });
}
