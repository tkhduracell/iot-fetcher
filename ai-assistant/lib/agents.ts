import {
  LlmAgent,
  InMemoryRunner,
  BaseTool,
  BaseToolset,
  GOOGLE_SEARCH,
} from "@google/adk";
import { FunctionCallingConfigMode } from "@google/genai";
import type { PersonaConfig } from "./personas";
import { metricsTools, sonosToolset } from "./mcp/home-automation";
import { braveSearchTools } from "./mcp/brave-search";
import { googleSheetsTools } from "./mcp/google-sheets";
import { veganSearchTools } from "./mcp/vegan-search";

// ADK reads GOOGLE_GENAI_API_KEY or GEMINI_API_KEY
if (process.env.GEMINI_API_KEY && !process.env.GOOGLE_GENAI_API_KEY) {
  process.env.GOOGLE_GENAI_API_KEY = process.env.GEMINI_API_KEY;
}

// In test mode, redirect external API calls to the local mock server
if (process.env.NODE_ENV === "test" && process.env.MOCK_SERVER_PORT) {
  const MOCK_BASE = `http://localhost:${process.env.MOCK_SERVER_PORT}`;
  const REDIRECT_HOSTS = [
    "generativelanguage.googleapis.com",
    "api.search.brave.com",
    "sheets.googleapis.com",
    "www.googleapis.com",
    "places.googleapis.com",
    "example.com",
  ];
  const _originalFetch = globalThis.fetch;
  globalThis.fetch = async function testFetchInterceptor(
    input: Parameters<typeof fetch>[0],
    init?: Parameters<typeof fetch>[1]
  ) {
    let url: string | undefined;
    if (typeof input === "string") url = input;
    else if (input instanceof URL) url = input.toString();
    else if (input && typeof input === "object" && "url" in input)
      url = (input as { url: string }).url;

    if (url) {
      for (const host of REDIRECT_HOSTS) {
        if (url.includes(host)) {
          const parsed = new URL(url);
          return _originalFetch(
            `${MOCK_BASE}${parsed.pathname}${parsed.search}`,
            init
          );
        }
      }
      if (url.includes("localhost:5005") || url.includes("127.0.0.1:5005")) {
        const parsed = new URL(url);
        return _originalFetch(
          `${MOCK_BASE}${parsed.pathname}${parsed.search}`,
          init
        );
      }
    }
    return _originalFetch(input, init);
  };
  console.log("[agents] Test mode: API calls redirected to", MOCK_BASE);
}

type CachedRunner = {
  runner: InMemoryRunner;
  persona: string;
  model: string;
  lastUsed: number;
};

const CACHE_TTL = 30 * 60 * 1000; // 30 minutes
const runnerCache = new Map<string, CachedRunner>();

// Cleanup expired runners periodically
const cleanupInterval = setInterval(() => {
  const now = Date.now();
  for (const [key, entry] of runnerCache) {
    if (now - entry.lastUsed > CACHE_TTL) {
      runnerCache.delete(key);
    }
  }
}, 5 * 60 * 1000); // check every 5 min
cleanupInterval.unref();

function getToolsForPersona(persona: PersonaConfig): (BaseTool | BaseToolset)[] {
  const tools: (BaseTool | BaseToolset)[] = [];
  for (const toolSet of persona.toolSets) {
    switch (toolSet) {
      case "home-automation":
        tools.push(...metricsTools, sonosToolset);
        break;
      case "brave-search":
        tools.push(...braveSearchTools);
        break;
      case "google-sheets":
        tools.push(...googleSheetsTools);
        break;
      case "vegan-search":
        tools.push(...veganSearchTools);
        break;
    }
  }
  return tools;
}

export function getRunner(
  sessionId: string,
  persona: PersonaConfig,
  model: string
): InMemoryRunner {
  const cached = runnerCache.get(sessionId);

  // Return cached runner if persona and model match
  if (cached && cached.persona === persona.id && cached.model === model) {
    cached.lastUsed = Date.now();
    return cached.runner;
  }

  const tools = getToolsForPersona(persona);
  // For the vegan researcher, add a Google Search sub-agent that the LLM
  // can delegate to for broad web/maps queries with Gemini grounding.
  // GOOGLE_SEARCH must be the sole tool in its agent (ADK limitation).
  const subAgents =
    persona.id === "vegan-researcher" && GOOGLE_SEARCH
      ? [
          new LlmAgent({
            name: "google_search_agent",
            model,
            description:
              "Delegates to Google Search for broad web queries, news, and real-time information. Use when brave_search is insufficient or you need Google-grounded results.",
            instruction:
              "You are a Google Search assistant. Answer the user's query using Google Search. Return the results with sources.",
            tools: [GOOGLE_SEARCH],
          }),
        ]
      : undefined;

  const agentConfig: ConstructorParameters<typeof LlmAgent>[0] = {
    name: persona.id.replace(/-/g, "_"),
    model,
    instruction: persona.systemPrompt,
    tools,
    subAgents,
  };
  if (tools.length > 0) {
    // Force the model to call tools rather than respond with text-only greetings.
    // Gemini preview models often ignore tools on first turn without this.
    agentConfig.generateContentConfig = {
      toolConfig: {
        functionCallingConfig: { mode: FunctionCallingConfigMode.ANY },
      },
    };
  }
  const agent = new LlmAgent(agentConfig);

  const runner = new InMemoryRunner({
    agent,
    appName: "home-automation-chat",
  });

  runnerCache.set(sessionId, {
    runner,
    persona: persona.id,
    model,
    lastUsed: Date.now(),
  });

  return runner;
}

export function evictRunner(sessionId: string): void {
  runnerCache.delete(sessionId);
}
