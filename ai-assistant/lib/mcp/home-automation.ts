import { FunctionTool } from "@google/adk";
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

// --- Sonos tools ---

const sonosGetZones = new FunctionTool({
  name: "sonos_get_zones",
  description:
    "List all Sonos speaker zones/rooms with their current playback state, volume, and what is playing",
  parameters: z.object({}),
  execute: async () => {
    const data = await fetchJson(`http://${SONOS_HOST}:5005/zones`);
    if (data.error) return { error: data.error };

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

    return { zones };
  },
});

const sonosPlay = new FunctionTool({
  name: "sonos_play",
  description: "Resume playback on a Sonos zone/room",
  parameters: z.object({
    room: z.string().describe("Room name, e.g. 'Living Room', 'Kitchen'"),
  }),
  execute: async ({ room }) => {
    const encoded = encodeURIComponent(room);
    return await fetchJson(`http://${SONOS_HOST}:5005/${encoded}/play`);
  },
});

const sonosPause = new FunctionTool({
  name: "sonos_pause",
  description: "Pause playback on a Sonos zone/room",
  parameters: z.object({
    room: z.string().describe("Room name"),
  }),
  execute: async ({ room }) => {
    const encoded = encodeURIComponent(room);
    return await fetchJson(`http://${SONOS_HOST}:5005/${encoded}/pause`);
  },
});

const sonosVolume = new FunctionTool({
  name: "sonos_volume",
  description: "Set the volume of a Sonos zone/room (0-100)",
  parameters: z.object({
    room: z.string().describe("Room name"),
    volume: z.number().min(0).max(100).describe("Volume level 0-100"),
  }),
  execute: async ({ room, volume }) => {
    const encoded = encodeURIComponent(room);
    return await fetchJson(
      `http://${SONOS_HOST}:5005/${encoded}/volume/${volume}`
    );
  },
});

const sonosMute = new FunctionTool({
  name: "sonos_mute",
  description: "Mute or unmute a Sonos zone/room",
  parameters: z.object({
    room: z.string().describe("Room name, e.g. 'Living Room', 'Kontor'"),
    mute: z
      .boolean()
      .describe("true to mute, false to unmute")
      .default(true),
  }),
  execute: async ({ room, mute }) => {
    const encoded = encodeURIComponent(room);
    const action = mute ? "mute" : "unmute";
    return await fetchJson(
      `http://${SONOS_HOST}:5005/${encoded}/${action}`
    );
  },
});

const sonosFavourite = new FunctionTool({
  name: "sonos_favourite",
  description: "Play a Sonos favourite/playlist on a zone/room",
  parameters: z.object({
    room: z.string().describe("Room name"),
    favourite: z.string().describe("Name of the favourite or playlist"),
  }),
  execute: async ({ room, favourite }) => {
    const encodedRoom = encodeURIComponent(room);
    const encodedFav = encodeURIComponent(favourite);
    return await fetchJson(
      `http://${SONOS_HOST}:5005/${encodedRoom}/favourite/${encodedFav}`
    );
  },
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

// --- Export all tools ---

export const homeAutomationTools = [
  listMetrics,
  queryMetrics,
  listLabels,
  sonosGetZones,
  sonosPlay,
  sonosPause,
  sonosVolume,
  sonosMute,
  sonosFavourite,
];
