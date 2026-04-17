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
  GOOGLE_PLACES_RESULTS,
  MOCK_WEBPAGE_HTML,
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

      // Title generation requests get a fixed response (don't consume the queue)
      const isTitleRequest = body.includes("Generate a very short title");
      if (isTitleRequest) {
        console.log("[mock-server] Title generation request — returning fixed title");
        const titleResp = buildGeminiResponse({ text: "Test Chat Title" });
        jsonResponse(res, titleResp[0]);
        return;
      }

      const mockResp = getNextGeminiResponse();
      const responseChunks = buildGeminiResponse(mockResp);

      if (url.includes("alt=sse") || url.includes("streamGenerateContent")) {
        // Streaming: return SSE format
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        });
        for (const chunk of responseChunks) {
          res.write(`data: ${JSON.stringify(chunk)}\n\n`);
        }
        res.end();
      } else {
        // Non-streaming generateContent: return single response object (not array)
        // Merge all chunks into one response with combined parts
        const allParts: unknown[] = [];
        for (const chunk of responseChunks) {
          const c = chunk as { candidates?: { content?: { parts?: unknown[] } }[] };
          if (c.candidates?.[0]?.content?.parts) {
            allParts.push(...c.candidates[0].content.parts);
          }
        }
        jsonResponse(res, {
          candidates: [
            {
              content: { parts: allParts, role: "model" },
              finishReason: "STOP",
            },
          ],
        });
      }
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

    // ── Google Places API (google_places_search) ──────────
    if (url.includes("/places:searchText") && method === "POST") {
      jsonResponse(res, GOOGLE_PLACES_RESULTS);
      return;
    }

    // ── Google Places Photos API (analyze_place_photos) ──
    // Metadata request with skipHttpRedirect=true returns a photoUri
    if (url.includes("/photos/") && url.includes("/media")) {
      jsonResponse(res, {
        photoUri: `http://localhost:${(res.socket?.localPort ?? 9876)}/__mock/photo.jpg`,
      });
      return;
    }

    // Serve a tiny 1x1 JPEG for the photo URI
    if (url === "/__mock/photo.jpg") {
      // Minimal valid JPEG (1x1 pixel, red)
      const jpeg = Buffer.from(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoH" +
        "BwYIDAoMCwsKCwsNCw0OEA8QDQsRERMTFBQVFRgYGBobGxscHBwcHBz/2wBDAQME" +
        "BAUEBQkFBQkcDwsPHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc" +
        "HBwcHBwcHBwcHBwcHBz/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEA" +
        "AAAAAAAAAAECAwQFBgcICQoL/8QAFRABAAAAAAAAAAAAAAAAAAAAAf/EABQBAQAAAAAA" +
        "AAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwC/AB//2Q==",
        "base64"
      );
      res.writeHead(200, { "Content-Type": "image/jpeg", "Content-Length": String(jpeg.length) });
      res.end(jpeg);
      return;
    }

    // ── Mock PDF for fetch_pdf tool ──────────────────────
    if (url.endsWith(".pdf")) {
      // Return a minimal valid PDF
      const pdfContent = "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R>>endobj\n4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 100 700 Td (Vegan Menu) Tj ET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000206 00000 n \ntrailer<</Size 5/Root 1 0 R>>\nstartxref\n300\n%%EOF";
      res.writeHead(200, { "Content-Type": "application/pdf" });
      res.end(pdfContent);
      return;
    }

    // ── Mock webpage for fetch_webpage tool ──────────────
    // Any request to example.com paths (redirected via fetch interceptor)
    if (url.match(/^\/(greengarden|vegano|menu)/)) {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(MOCK_WEBPAGE_HTML);
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
