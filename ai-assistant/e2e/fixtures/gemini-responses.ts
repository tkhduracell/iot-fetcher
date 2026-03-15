/**
 * Predefined Gemini API response fixtures for deterministic testing.
 *
 * These mimic the JSON structure returned by the Gemini
 * `streamGenerateContent` endpoint that Google ADK calls internally.
 */

/** A simple text-only response */
export const SIMPLE_TEXT_RESPONSE = {
  text: "Hello! I'm your home assistant. How can I help you today?",
};

/** Response that triggers the sonos_get_zones tool call */
export const SONOS_ZONES_TOOL_CALL = {
  toolCalls: [{ name: "sonos_get_zones", args: {} }],
  // After tool result, the model produces this follow-up text
  followUpText:
    "Currently, the Living Room is playing jazz at volume 30, and the Kitchen speaker is idle.",
};

/** Response that triggers the query_metrics tool call */
export const METRICS_QUERY_TOOL_CALL = {
  toolCalls: [
    {
      name: "query_metrics",
      args: { query: "sigenergy_battery_soc" },
    },
  ],
  followUpText: "The current battery state of charge is 78%.",
};

/** Response that triggers brave_search tool call */
export const BRAVE_SEARCH_TOOL_CALL = {
  toolCalls: [
    {
      name: "brave_search",
      args: { query: "weather in Stockholm", count: 3 },
    },
  ],
  followUpText:
    "Based on my search, Stockholm currently has partly cloudy skies with a temperature of 5 degrees Celsius.",
};

/** Response that triggers list_metrics tool call */
export const LIST_METRICS_TOOL_CALL = {
  toolCalls: [{ name: "list_metrics", args: {} }],
  followUpText:
    "Here are the available metrics: sigenergy_battery_soc, tapo_device_power, sigenergy_pv_power.",
};

/** A multi-turn conversation scenario: first response */
export const MULTI_TURN_FIRST = {
  text: "I can help you with that! What room would you like to control?",
};

/** A multi-turn conversation scenario: second response */
export const MULTI_TURN_SECOND = {
  toolCalls: [
    { name: "sonos_play", args: { room: "Living Room" } },
  ],
  followUpText: "Done! Music is now playing in the Living Room.",
};
