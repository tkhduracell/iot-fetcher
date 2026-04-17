/**
 * Global fetch interceptor loaded into the Next.js server process via
 * NODE_OPTIONS="--require ./e2e/helpers/setup-fetch-mock.js"
 *
 * Redirects outbound HTTPS requests to external services to the local mock
 * server. This is necessary because Playwright's page.route() only intercepts
 * browser-side requests, not server-side fetch calls.
 */

const MOCK_PORT = process.env.MOCK_SERVER_PORT || "9876";
const MOCK_BASE = `http://localhost:${MOCK_PORT}`;

const REDIRECT_HOSTS = [
  "generativelanguage.googleapis.com",
  "api.search.brave.com",
  "sheets.googleapis.com",
  "www.googleapis.com",
  "places.googleapis.com",
  "example.com",
];

const originalFetch = globalThis.fetch;

globalThis.fetch = async function patchedFetch(input, init) {
  let url;
  if (typeof input === "string") {
    url = input;
  } else if (input instanceof URL) {
    url = input.toString();
  } else if (input && typeof input === "object" && "url" in input) {
    url = input.url;
  } else {
    return originalFetch(input, init);
  }

  for (const host of REDIRECT_HOSTS) {
    if (url.includes(host)) {
      // Rewrite to mock server, preserving the path
      try {
        const parsed = new URL(url);
        const mockUrl = `${MOCK_BASE}${parsed.pathname}${parsed.search}`;
        return originalFetch(mockUrl, init);
      } catch {
        // If URL parsing fails, try simple replacement
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
      return originalFetch(input, init);
    }
  }

  return originalFetch(input, init);
};

console.log(
  `[fetch-mock] Intercepting requests to: ${REDIRECT_HOSTS.join(", ")}, localhost:5005 -> ${MOCK_BASE}`
);
