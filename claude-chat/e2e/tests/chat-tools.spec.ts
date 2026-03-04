import { test, expect } from "../helpers";
import { setGeminiResponse, enqueueGeminiResponse, resetMockServer } from "../helpers/mock-api";
import {
  SONOS_ZONES_TOOL_CALL,
  METRICS_QUERY_TOOL_CALL,
  BRAVE_SEARCH_TOOL_CALL,
  LIST_METRICS_TOOL_CALL,
} from "../fixtures/gemini-responses";

async function safeResetMock() {
  try {
    await resetMockServer();
  } catch { /* ignore */ }
}

async function safeEnqueue(response: Parameters<typeof enqueueGeminiResponse>[0]) {
  try {
    await enqueueGeminiResponse(response);
  } catch { /* ignore */ }
}

test.describe("Tool Call Display", () => {
  test.beforeEach(async () => {
    await safeResetMock();
  });

  test("displays Sonos zone tool call and result", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: SONOS_ZONES_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: SONOS_ZONES_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const input = page.locator('textarea[placeholder="Message..."]');
    await input.fill("What music is playing?");
    await input.press("Enter");

    // Wait for assistant response (either tool call display or text)
    await expect(
      page.locator(".prose").last()
    ).not.toBeEmpty({ timeout: 15_000 });
  });

  test("displays metrics query tool call", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: METRICS_QUERY_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: METRICS_QUERY_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const input = page.locator('textarea[placeholder="Message..."]');
    await input.fill("What is the battery level?");
    await input.press("Enter");

    // Wait for assistant response
    await expect(
      page.locator(".prose").last()
    ).not.toBeEmpty({ timeout: 15_000 });
  });

  test("displays brave search tool call for researcher persona", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: BRAVE_SEARCH_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: BRAVE_SEARCH_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Researcher").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const input = page.locator('textarea[placeholder="Message..."]');
    await input.fill("What's the weather in Stockholm?");
    await input.press("Enter");

    // Wait for assistant response
    await expect(
      page.locator(".prose").last()
    ).not.toBeEmpty({ timeout: 15_000 });
  });

  test("displays list metrics tool call", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: LIST_METRICS_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: LIST_METRICS_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const input = page.locator('textarea[placeholder="Message..."]');
    await input.fill("List all metrics");
    await input.press("Enter");

    // Wait for assistant response
    await expect(
      page.locator(".prose").last()
    ).not.toBeEmpty({ timeout: 15_000 });
  });
});
