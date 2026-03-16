import { FunctionTool } from "@google/adk";
import { z } from "zod";
import { PDFParse } from "pdf-parse";

const GOOGLE_MAPS_API_KEY = process.env.GOOGLE_MAPS_API_KEY ?? "";

export const veganSearchTools = [
  new FunctionTool({
    name: "google_places_search",
    description:
      "Search Google Maps/Places for restaurants by name or type in a location. Returns ratings, reviews, websites, opening hours, and photo references.",
    parameters: z.object({
      query: z
        .string()
        .describe(
          'Search query, e.g. "vegan restaurants" or a specific restaurant name'
        ),
      location: z
        .string()
        .optional()
        .describe(
          'Location bias, e.g. "Södermalm, Stockholm" or "Maioris Décima, Mallorca"'
        ),
      radius: z
        .number()
        .optional()
        .describe("Search radius in meters (default 5000)"),
    }),
    execute: async ({ query, location, radius }) => {
      if (!GOOGLE_MAPS_API_KEY) {
        return { error: "GOOGLE_MAPS_API_KEY not configured" };
      }

      const textQuery = location ? `${query} in ${location}` : query;
      const body: Record<string, unknown> = { textQuery };
      if (location) {
        body.locationBias = {
          circle: {
            radius: radius ?? 5000,
          },
        };
      }

      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15_000);

        const res = await fetch(
          "https://places.googleapis.com/v1/places:searchText",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
              "X-Goog-FieldMask":
                "places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.websiteUri,places.googleMapsUri,places.reviews,places.regularOpeningHours,places.photos",
            },
            body: JSON.stringify(body),
            signal: controller.signal,
          }
        );
        clearTimeout(timeout);

        if (!res.ok) {
          const text = await res.text().catch(() => "");
          return { error: `HTTP ${res.status}: ${text}` };
        }

        const data = await res.json();
        const places = (data.places ?? [])
          .slice(0, 10)
          .map(
            (p: {
              displayName?: { text?: string };
              formattedAddress?: string;
              rating?: number;
              userRatingCount?: number;
              websiteUri?: string;
              googleMapsUri?: string;
              reviews?: { text?: { text?: string } }[];
              regularOpeningHours?: { weekdayDescriptions?: string[] };
              photos?: { name?: string }[];
            }) => ({
              name: p.displayName?.text ?? "Unknown",
              address: p.formattedAddress ?? "",
              rating: p.rating ?? null,
              reviewCount: p.userRatingCount ?? 0,
              website: p.websiteUri ?? null,
              mapsUrl: p.googleMapsUri ?? null,
              reviews: (p.reviews ?? [])
                .slice(0, 3)
                .map((r) => r.text?.text ?? ""),
              openingHours: p.regularOpeningHours?.weekdayDescriptions ?? [],
              photoCount: (p.photos ?? []).length,
            })
          );

        return { places, query: textQuery };
      } catch (err) {
        return { error: String(err) };
      }
    },
  }),

  new FunctionTool({
    name: "fetch_webpage",
    description:
      "Fetch a URL and extract readable text content. Use this to read restaurant websites and find menu pages.",
    parameters: z.object({
      url: z.string().url().describe("The URL to fetch"),
      max_length: z
        .number()
        .optional()
        .describe(
          "Maximum characters to return (default 10000)"
        ),
    }),
    execute: async ({ url, max_length }) => {
      const maxLen = max_length ?? 10_000;

      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15_000);

        const res = await fetch(url, {
          headers: {
            "User-Agent":
              "Mozilla/5.0 (compatible; VeganResearcher/1.0)",
            Accept: "text/html,application/xhtml+xml,*/*",
          },
          signal: controller.signal,
          redirect: "follow",
        });
        clearTimeout(timeout);

        if (!res.ok) {
          return { error: `HTTP ${res.status}` };
        }

        const html = await res.text();

        // Extract title
        const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
        const title = titleMatch
          ? titleMatch[1].replace(/\s+/g, " ").trim()
          : "";

        // Strip script and style blocks, then remove tags
        const content = html
          .replace(/<script[\s\S]*?<\/script>/gi, "")
          .replace(/<style[\s\S]*?<\/style>/gi, "")
          .replace(/<[^>]+>/g, " ")
          .replace(/&nbsp;/gi, " ")
          .replace(/&amp;/gi, "&")
          .replace(/&lt;/gi, "<")
          .replace(/&gt;/gi, ">")
          .replace(/&#\d+;/g, "")
          .replace(/\s+/g, " ")
          .trim()
          .slice(0, maxLen);

        return { url, title, content };
      } catch (err) {
        return { error: String(err) };
      }
    },
  }),

  new FunctionTool({
    name: "fetch_pdf",
    description:
      "Download a PDF from a URL and extract its text content. Use this for restaurant PDF menus.",
    parameters: z.object({
      url: z.string().url().describe("The PDF URL to fetch"),
      max_length: z
        .number()
        .optional()
        .describe(
          "Maximum characters to return (default 15000)"
        ),
    }),
    execute: async ({ url, max_length }) => {
      const maxLen = max_length ?? 15_000;

      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 20_000);

        const res = await fetch(url, {
          headers: {
            "User-Agent":
              "Mozilla/5.0 (compatible; VeganResearcher/1.0)",
            Accept: "application/pdf,*/*",
          },
          signal: controller.signal,
          redirect: "follow",
        });
        clearTimeout(timeout);

        if (!res.ok) {
          return { error: `HTTP ${res.status}` };
        }

        const contentType = res.headers.get("content-type") ?? "";
        if (
          !contentType.includes("pdf") &&
          !url.toLowerCase().endsWith(".pdf")
        ) {
          return {
            error: `Not a PDF. Content-Type: ${contentType}`,
          };
        }

        const arrayBuffer = await res.arrayBuffer();
        const pdf = new PDFParse({ data: new Uint8Array(arrayBuffer) });
        const textResult = await pdf.getText();
        await pdf.destroy();

        const content = textResult.text.slice(0, maxLen);

        return { url, pages: textResult.total, content };
      } catch (err) {
        return { error: String(err) };
      }
    },
  }),
];
