import { LlmAgent, InMemoryRunner } from "@google/adk";
import type { PersonaConfig } from "./personas";
import { homeAutomationTools } from "./mcp/home-automation";
import { braveSearchTools } from "./mcp/brave-search";
import { googleSheetsTools } from "./mcp/google-sheets";

// ADK reads GOOGLE_GENAI_API_KEY or GEMINI_API_KEY
if (process.env.GEMINI_API_KEY && !process.env.GOOGLE_GENAI_API_KEY) {
  process.env.GOOGLE_GENAI_API_KEY = process.env.GEMINI_API_KEY;
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
setInterval(() => {
  const now = Date.now();
  for (const [key, entry] of runnerCache) {
    if (now - entry.lastUsed > CACHE_TTL) {
      runnerCache.delete(key);
    }
  }
}, 5 * 60 * 1000); // check every 5 min

function getToolsForPersona(persona: PersonaConfig) {
  const tools = [];
  for (const toolSet of persona.toolSets) {
    switch (toolSet) {
      case "home-automation":
        tools.push(...homeAutomationTools);
        break;
      case "brave-search":
        tools.push(...braveSearchTools);
        break;
      case "google-sheets":
        tools.push(...googleSheetsTools);
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

  const agent = new LlmAgent({
    name: persona.id.replace(/-/g, "_"),
    model,
    instruction: persona.systemPrompt,
    tools: getToolsForPersona(persona),
  });

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
