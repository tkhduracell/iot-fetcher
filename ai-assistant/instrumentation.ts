/**
 * Next.js instrumentation hook.
 * In test mode, patches global.fetch to redirect external API calls
 * to the local mock server.
 */
export async function register() {
  if (process.env.NODE_ENV === "test" && process.env.MOCK_SERVER_PORT) {
    const MOCK_PORT = process.env.MOCK_SERVER_PORT;
    const MOCK_BASE = `http://localhost:${MOCK_PORT}`;

    const REDIRECT_HOSTS = [
      "generativelanguage.googleapis.com",
      "api.search.brave.com",
      "sheets.googleapis.com",
      "www.googleapis.com",
    ];

    const originalFetch = globalThis.fetch;

    globalThis.fetch = async function patchedFetch(
      input: Parameters<typeof fetch>[0],
      init?: Parameters<typeof fetch>[1]
    ) {
      let url: string | undefined;
      if (typeof input === "string") {
        url = input;
      } else if (input instanceof URL) {
        url = input.toString();
      } else if (input && typeof input === "object" && "url" in input) {
        url = (input as { url: string }).url;
      }

      if (url) {
        for (const host of REDIRECT_HOSTS) {
          if (url.includes(host)) {
            try {
              const parsed = new URL(url);
              const mockUrl = `${MOCK_BASE}${parsed.pathname}${parsed.search}`;
              return originalFetch(mockUrl, init);
            } catch {
              const mockUrl = url.replace(
                new RegExp(`https?://[^/]*${host.replace(/\./g, "\\.")}`),
                MOCK_BASE
              );
              return originalFetch(mockUrl, init);
            }
          }
        }

        // Redirect Sonos HTTP API calls (localhost:5005) to mock server
        if (url.includes("localhost:5005") || url.includes("127.0.0.1:5005")) {
          try {
            const parsed = new URL(url);
            const mockUrl = `${MOCK_BASE}${parsed.pathname}${parsed.search}`;
            return originalFetch(mockUrl, init);
          } catch {
            // fall through
          }
        }
      }

      return originalFetch(input, init);
    };

    console.log(
      `[instrumentation] Test mode: intercepting API calls -> ${MOCK_BASE}`
    );
  }
}
