# Playwright E2E Testing Plan

## Overview

End-to-end tests for the agent-assistant Next.js 15 application using Playwright. The app uses Google ADK (Gemini), NextAuth v4 (Google OAuth), SQLite (better-sqlite3), and streaming SSE responses.

---

## 1. Authentication Bypass

### Strategy: Inject NextAuth session cookie directly

NextAuth v4 uses JWT-based sessions. We bypass Google OAuth by crafting a valid JWT session token and setting it as a cookie before each test.

**Implementation:**

Create `e2e/helpers/auth.ts`:

```ts
import { encode } from "next-auth/jwt";

const TEST_USER = {
  name: "Test User",
  email: "test@example.com",
};

export async function getAuthCookie(): Promise<{
  name: string;
  value: string;
}> {
  const token = await encode({
    token: {
      ...TEST_USER,
      sub: "test-user-id",
    },
    secret: process.env.NEXTAUTH_SECRET!,
  });

  return {
    name: "next-auth.session-token",
    value: token,
  };
}
```

**Environment requirements:**
- `NEXTAUTH_SECRET` must be set to a known value in the test `.env.test` file
- `ALLOWED_EMAILS` must include `test@example.com` (or be empty to allow all)

**Usage in tests:**

```ts
test.beforeEach(async ({ context }) => {
  const cookie = await getAuthCookie();
  await context.addCookies([{
    ...cookie,
    domain: "localhost",
    path: "/",
  }]);
});
```

---

## 2. Gemini API Mocking

### Strategy: Playwright route interception on `generativelanguage.googleapis.com`

The Google ADK (`@google/adk`) makes HTTP calls to `generativelanguage.googleapis.com` from the Next.js server. Since these are server-side calls, we **cannot** intercept them with `page.route()` (which only intercepts browser requests).

### Approach: Mock HTTP server + environment variable

Start a lightweight mock server (e.g., using Node's `http.createServer`) that mimics the Gemini API responses. Point the ADK to it via `GOOGLE_GENAI_API_BASE_URL` or similar env var.

**Implementation:**

Create `e2e/mocks/gemini-mock-server.ts`:

```ts
import http from "http";

type MockResponse = {
  text: string;
  toolCalls?: { name: string; args: Record<string, unknown> }[];
};

let nextResponse: MockResponse = { text: "Hello from mock Gemini!" };

export function setMockResponse(response: MockResponse) {
  nextResponse = response;
}

export function createGeminiMockServer(): http.Server {
  return http.createServer((req, res) => {
    // Handle generateContent / streamGenerateContent endpoints
    if (req.url?.includes("streamGenerateContent")) {
      res.writeHead(200, { "Content-Type": "application/json" });

      const parts: object[] = [];
      if (nextResponse.text) {
        parts.push({ text: nextResponse.text });
      }
      if (nextResponse.toolCalls) {
        for (const tc of nextResponse.toolCalls) {
          parts.push({
            functionCall: { name: tc.name, args: tc.args },
          });
        }
      }

      const responseBody = [
        {
          candidates: [
            {
              content: { parts, role: "model" },
              finishReason: "STOP",
            },
          ],
        },
      ];

      res.end(JSON.stringify(responseBody));
      return;
    }

    // Default: 404
    res.writeHead(404);
    res.end("Not found");
  });
}
```

**Configuration:**
- Set `GOOGLE_GENAI_API_BASE_URL=http://localhost:<MOCK_PORT>` in the test environment so ADK routes requests to our mock
- If ADK does not support a base URL override, use the `GOOGLE_GENAI_API_KEY` with a dummy value and intercept at the DNS/network level using a custom fetch or Node `--dns-result-order` approach. Fallback: patch `global.fetch` in a test setup file loaded via `NODE_OPTIONS`.

**Important:** Check if `@google/adk` respects a base URL env var. If not, the recommended fallback is a global fetch interceptor in the Next.js server process.

### Alternative: Global fetch interceptor

Create `e2e/helpers/setup-fetch-mock.ts` that gets loaded into the Next.js server process:

```ts
// Loaded via NODE_OPTIONS="--require ./e2e/helpers/setup-fetch-mock.js"
const originalFetch = global.fetch;
const MOCK_GEMINI_PORT = process.env.MOCK_GEMINI_PORT || "9876";

global.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.toString();
  if (url.includes("generativelanguage.googleapis.com")) {
    const mockUrl = url.replace(
      /https:\/\/generativelanguage\.googleapis\.com/,
      `http://localhost:${MOCK_GEMINI_PORT}`
    );
    return originalFetch(mockUrl, init);
  }
  return originalFetch(input, init);
};
```

---

## 3. Tool Call Mocking

### Strategy: Intercept outbound HTTP from the Next.js server

Tools make HTTP calls to external services:

| Tool Set | External Service | URL Pattern |
|----------|-----------------|-------------|
| home-automation (Sonos) | Sonos HTTP API | `http://<SONOS_HOST>:5005/*` |
| home-automation (Metrics) | VictoriaMetrics | `https://<INFLUXDB_V3_URL>/api/v1/*` |
| brave-search | Brave Search API | `https://api.search.brave.com/*` |
| google-sheets | Google APIs | `https://sheets.googleapis.com/*`, `https://www.googleapis.com/drive/*` |

