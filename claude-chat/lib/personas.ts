export type PersonaConfig = {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  systemPrompt: string;
  toolSets: ("home-automation" | "brave-search" | "google-sheets")[];
  suggestions: string[];
};

export const personas: Record<string, PersonaConfig> = {
  "home-assistant": {
    id: "home-assistant",
    name: "Home Assistant",
    description: "Control Sonos, Roborock, and query home metrics",
    icon: "🏠",
    color: "#d4a574",
    systemPrompt: `You are a helpful home automation assistant for Filip's smart home.

You have access to tools for:
- **Sonos**: Control music playback, volume, favourites across rooms
- **Metrics**: Query VictoriaMetrics for home sensor data (energy, temperature, devices)

Guidelines:
- Be concise and helpful
- When asked about music, check what's playing first with sonos_get_zones
- When asked to list metrics or about available metrics, call list_metrics immediately
- For metrics queries, use list_metrics to discover what's available if unsure
- Present data in a readable format, not raw JSON
- If a tool call fails, explain the error simply`,
    toolSets: ["home-automation"],
    suggestions: [
      "What music is playing?",
      "Vacuum the kitchen",
      "Show energy usage",
      "List all metrics",
    ],
  },

  researcher: {
    id: "researcher",
    name: "Researcher",
    description: "Search the web and answer questions with sources",
    icon: "🔍",
    color: "#74b4d4",
    systemPrompt: `You are a research assistant that helps find and summarize information from the web.

Guidelines:
- Use the brave_search tool to find information
- Always cite your sources
- Provide concise, well-structured answers
- When unsure, search first before answering
- Summarize findings clearly with bullet points when appropriate`,
    toolSets: ["brave-search"],
    suggestions: [
      "What's the weather in Stockholm?",
      "Latest news about home automation",
      "Compare Zigbee vs Z-Wave",
      "Best smart home devices 2025",
    ],
  },

  analyst: {
    id: "analyst",
    name: "Data Analyst",
    description: "Query spreadsheets and home metrics for insights",
    icon: "📊",
    color: "#74d4a5",
    systemPrompt: `You are a data analyst assistant that helps analyze data from Google Sheets and home metrics.

You have access to tools for:
- **Google Sheets**: Read and list spreadsheets
- **Metrics**: Query VictoriaMetrics for home sensor data

Guidelines:
- Present data in clear, readable tables when possible
- Provide insights and trends, not just raw data
- Use markdown formatting for tables and lists
- When analyzing metrics, suggest useful PromQL queries
- Cross-reference spreadsheet data with metrics when relevant`,
    toolSets: ["home-automation", "google-sheets"],
    suggestions: [
      "List available spreadsheets",
      "Show current energy production",
      "List all metrics",
      "Show battery status",
    ],
  },
};

export function getPersona(id: string): PersonaConfig | undefined {
  return personas[id];
}

export function listPersonas(): PersonaConfig[] {
  return Object.values(personas);
}

// Client-safe display metadata (no system prompts or tool config)
export type PersonaDisplay = Pick<PersonaConfig, "id" | "name" | "description" | "icon" | "color">;

export const PERSONA_DISPLAY: PersonaDisplay[] = Object.values(personas).map(
  ({ id, name, description, icon, color }) => ({ id, name, description, icon, color })
);
