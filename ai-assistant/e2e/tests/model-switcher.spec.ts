import { test, expect } from "../helpers";
import { setGeminiResponse, resetMockServer } from "../helpers/mock-api";
import { SIMPLE_TEXT_RESPONSE } from "../fixtures/gemini-responses";

async function safeResetMock() {
  try {
    await resetMockServer();
  } catch { /* ignore */ }
}

async function safeSetGeminiResponse(response: { text?: string }) {
  try {
    await setGeminiResponse(response);
  } catch { /* ignore */ }
}

test.describe("Model Switcher", () => {
  test.beforeEach(async () => {
    await safeResetMock();
  });

  test("model switcher appears in header when in a chat session", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");

    // Model switcher should NOT be visible on home page
    const modelSelect = page.locator("select");
    await expect(modelSelect).not.toBeVisible();

    // Navigate to a chat session
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Model switcher should now be visible in the header
    await expect(modelSelect).toBeVisible();
  });

  test("default model is Gemini 3 Flash", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const modelSelect = page.locator("select");
    await expect(modelSelect).toHaveValue("gemini-3-flash-preview");
  });

  test("can switch to a different model", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const modelSelect = page.locator("select");

    // Switch to Gemini 3.1 Pro
    await modelSelect.selectOption("gemini-3.1-pro-preview");
    await expect(modelSelect).toHaveValue("gemini-3.1-pro-preview");
  });

  test("model selection persists after page reload", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);
    const chatUrl = page.url();

    // Switch to Gemini 3.1 Pro
    const modelSelect = page.locator("select");
    await modelSelect.selectOption("gemini-3.1-pro-preview");

    // Wait for the PATCH request to complete
    await page.waitForResponse(
      (resp) => resp.url().includes("/api/sessions/") && resp.request().method() === "PATCH",
      { timeout: 5_000 }
    );

    // Reload
    await page.goto(chatUrl);

    // Model should still be Gemini 3.1 Pro
    await expect(page.locator("select")).toHaveValue("gemini-3.1-pro-preview", {
      timeout: 5000,
    });
  });

  test("all model options are available", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    const modelSelect = page.locator("select");
    const options = modelSelect.locator("option");

    await expect(options).toHaveCount(3);
    await expect(options.nth(0)).toHaveText("Gemini 3 Flash");
    await expect(options.nth(1)).toHaveText("Gemini 3.1 Pro");
    await expect(options.nth(2)).toHaveText("Gemini 3.1 Flash Lite");
  });

  test("chat works after switching model", async ({
    authenticatedPage: page,
  }) => {
    await safeSetGeminiResponse(SIMPLE_TEXT_RESPONSE);

    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Switch model
    const modelSelect = page.locator("select");
    await modelSelect.selectOption("gemini-3.1-flash-lite-preview");

    // Send a message
    const chatResponsePromise = page.waitForResponse(
      (resp) => resp.url().includes("/api/chat"),
      { timeout: 20_000 }
    );
    const input = page.locator('textarea[placeholder="Message..."]');
    await input.fill("Hello with new model");
    await input.press("Enter");

    const chatResponse = await chatResponsePromise;
    expect(chatResponse.status()).toBe(200);

    // Should get a response
    await expect(
      page.locator(".prose").last()
    ).not.toBeEmpty({ timeout: 15_000 });
  });
});