### Approach: Same mock server, different routes

Extend the mock HTTP server to handle tool endpoints. Configure environment variables to point to the mock:

```env
SONOS_HOST=localhost
INFLUXDB_V3_URL=http://localhost:<MOCK_PORT>
BRAVE_API_KEY=test-key
```

Add routes to the mock server for:
- `GET /zones` - Returns mock Sonos zone data
- `GET /api/v1/label/__name__/values` - Returns mock metric names
- `GET /api/v1/query` - Returns mock metric data
- Brave Search and Google Sheets endpoints (routed via fetch interceptor)

For Brave Search and Google Sheets, which use HTTPS to external domains, the global fetch interceptor approach (same as Gemini) redirects those to the mock server.

---

## 4. Test Isolation

### Strategy: Per-test SQLite database + server restart

**Database isolation:**
- Set `SQLITE_PATH` to a unique temp file per test worker (e.g., `/tmp/agent-assistant-test-<workerId>.db`)
- Each test (or test file) starts with a fresh database
- Use `test.beforeEach` to delete and recreate the DB file

**ADK session isolation:**
- The `InMemoryRunner` uses in-memory session storage, which resets with the server
- For test isolation within a single server process, each test creates a new chat session with a unique UUID

**Server lifecycle:**
- Use Playwright's `webServer` config to start the Next.js dev server once per test run
- Tests create fresh sessions via the API, ensuring no cross-contamination

```ts
// playwright.config.ts
export default defineConfig({
  webServer: {
    command: "npm run dev",
    port: 3001,
    cwd: "./agent-assistant",
    reuseExistingServer: !process.env.CI,
    env: {
      SQLITE_PATH: "/tmp/agent-assistant-test.db",
      NEXTAUTH_SECRET: "test-secret-for-playwright",
      NEXTAUTH_URL: "http://localhost:3001",
      ALLOWED_EMAILS: "test@example.com",
      GOOGLE_CLIENT_ID: "test-client-id",
      GOOGLE_CLIENT_SECRET: "test-client-secret",
      GOOGLE_GENAI_API_KEY: "test-key",
      SONOS_HOST: "localhost",
      MOCK_GEMINI_PORT: "9876",
      NODE_OPTIONS: "--require ./e2e/helpers/setup-fetch-mock.js",
    },
  },
});
```

---

## 5. Project Structure

```
agent-assistant/
  e2e/
    playwright.config.ts          # Playwright configuration
    helpers/
      auth.ts                     # Auth cookie generation
      setup-fetch-mock.ts         # Global fetch interceptor (compiled to .js)
      mock-data.ts                # Shared mock response data
    mocks/
      gemini-mock-server.ts       # Mock server for Gemini + tools
      mock-responses.ts           # Predefined response fixtures
    tests/
      home-page.spec.ts           # Persona selection, navigation
      chat-basic.spec.ts          # Send message, receive streamed response
      chat-tool-calls.spec.ts     # Tool use display (Sonos, metrics, search)
      sessions.spec.ts            # Session CRUD, sidebar, persistence
      auth.spec.ts                # Unauthenticated redirect, sign-in page
```

---

## 6. Test Cases

### 6.1 Authentication (`auth.spec.ts`)
- **Unauthenticated redirect**: Visiting `/` without auth redirects to `/auth/signin`
- **API returns 401**: Calling `/api/sessions` without auth returns 401
- **Authenticated access**: With auth cookie, home page loads with persona cards

### 6.2 Home Page (`home-page.spec.ts`)
- **Renders persona cards**: All 3 personas (Home Assistant, Researcher, Data Analyst) are displayed
- **Persona selection creates session**: Clicking a persona card creates a session and navigates to `/chat/<id>`
- **Greeting shows user name**: "Hi, Test" greeting is visible

