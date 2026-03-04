import { test as base, type Page, type BrowserContext } from "@playwright/test";
import { authenticateContext, TEST_USER } from "./auth";
import { createTestDb, cleanupTestDb, seedSession, seedMessage } from "./db";
import type Database from "better-sqlite3";

type TestFixtures = {
  /** A Page that is already authenticated as TEST_USER */
  authenticatedPage: Page;
  /** Authenticate the default context (use when you need the context, not just a page) */
  authenticatedContext: BrowserContext;
  /** A fresh test database. Cleaned up after test. */
  testDb: { db: Database.Database; dbPath: string };
};

export const test = base.extend<TestFixtures>({
  authenticatedContext: async ({ context, baseURL }, use) => {
    await authenticateContext(context, baseURL ?? "http://localhost:3099");
    await use(context);
  },

  authenticatedPage: async ({ context, baseURL }, use) => {
    await authenticateContext(context, baseURL ?? "http://localhost:3099");
    const page = await context.newPage();
    await use(page);
    await page.close();
  },

  testDb: async ({}, use, testInfo) => {
    const name = testInfo.title.replace(/\W+/g, "-").slice(0, 50);
    const { db, dbPath } = createTestDb(name);
    await use({ db, dbPath });
    db.close();
    cleanupTestDb(dbPath);
  },
});

export { expect } from "@playwright/test";
export { TEST_USER } from "./auth";
export { seedSession, seedMessage } from "./db";

/** Mock server control helper — set the next Gemini response */
export async function setMockGeminiResponse(
  response: {
    text?: string;
    toolCalls?: { name: string; args: Record<string, unknown> }[];
  },
  mockPort = 9876
): Promise<void> {
  await fetch(`http://localhost:${mockPort}/__mock/set-response`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(response),
  });
}
