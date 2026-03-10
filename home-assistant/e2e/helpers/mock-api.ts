/**
 * Helper functions for tests to configure the mock server responses.
 * Tests import these to set up specific Gemini/tool responses before
 * interacting with the UI.
 */

const MOCK_BASE = `http://localhost:${process.env.MOCK_SERVER_PORT || "9876"}`;

export type MockGeminiResponse = {
  text?: string;
  toolCalls?: { name: string; args: Record<string, unknown> }[];
  followUpText?: string;
};

/**
 * Set the default Gemini response. Clears any queued responses.
 */
export async function setGeminiResponse(
  response: MockGeminiResponse
): Promise<void> {
  await fetch(`${MOCK_BASE}/__mock/set-response`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(response),
  });
}

/**
 * Enqueue a Gemini response. Responses are consumed in FIFO order.
 * When the queue is empty, the default response is used.
 */
export async function enqueueGeminiResponse(
  response: MockGeminiResponse
): Promise<void> {
  await fetch(`${MOCK_BASE}/__mock/enqueue-response`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(response),
  });
}

/**
 * Reset mock server state (queue, default response, request log).
 */
export async function resetMockServer(): Promise<void> {
  await fetch(`${MOCK_BASE}/__mock/reset`, { method: "POST" });
}

/**
 * Get the request log from the mock server (for assertions).
 */
export async function getRequestLog(): Promise<
  { url: string; body: string; timestamp: number }[]
> {
  const res = await fetch(`${MOCK_BASE}/__mock/request-log`);
  return res.json();
}