### 6.3 Basic Chat (`chat-basic.spec.ts`)
- **Send message and receive response**: Type a message, press Enter, see streamed response appear
- **Message persistence**: Refresh the page, messages are still visible (loaded from DB)
- **Empty state shows suggestions**: New chat session shows suggestion chips
- **Suggestion chips send messages**: Clicking a suggestion chip sends that text

### 6.4 Tool Calls (`chat-tool-calls.spec.ts`)
- **Tool call display**: When Gemini response includes a tool call, the tool call UI (ToolCallDisplay) renders with name and input
- **Tool result display**: After tool execution, the result is shown in the UI
- **Sonos zone query**: Mock Gemini to call `sonos_get_zones`, verify zones are displayed
- **Metrics query**: Mock Gemini to call `query_metrics`, verify data is displayed

### 6.5 Sessions (`sessions.spec.ts`)
- **Session appears in sidebar**: After creating a session, it appears in the sidebar
- **Session deletion**: Delete a session, verify it's removed from sidebar
- **Multiple sessions**: Create sessions with different personas, verify they're listed
- **Auto-generated title**: After first assistant response, session title updates in sidebar

### 6.6 Model Switcher (if covered in UI)
- **Switch model**: Change model in UI, verify next chat uses the new model

---

## 7. Key Configuration

### `playwright.config.ts`

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e/tests",
  fullyParallel: false, // Sequential to share one server
  retries: process.env.CI ? 1 : 0,
  workers: 1, // Single worker to share server + DB
  reporter: [["html", { open: "never" }], ["list"]],
  use: {
    baseURL: "http://localhost:3001",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    port: 3001,
    cwd: ".",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
```

### `e2e/.env.test`

```env
NEXTAUTH_SECRET=test-secret-for-playwright
NEXTAUTH_URL=http://localhost:3001
ALLOWED_EMAILS=test@example.com
GOOGLE_CLIENT_ID=test-client-id
GOOGLE_CLIENT_SECRET=test-client-secret
GOOGLE_GENAI_API_KEY=test-key
SONOS_HOST=localhost
INFLUXDB_V3_URL=http://localhost:9876
BRAVE_API_KEY=test-brave-key
SQLITE_PATH=/tmp/agent-assistant-playwright-test.db
```

---

## 8. Dependencies to Install

```json
{
  "devDependencies": {
    "@playwright/test": "^1.50.0",
    "next-auth": "^4.24.0"
  }
}
```

Note: `next-auth` is already a dependency. The `encode` function from `next-auth/jwt` is used in test helpers for cookie generation.

---

## 9. Mock Server Lifecycle

The mock server for Gemini + tools runs as a Playwright `globalSetup`:

```ts
// e2e/global-setup.ts
import { createGeminiMockServer } from "./mocks/gemini-mock-server";

export default async function globalSetup() {
  const server = createGeminiMockServer();
  await new Promise<void>((resolve) => server.listen(9876, resolve));
  // Store for teardown
  (globalThis as Record<string, unknown>).__mockServer = server;
}
```

```ts
// e2e/global-teardown.ts
export default async function globalTeardown() {
  const server = (globalThis as Record<string, unknown>).__mockServer as import("http").Server;
  server?.close();
}
```

For per-test response customization, the mock server exposes an HTTP control endpoint (e.g., `POST /__mock/set-response`) that tests call before interacting with the UI:

```ts
// In test:
await fetch("http://localhost:9876/__mock/set-response", {
  method: "POST",
  body: JSON.stringify({
    text: "The living room Sonos is playing jazz.",
    toolCalls: [{ name: "sonos_get_zones", args: {} }],
  }),
});
```

---

## 10. Running Tests

```bash
cd agent-assistant
npx playwright install chromium
npx playwright test
npx playwright test --ui  # Interactive mode
```

---

## Summary of Mocking Strategy

| Component | Mock Approach | Configuration |
|-----------|--------------|---------------|
| Google OAuth | JWT cookie injection via `next-auth/jwt` encode | `NEXTAUTH_SECRET` |
| Gemini API (ADK) | Global fetch interceptor + local mock HTTP server | `NODE_OPTIONS`, `MOCK_GEMINI_PORT` |
| Sonos HTTP API | Environment variable pointing to mock server | `SONOS_HOST=localhost` |
| VictoriaMetrics | Environment variable pointing to mock server | `INFLUXDB_V3_URL=http://localhost:9876` |
| Brave Search | Global fetch interceptor to mock server | `BRAVE_API_KEY=test-key` |
| Google Sheets | Global fetch interceptor to mock server | Dummy service account key |
| SQLite | Temp file per test run | `SQLITE_PATH=/tmp/...` |
