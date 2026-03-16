import { test, expect } from "../helpers";
import { enqueueGeminiResponse, resetMockServer } from "../helpers/mock-api";
import {
  PLACES_SEARCH_TOOL_CALL,
  FETCH_WEBPAGE_TOOL_CALL,
  FETCH_PDF_TOOL_CALL,
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

async function sendMessage(page: import("@playwright/test").Page, text: string) {
  const input = page.locator('textarea[placeholder="Message..."]');
  await input.fill(text);
  await input.press("Enter");
}

test.describe("Vegan Researcher Persona", () => {
  test.beforeEach(async () => {
    await safeResetMock();
  });

  test("displays google_places_search tool call", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: PLACES_SEARCH_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: PLACES_SEARCH_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Vegan Researcher").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await sendMessage(page, "Find vegan restaurants near Södermalm, Stockholm");

    // Wait for tool call UI to appear
    const toolCallButton = page.locator("button", { hasText: "Places Search" });
    await expect(toolCallButton).toBeVisible({ timeout: 15_000 });

    // Wait for the success checkmark
    const successIcon = toolCallButton.locator('svg path[d="M8 12l3 3 5-5"]');
    await expect(successIcon).toBeVisible({ timeout: 15_000 });

    // Verify the follow-up text
    await expect(page.locator(".prose", { hasText: "Green Garden Vegan" })).toBeVisible({ timeout: 15_000 });
  });

  test("displays fetch_webpage tool call", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: FETCH_WEBPAGE_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: FETCH_WEBPAGE_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Vegan Researcher").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await sendMessage(page, "Check the menu at Green Garden Vegan");

    // Wait for tool call UI
    const toolCallButton = page.locator("button", { hasText: "Fetch Webpage" });
    await expect(toolCallButton).toBeVisible({ timeout: 15_000 });

    // Wait for success
    const successIcon = toolCallButton.locator('svg path[d="M8 12l3 3 5-5"]');
    await expect(successIcon).toBeVisible({ timeout: 15_000 });

    // Verify follow-up text
    await expect(page.locator(".prose", { hasText: "Beyond Burger" })).toBeVisible({ timeout: 15_000 });
  });

  test("displays fetch_pdf tool call", async ({
    authenticatedPage: page,
  }) => {
    await safeEnqueue({ toolCalls: FETCH_PDF_TOOL_CALL.toolCalls });
    await safeEnqueue({ text: FETCH_PDF_TOOL_CALL.followUpText });

    await page.goto("/");
    await page.getByText("Vegan Researcher").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await sendMessage(page, "Download the PDF menu from Green Garden");

    // Wait for tool call UI
    const toolCallButton = page.locator("button", { hasText: "Fetch PDF" });
    await expect(toolCallButton).toBeVisible({ timeout: 15_000 });

    // Wait for success
    const successIcon = toolCallButton.locator('svg path[d="M8 12l3 3 5-5"]');
    await expect(successIcon).toBeVisible({ timeout: 15_000 });

    // Verify follow-up text
    await expect(page.locator(".prose", { hasText: "Vegan Menu" })).toBeVisible({ timeout: 15_000 });
  });
});
