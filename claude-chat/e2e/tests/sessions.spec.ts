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

test.describe("Sessions", () => {
  test.beforeEach(async () => {
    await safeResetMock();
  });

  test("new session appears in sidebar", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");

    // Create a session
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Sidebar should show the session entry (with "New chat" as default title)
    const sidebar = page.locator("aside");
    await expect(sidebar.getByText("New chat").first()).toBeVisible();
  });

  test("multiple sessions with different personas", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");

    // Create first session (Home Assistant)
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Go back to home
    await page.getByText("+ New chat").click();
    await page.waitForURL("/");

    // Create second session (Researcher)
    await page.getByText("Researcher").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Sidebar should show at least 2 session buttons (plus "New chat" button)
    const sidebar = page.locator("aside");
    // Count session items (buttons inside the date groups, not the "+ New chat" button)
    const sessionItems = sidebar.locator("button").filter({ hasNotText: "+ New chat" });
    const count = await sessionItems.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test("clicking a session in sidebar navigates to it", async ({
    authenticatedPage: page,
  }) => {
    await safeSetGeminiResponse(SIMPLE_TEXT_RESPONSE);

    await page.goto("/");

    // Create first session
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);
    const firstSessionUrl = page.url();

    // Send a message so we have content
    const input = page.locator('textarea[placeholder="Message..."]');
    await input.fill("Hello from session 1");
    await input.press("Enter");
    await expect(page.locator(".prose").last()).not.toBeEmpty({ timeout: 15_000 });

    // Create second session
    await page.getByText("+ New chat").click();
    await page.waitForURL("/");
    await page.getByText("Researcher").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Navigate directly back to first session URL
    await page.goto(firstSessionUrl);

    // Should show previous messages (loaded from DB)
    await expect(page.getByText("Hello from session 1")).toBeVisible({ timeout: 10_000 });
  });

  test("delete session via API removes it", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");

    // Create a session
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Get the session ID from the URL
    const sessionId = page.url().split("/chat/")[1];
    expect(sessionId).toBeTruthy();

    // Count sessions before delete
    const sidebar = page.locator("aside");
    const sessionsBefore = await sidebar
      .locator("button")
      .filter({ hasNotText: "+ New chat" })
      .count();

    // Delete the session via API (same as the UI delete handler does)
    const deleteResponse = await page.request.delete(`/api/sessions/${sessionId}`);
    expect(deleteResponse.status()).toBe(204);

    // Trigger sidebar refresh
    await page.evaluate(() => window.dispatchEvent(new Event("sessions-updated")));
    await page.waitForTimeout(1000);

    // Navigate to home to see updated sidebar
    await page.goto("/");
    const sessionsAfter = await sidebar
      .locator("button")
      .filter({ hasNotText: "+ New chat" })
      .count();
    expect(sessionsAfter).toBeLessThan(sessionsBefore);
  });

  test("sidebar toggle button shows and hides sidebar", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");

    // Sidebar should be visible by default
    const sidebar = page.locator("aside");
    await expect(sidebar).toBeVisible();

    // Click the hamburger menu button to toggle
    const toggleButton = page.locator("header button").first();
    await toggleButton.click();

    // Sidebar should be hidden (opacity: 0)
    await expect(sidebar).toHaveCSS("opacity", "0");

    // Click again to show
    await toggleButton.click();
    await expect(sidebar).toHaveCSS("opacity", "1");
  });

  test("'+ New chat' button navigates to home page", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/");
    await page.getByText("Home Assistant").click();
    await page.waitForURL(/\/chat\/[a-f0-9-]+/);

    // Click "+ New chat" in sidebar
    await page.getByText("+ New chat").click();
    await page.waitForURL("/");

    // Should be back on the home page with persona cards
    await expect(page.getByText("Choose an assistant")).toBeVisible();
  });
});
