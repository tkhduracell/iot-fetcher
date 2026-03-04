/**
 * Mock HTTP server that handles:
 * 1. Gemini API requests (from Google ADK)
 * 2. Sonos HTTP API requests
 * 3. VictoriaMetrics API requests
 * 4. Brave Search API requests (redirected via fetch interceptor)
 * 5. Google Sheets/Drive API requests (redirected via fetch interceptor)
 * 6. A control endpoint (/__mock/*) for per-test response customization
 */
import http from "http";
import {
  SONOS_ZONES,
  SONOS_ACTION_SUCCESS,
  METRICS_NAMES,
  METRICS_QUERY_RESULT,
  METRICS_LABELS,
  BRAVE_SEARCH_RESULTS,
  GOOGLE_DRIVE_FILES,
  GOOGLE_SHEETS_DATA,
} from "../fixtures/tool-responses";

export type GeminiMockState = {
  /** Queue of responses. Each call to generateContent pops the first. */
  responseQueue: GeminiMockResponse[];
  /** Default response when queue is empty */
  defaultResponse: GeminiMockResponse;
  /** Record of all requests received (for assertions) */
  requestLog: { url: string; body: string; timestamp: number }[];
};

export type GeminiMockResponse = {
  text?: string;
  toolCalls?: { name: string; args: Record<string, unknown> }[];
  followUpText?: string;
};

const state: GeminiMockState = {
  responseQueue: [],
  defaultResponse: { text: "Mock response from Gemini." },
  requestLog: [],
};

function buildGeminiResponse(mockResp: GeminiMockResponse): object[] {
  const chunks: object[] = [];

  // If there are tool calls, send the function call chunk first
  if (mockResp.toolCalls && mockResp.toolCalls.length > 0) {
    const parts = mockResp.toolCalls.map((tc) => ({
      functionCall: { name: tc.name, args: tc.args },
    }));
    chunks.push({
      candidates: [
        {
          content: { parts, role: "model" },
          finishReason: "STOP",
        },
      ],
    });
  }

  // Text response (or follow-up text after tool calls)
  const text = mockResp.text || mockResp.followUpText;
  if (text) {
    chunks.push({
      candidates: [
        {
          content: { parts: [{ text }], role: "model" },
          finishReason: "STOP",
        },
      ],
    });
  }

  // If nothing was generated, return a minimal response
  if (chunks.length === 0) {
    chunks.push({
      candidates: [
        {
          content: { parts: [{ text: "" }], role: "model" },
          finishReason: "STOP",
        },
      ],
    });
  }

  return chunks;
}

function getNextGeminiResponse(): GeminiMockResponse {
  if (state.responseQueue.length > 0) {
    return state.responseQueue.shift()!;
  }
  return state.defaultResponse;
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => resolve(data));
  });
}

function jsonResponse(
  res: http.ServerResponse,
  data: unknown,
  status = 200
) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

export function createMockServer(): http.Server {
  const server = http.createServer(async (req, res) => {
    try {
    const url = req.url ?? "";
    const method = req.method ?? "GET";

    // ── Control endpoints ──────────────────────────────────
    if (url === "/__mock/set-response" && method === "POST") {
      const body = JSON.parse(await readBody(req));
      state.defaultResponse = body;
      state.responseQueue = [];
      jsonResponse(res, { ok: true });
      return;
    }

    if (url === "/__mock/enqueue-response" && method === "POST") {
      const body = JSON.parse(await readBody(req));
      state.responseQueue.push(body);
      jsonResponse(res, { ok: true, queueLength: state.responseQueue.length });
      return;
    }

    if (url === "/__mock/reset" && method === "POST") {
      state.responseQueue = [];
      state.defaultResponse = { text: "Mock response from Gemini." };
      state.requestLog = [];
      jsonResponse(res, { ok: true });
      return;
    }

    if (url === "/__mock/request-log" && method === "GET") {
      jsonResponse(res, state.requestLog);
      return;
    }

    // ── Gemini API ─────────────────────────────────────────
    if (url.includes("streamGenerateContent") || url.includes("generateContent")) {
      const body = await readBody(req);
      state.requestLog.push({ url, body, timestamp: Date.now() });

      const mockResp = getNextGeminiResponse();
      const responseChunks = buildGeminiResponse(mockResp);

      // Return as JSON array (ADK expects streamed JSON chunks)
      jsonResponse(res, responseChunks);
      return;
    }

    // Also handle model listing that ADK may call
    if (url.includes("/models") && !url.includes("generateContent")) {
      jsonResponse(res, {
        models: [
          {
            name: "models/gemini-2.0-flash",
            displayName: "Gemini 2.0 Flash",
            supportedGenerationMethods: ["generateContent", "streamGenerateContent"],
          },
        ],
      });
      return;
    }

    // ── Sonos HTTP API ─────────────────────────────────────
    if (url === "/zones") {
      jsonResponse(res, SONOS_ZONES);
      return;
    }

    if (url.match(/\/[^/]+\/play$/)) {
      jsonResponse(res, SONOS_ACTION_SUCCESS);
      return;
    }

    if (url.match(/\/[^/]+\/pause$/)) {
      jsonResponse(res, SONOS_ACTION_SUCCESS);
      return;
    }

    if (url.match(/\/[^/]+\/volume\/\d+$/)) {
      jsonResponse(res, SONOS_ACTION_SUCCESS);
      return;
    }

    if (url.match(/\/[^/]+\/favourite\//)) {
      jsonResponse(res, SONOS_ACTION_SUCCESS);
      return;
    }

    // ── VictoriaMetrics API ────────────────────────────────
    if (url.includes("/api/v1/label/__name__/values")) {
      jsonResponse(res, METRICS_NAMES);
      return;
    }

    if (url.includes("/api/v1/labels")) {
      jsonResponse(res, METRICS_LABELS);
      return;
    }

    if (url.includes("/api/v1/label/") && url.includes("/values")) {
      // Generic label values
      jsonResponse(res, { status: "success", data: ["value1", "value2"] });
      return;
    }

    if (url.includes("/api/v1/query")) {
      jsonResponse(res, METRICS_QUERY_RESULT);
      return;
    }

    // ── Brave Search API ───────────────────────────────────
    if (url.includes("/res/v1/web/search")) {
      jsonResponse(res, BRAVE_SEARCH_RESULTS);
      return;
    }

    // ── Google Drive API (sheets_list) ─────────────────────
    if (url.includes("/drive/v3/files")) {
      jsonResponse(res, GOOGLE_DRIVE_FILES);
      return;
    }

    // ── Google Sheets API (sheets_read) ────────────────────
    if (url.includes("/v4/spreadsheets/")) {
      jsonResponse(res, GOOGLE_SHEETS_DATA);
      return;
    }

    // ── Fallback ───────────────────────────────────────────
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Mock server: no handler for " + url }));
    } catch (err) {
      console.error("[mock-server] Request handler error:", err);
      if (!res.headersSent) {
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Mock server internal error" }));
      }
    }
  });

  return server;
}

/** Start the mock server and return a cleanup function */
export async function startMockServer(
  port = 9876
): Promise<{ port: number; close: () => Promise<void> }> {
  const server = createMockServer();
  await new Promise<void>((resolve) => server.listen(port, resolve));
  return {
    port,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}
