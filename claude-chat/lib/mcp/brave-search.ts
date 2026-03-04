import { FunctionTool } from "@google/adk";
import { z } from "zod";

const BRAVE_API_KEY = process.env.BRAVE_API_KEY ?? "";

export const braveSearchTools = [
  new FunctionTool({
    name: "brave_search",
    description:
      "Search the web using Brave Search API. Returns relevant web results with titles, descriptions, and URLs.",
    parameters: z.object({
      query: z.string().describe("The search query"),
      count: z
        .number()
        .min(1)
        .max(20)
        .optional()
        .describe("Number of results to return (default 5)"),
    }),
    execute: async ({ query, count }) => {
      if (!BRAVE_API_KEY) {
        return { error: "BRAVE_API_KEY not configured" };
      }

      const numResults = count ?? 5;
      const url = `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=${numResults}`;

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15_000);

      try {
        const res = await fetch(url, {
          headers: {
            Accept: "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": BRAVE_API_KEY,
          },
          signal: controller.signal,
        });

        if (!res.ok) {
          const text = await res.text().catch(() => "");
          return { error: `HTTP ${res.status}: ${text}` };
        }

        const data = await res.json();
        const results = (data.web?.results ?? []).map(
          (r: { title: string; url: string; description: string }) => ({
            title: r.title,
            url: r.url,
            description: r.description,
          })
        );

        return { results, query };
      } catch (err) {
        return { error: String(err) };
      } finally {
        clearTimeout(timeout);
      }
    },
  }),
];
