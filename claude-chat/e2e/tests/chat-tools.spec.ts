import { test, expect } from "../helpers";
import { enqueueGeminiResponse, resetMockServer } from "../helpers/mock-api";
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

/** Send a chat message and return the assistant message container */
async function sendMessage(page: import("@playwright/test").Page, text: string) {
  const input = page.locator('textarea[placeholder="Message..."]');
  await input.fill(text);
  await input.press("Enter");
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

    await sendMessage(page, "What music is playing?");

    // Wait for tool call UI to appear with the tool name
    const toolCallButton = page.locator("button", { hasText: "Sonos Zones" });
    await expect(toolCallButton).toBeVisible({ timeout: 15_000 });

    // Wait for the success checkmark (tool call completed) — the SVG path for the checkmark
    const successIcon = toolCallButton.locator('svg path[d="M8 12l3 3 5-5"]');
    await expect(successIcon).toBeVisible({ timeout: 15_000 });

    // Verify the follow-up text appears after tool completion
    await expect(page.locator("text=Living Room")).toBeVisible({ timeout: 15_000 });
  });

  test("displays metrics query tool call", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: METRICS_QUERY_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: METRICS_QUERY_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await sendMessage(page, "What is the battery level?");

    // Wait for tool call UI
    const toolCallButton = page.locator("button", { hasText: "Query Metrics" });
    await expect(toolCallButton).toBeVisible({ timeout: 15_000 });

    // Wait for success status
    const successIcon = toolCallButton.locator('svg path[d="M8 12l3 3 5-5"]');
    await expect(successIcon).toBeVisible({ timeout: 15_000 });

    // Verify follow-up text
    await expect(page.locator("text=battery")).toBeVisible({ timeout: 15_000 });
  });

  test("displays brave search tool call for researcher persona", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: BRAVE_SEARCH_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: BRAVE_SEARCH_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Researcher").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await sendMessage(page, "What's the weather in Stockholm?");

    // Wait for tool call UI
    const toolCallButton = page.locator("button", { hasText: "Web Search" });
    await expect(toolCallButton).toBeVisible({ timeout: 15_000 });

    // Wait for success status
    const successIcon = toolCallButton.locator('svg path[d="M8 12l3 3 5-5"]');
    await expect(successIcon).toBeVisible({ timeout: 15_000 });

    // Verify follow-up text
    await expect(page.locator("text=Stockholm")).toBeVisible({ timeout: 15_000 });
  });

  test("displays list metrics tool call", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: LIST_METRICS_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: LIST_METRICS_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await sendMessage(page, "List all metrics");

    // Wait for tool call UI
    const toolCallButton = page.locator("button", { hasText: "List Metrics" });
    await expect(toolCallButton).toBeVisible({ timeout: 15_000 });

    // Wait for success status
    const successIcon = toolCallButton.locator('svg path[d="M8 12l3 3 5-5"]');
    await expect(successIcon).toBeVisible({ timeout: 15_000 });

    // Verify follow-up text
    await expect(page.locator("text=sigenergy_battery_soc")).toBeVisible({ timeout: 15_000 });
  });
});
