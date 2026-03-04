import { test, expect } from "../helpers";
import { setGeminiResponse, resetMockServer } from "../helpers/mock-api";
import { SIMPLE_TEXT_RESPONSE } from "../fixtures/gemini-responses";

/**
 * Helper to safely reset the mock server. If it fails (e.g., server not yet
 * ready), we log and continue rather than failing the test.
 */
async function safeResetMock() {
  try {
    await resetMockServer();
  } catch {
    // Mock server may not be reachable yet; tests will still work
    // because the default mock response is reasonable.
  }
}

/**
 * Helper to safely set a Gemini mock response.
 */
async function safeSetGeminiResponse(response: { text?: string }) {
  try {
    await setGeminiResponse(response);
  } catch {
    // If mock server isn't reachable, the default response will be used
  }
}

/**
 * Helper: send a message and wait for the assistant response to appear.
 * Uses the /api/chat response status + .prose element as reliable indicators.
 */
async function sendAndWaitForResponse(
  page: import("@playwright/test").Page,
  message: string
) {
  const chatResponsePromise = page.waitForResponse(
    (resp) => resp.url().includes("/api/chat"),
    { timeout: 20_000 }
  );

  const input = page.locator('textarea[placeholder="Message..."]');
  await input.fill(message);
  await input.press("Enter");

  // Wait for the API to respond
  const chatResponse = await chatResponsePromise;
  expect(chatResponse.status()).toBe(200);

  // Wait for the assistant message content to appear
  // The assistant message will have a .prose div with text
  await expect(
    page.locator(".prose").last()
  ).not.toBeEmpty({ timeout: 15_000 });
}

test.describe("Basic Chat", () => {
  test.beforeEach(async () => {
    await safeResetMock();
  });

  test("persona selection creates session and navigates to chat", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");

    // All 3 persona cards should be visible
    await expect(page.getByText("Home Assistant")).toBeVisible();
    await expect(page.getByText("Researcher")).toBeVisible();
    await expect(page.getByText("Data Analyst")).toBeVisible();

    // Greeting should include user name
    await expect(page.getByText("Hi, Test")).toBeVisible();

    // Click the Home Assistant persona
    await page.getByText("Home Assistant").click();

    // Should navigate to /chat/<uuid>
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);
    await expect(page).toHaveURL(/\/chat\/[a-f0-9-]+/);
  });

  test("empty chat shows suggestions for home-assistant persona", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Should show persona-specific suggestions
    await expect(page.getByText("What music is playing?")).toBeVisible();
    await expect(page.getByText("Vacuum the kitchen")).toBeVisible();
    await expect(page.getByText("Show energy usage")).toBeVisible();
    await expect(page.getByText("List all metrics")).toBeVisible();
  });

  test("send message and receive streamed response", async ({
    authenticatedPage: page,
  }) => {
    await safeSetGeminiResponse(SIMPLE_TEXT_RESPONSE);

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await sendAndWaitForResponse(page, "Hello there");

    // User message should be visible
    await expect(page.getByText("Hello there")).toBeVisible();

    // At least one assistant .prose div should have content
    const proseElements = page.locator(".prose");
    await expect(proseElements.last()).not.toBeEmpty();
  });

  test("clicking a suggestion chip sends that message", async ({
    authenticatedPage: page,
  }) => {
    await safeSetGeminiResponse(SIMPLE_TEXT_RESPONSE);

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Set up response listener before clicking
    const chatResponsePromise = page.waitForResponse(
      (resp) => resp.url().includes("/api/chat"),
      { timeout: 20_000 }
    );

    // Click a suggestion chip
    await page.getByText("What music is playing?").click();

    // Wait for the chat response
    const chatResponse = await chatResponsePromise;
    expect(chatResponse.status()).toBe(200);

    // Wait for the assistant message to appear
    await expect(
      page.locator(".prose").last()
    ).not.toBeEmpty({ timeout: 15_000 });
  });

  test("send button is disabled while streaming", async ({
    authenticatedPage: page,
  }) => {
    await safeSetGeminiResponse(SIMPLE_TEXT_RESPONSE);

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const input = page.locator('textarea[placeholder="Message..."]');
    await input.fill("Hello");
    await input.press("Enter");

    // Input should be disabled during streaming
    await expect(input).toBeDisabled();

    // Wait for response to complete, then input should re-enable
    await expect(
      page.locator(".prose").last()
    ).not.toBeEmpty({ timeout: 15_000 });
    await expect(input).toBeEnabled({ timeout: 5_000 });
  });

  test("message persistence after page reload", async ({
    authenticatedPage: page,
  }) => {
    await safeSetGeminiResponse(SIMPLE_TEXT_RESPONSE);

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const chatUrl = page.url();

    await sendAndWaitForResponse(page, "Remember this message");

    // Reload the page
    await page.goto(chatUrl);

    // Messages should persist (loaded from DB)
    await expect(page.getByText("Remember this message")).toBeVisible({ timeout: 10_000 });
  });

  test("researcher persona shows correct suggestions", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");
    await page.getByText("Researcher").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await expect(
      page.getByText("What's the weather in Stockholm?")
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.getByText("Latest news about home automation")
    ).toBeVisible();
  });

  test("data analyst persona shows correct suggestions", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");
    await page.getByText("Data Analyst").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    await expect(
      page.getByText("List available spreadsheets")
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.getByText("Show current energy production")
    ).toBeVisible();
  });
});
