/**
 * Deterministic mock responses for tool HTTP endpoints.
 * These are returned by the mock server when tools make outbound HTTP calls.
 */

/** Mock response for GET /zones (Sonos HTTP API) */
export const SONOS_ZONES = [
  {
    coordinator: {
      roomName: "Living Room",
      state: {
        volume: 30,
        playbackState: "PLAYING",
        currentTrack: {
          artist: "Miles Davis",
          title: "So What",
          album: "Kind of Blue",
        },
      },
    },
    members: [{ roomName: "Living Room" }],
  },
  {
    coordinator: {
      roomName: "Kitchen",
      state: {
        volume: 20,
        playbackState: "STOPPED",
        currentTrack: {
          artist: "",
          title: "",
          album: "",
        },
      },
    },
    members: [{ roomName: "Kitchen" }],
  },
];

/** Mock response for sonos play/pause actions */
export const SONOS_ACTION_SUCCESS = { status: "ok" };

/** Mock response for VictoriaMetrics label/__name__/values */
export const METRICS_NAMES = {
  status: "success",
  data: [
    "sigenergy_battery_soc",
    "sigenergy_pv_power",
    "tapo_device_power",
    "tapo_device_energy_today",
    "sigenergy_grid_power",
  ],
};

/** Mock response for VictoriaMetrics /api/v1/query */
export const METRICS_QUERY_RESULT = {
  status: "success",
  data: {
    resultType: "vector",
    result: [
      {
        metric: { __name__: "sigenergy_battery_soc", instance: "sigenergy" },
        value: [1700000000, "78"],
      },
    ],
  },
};

/** Mock response for VictoriaMetrics /api/v1/labels */
export const METRICS_LABELS = {
  status: "success",
  data: ["__name__", "instance", "job", "room", "device"],
};

/** Mock response for Brave Search API */
export const BRAVE_SEARCH_RESULTS = {
  web: {
    results: [
      {
        title: "Stockholm Weather Forecast",
        url: "https://example.com/weather/stockholm",
        description: "Current weather in Stockholm: Partly cloudy, 5C. Wind NW 10 km/h.",
      },
      {
        title: "Stockholm 10-Day Forecast",
        url: "https://example.com/forecast/stockholm",
        description: "Extended forecast for Stockholm, Sweden. Expect rain later this week.",
      },
      {
        title: "Weather Stockholm - Swedish Meteorological Institute",
        url: "https://example.com/smhi/stockholm",
        description: "Official weather data for Stockholm area.",
      },
    ],
  },
};

/** Mock response for Google Drive file listing (sheets_list) */
export const GOOGLE_DRIVE_FILES = {
  files: [
    {
      id: "spreadsheet-id-1",
      name: "Home Energy Log",
      modifiedTime: "2025-01-15T10:30:00Z",
    },
    {
      id: "spreadsheet-id-2",
      name: "Device Inventory",
      modifiedTime: "2025-01-10T08:00:00Z",
    },
  ],
};

/** Mock response for Google Places API (google_places_search) */
export const GOOGLE_PLACES_RESULTS = {
  places: [
    {
      displayName: { text: "Green Garden Vegan" },
      formattedAddress: "Hornsgatan 42, 118 20 Stockholm, Sweden",
      rating: 4.6,
      userRatingCount: 234,
      websiteUri: "https://example.com/greengarden",
      googleMapsUri: "https://maps.google.com/?cid=123456",
      reviews: [
        { text: { text: "Amazing vegan burgers and great atmosphere!" } },
        { text: { text: "Best plant-based food in Södermalm" } },
      ],
      regularOpeningHours: {
        weekdayDescriptions: [
          "Monday: 11:00 AM – 9:00 PM",
          "Tuesday: 11:00 AM – 9:00 PM",
        ],
      },
      photos: [{ name: "places/abc/photos/1" }, { name: "places/abc/photos/2" }],
    },
    {
      displayName: { text: "Vegano" },
      formattedAddress: "Götgatan 15, 116 46 Stockholm, Sweden",
      rating: 4.3,
      userRatingCount: 156,
      websiteUri: "https://example.com/vegano",
      googleMapsUri: "https://maps.google.com/?cid=789012",
      reviews: [
        { text: { text: "Great vegan pizza and pasta options" } },
      ],
      regularOpeningHours: {
        weekdayDescriptions: ["Monday: 10:00 AM – 10:00 PM"],
      },
      photos: [{ name: "places/def/photos/1" }],
    },
  ],
};

/** Mock HTML page for fetch_webpage (restaurant menu page) */
export const MOCK_WEBPAGE_HTML = `<!DOCTYPE html>
<html><head><title>Green Garden Vegan - Menu</title></head>
<body>
<h1>Our Menu</h1>
<h2>Starters</h2>
<ul>
  <li>Edamame with sea salt - 65 SEK</li>
  <li>Crispy tofu bites with sriracha mayo - 79 SEK</li>
</ul>
<h2>Mains</h2>
<ul>
  <li>Beyond Burger with fries - 149 SEK (vegan)</li>
  <li>Pad Thai with tofu - 139 SEK (vegan)</li>
  <li>Mushroom risotto - 145 SEK (can be made vegan)</li>
</ul>
<h2>Desserts</h2>
<ul>
  <li>Oat milk chocolate mousse - 75 SEK (vegan)</li>
</ul>
<p><a href="/menu.pdf">Download full menu (PDF)</a></p>
</body></html>`;

/** Mock response for Google Sheets read (sheets_read) */
export const GOOGLE_SHEETS_DATA = {
  range: "Sheet1!A1:D5",
  values: [
    ["Date", "PV Production", "Consumption", "Grid Export"],
    ["2025-01-15", "12.5", "8.3", "4.2"],
    ["2025-01-14", "10.1", "9.0", "1.1"],
    ["2025-01-13", "15.2", "7.5", "7.7"],
    ["2025-01-12", "3.8", "10.2", "-6.4"],
  ],
};
