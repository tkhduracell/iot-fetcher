import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e/tests",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["html", { open: "never" }], ["list"]],
  use: {
    baseURL: "http://localhost:3099",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  webServer: {
    command: "npm run dev -- -p 3099",
    url: "http://localhost:3099",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    env: {
      NODE_ENV: "test",
      NEXTAUTH_SECRET: "test-secret-for-playwright",
      NEXTAUTH_URL: "http://localhost:3099",
      ALLOWED_EMAILS: "test@example.com",
      GOOGLE_CLIENT_ID: "test-client-id",
      GOOGLE_CLIENT_SECRET: "test-client-secret",
      GOOGLE_GENAI_API_KEY: "test-key",
      SONOS_HOST: "localhost",
      INFLUXDB_V3_URL: "http://localhost:9876",
      BRAVE_API_KEY: "test-brave-key",
      SQLITE_PATH: "/tmp/agent-assistant-playwright-test.db",
      MOCK_SERVER_PORT: "9876",
      NODE_OPTIONS: "--require ./e2e/helpers/setup-fetch-mock.js",
    },
  },
});
