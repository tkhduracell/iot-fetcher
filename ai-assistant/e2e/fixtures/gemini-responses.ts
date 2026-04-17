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

/** Response that triggers google_places_search tool call */
export const PLACES_SEARCH_TOOL_CALL = {
  toolCalls: [
    {
      name: "google_places_search",
      args: { query: "vegan restaurants", location: "Södermalm, Stockholm" },
    },
  ],
  followUpText:
    "I found 2 vegan restaurants in Södermalm: Green Garden Vegan (4.6 stars) at Hornsgatan 42, and Vegano (4.3 stars) at Götgatan 15. Both have great reviews for their plant-based menus.",
};

/** Response that triggers fetch_webpage tool call */
export const FETCH_WEBPAGE_TOOL_CALL = {
  toolCalls: [
    {
      name: "fetch_webpage",
      args: { url: "https://example.com/greengarden" },
    },
  ],
  followUpText:
    "The Green Garden Vegan menu includes: Beyond Burger (149 SEK), Pad Thai with tofu (139 SEK), and an oat milk chocolate mousse (75 SEK). All clearly marked as vegan.",
};

/** Response that triggers analyze_place_photos tool call */
export const ANALYZE_PHOTOS_TOOL_CALL = {
  toolCalls: [
    {
      name: "analyze_place_photos",
      args: {
        photoNames: ["places/abc/photos/1", "places/abc/photos/2"],
        restaurantName: "Green Garden Vegan",
      },
    },
  ],
  followUpText:
    "I analyzed 2 photos from Green Garden Vegan. Photo 1 shows a menu board listing a Beyond Burger (149 SEK) and Pad Thai with tofu (139 SEK), both marked with a leaf icon as vegan. Photo 2 shows a colorful poke bowl with tofu.",
};

/** Response that triggers fetch_pdf tool call */
export const FETCH_PDF_TOOL_CALL = {
  toolCalls: [
    {
      name: "fetch_pdf",
      args: { url: "https://example.com/menu.pdf" },
    },
  ],
  followUpText:
    "I downloaded the PDF menu. The restaurant offers several vegan dishes including a Vegan Menu section with plant-based options.",
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
