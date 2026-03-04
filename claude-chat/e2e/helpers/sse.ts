import type { Page } from "@playwright/test";

export type SSEEvent =
  | { type: "session_id"; sessionId: string }
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: unknown }
  | { type: "tool_result"; id: string; result: string }
  | { type: "done" }
  | { type: "error"; error: string };

/**
 * Parse raw SSE text into structured events.
 * Handles the `data: {...}\n\n` format used by the chat API.
 */
export function parseSSEEvents(raw: string): SSEEvent[] {
  const events: SSEEvent[] = [];
  const lines = raw.split("\n");

  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;
    const data = line.slice(6).trim();
    if (data === "[DONE]") continue;

    try {
      events.push(JSON.parse(data) as SSEEvent);
    } catch {
      // skip unparseable lines
    }
  }

  return events;
}

/**
 * Collect the full assistant text from a stream of SSE events.
 */
export function extractAssistantText(events: SSEEvent[]): string {
  return events
    .filter((e): e is SSEEvent & { type: "text" } => e.type === "text")
    .map((e) => e.text)
    .join("");
}

/**
 * Wait for the chat response to finish streaming in the UI.
 * Looks for the streaming indicator to disappear.
 */
export async function waitForStreamingComplete(
  page: Page,
  opts: { timeout?: number } = {}
): Promise<void> {
  const timeout = opts.timeout ?? 30_000;

  // Wait for at least one assistant message to appear
  await page.locator('[data-testid="assistant-message"]').first().waitFor({
    state: "visible",
    timeout,
  });

  // Wait for streaming indicator to disappear (if present)
  const streamingIndicator = page.locator('[data-testid="streaming-indicator"]');
  if (await streamingIndicator.isVisible().catch(() => false)) {
    await streamingIndicator.waitFor({ state: "hidden", timeout });
  }
}

/**
 * Send a chat message via the UI and wait for the response to complete.
 */
export async function sendChatMessage(
  page: Page,
  message: string,
  opts: { timeout?: number } = {}
): Promise<void> {
  const input = page.locator(
    '[data-testid="chat-input"], textarea[placeholder*="message"], input[placeholder*="message"]'
  );
  await input.fill(message);

  const sendButton = page.locator(
    '[data-testid="send-button"], button[type="submit"]'
  );
  await sendButton.click();

  await waitForStreamingComplete(page, opts);
}
