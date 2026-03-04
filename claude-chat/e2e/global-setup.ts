/**
 * Playwright global setup: starts the mock server before all tests.
 */
import { createMockServer } from "./mocks/mock-server";
import http from "http";

const MOCK_PORT = parseInt(process.env.MOCK_SERVER_PORT || "9876", 10);

/**
 * Check if the mock server is responsive by hitting its control endpoint.
 */
function isMockServerHealthy(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.request(
      { hostname: "localhost", port, path: "/__mock/request-log", method: "GET", timeout: 1000 },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          try {
            JSON.parse(data);
            resolve(true);
          } catch {
            resolve(false);
          }
        });
      }
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

/**
 * Try to start the mock server on the given port. Returns the server on
 * success, or null if the port is already in use.
 */
function tryListen(server: http.Server, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    server.once("error", (err: NodeJS.ErrnoException) => {
      if (err.code === "EADDRINUSE") {
        resolve(false);
      } else {
        resolve(false);
      }
    });
    server.listen(port, () => {
      server.removeAllListeners("error");
      resolve(true);
    });
  });
}

export default async function globalSetup() {
  // First check if a healthy mock server is already running
  const healthy = await isMockServerHealthy(MOCK_PORT);
  if (healthy) {
    console.log(`[global-setup] Mock server already running on port ${MOCK_PORT}`);
    return;
  }

  // Try to start the mock server, retrying a few times if port is busy
  const server = createMockServer();
  for (let attempt = 0; attempt < 5; attempt++) {
    const started = await tryListen(server, MOCK_PORT);
    if (started) {
      console.log(`[global-setup] Mock server started on port ${MOCK_PORT}`);
      (globalThis as Record<string, unknown>).__mockServer = server;
      return;
    }
    // Wait before retrying
    await new Promise((r) => setTimeout(r, 1000));
  }

  console.warn(`[global-setup] Could not start mock server on port ${MOCK_PORT} after retries`);
}
