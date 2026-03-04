/**
 * Playwright global teardown: stops the mock server after all tests.
 */
export default async function globalTeardown() {
  const server = (globalThis as Record<string, unknown>).__mockServer as
    | import("http").Server
    | undefined;
  if (server) {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    console.log("[global-teardown] Mock server stopped");
  }
}
