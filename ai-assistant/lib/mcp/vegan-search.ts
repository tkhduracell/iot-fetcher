import { FunctionTool } from "@google/adk";
import { z } from "zod";
import { PDFParse } from "pdf-parse";

const GOOGLE_MAPS_API_KEY = process.env.GOOGLE_MAPS_API_KEY ?? "";

const MAX_PDF_SIZE = 10 * 1024 * 1024; // 10 MB

/**
 * Validate a URL for SSRF protection.
 * Only allows http/https, blocks private/internal IPs, and restricts ports.
 */
function validateUrl(raw: string): { ok: true } | { ok: false; error: string } {
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return { ok: false, error: "Invalid URL" };
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { ok: false, error: `Blocked protocol: ${parsed.protocol}` };
  }

  // Block non-standard ports
  if (parsed.port && parsed.port !== "80" && parsed.port !== "443") {
    return { ok: false, error: `Blocked port: ${parsed.port}` };
  }

  const hostname = parsed.hostname.toLowerCase();

  // Block localhost variants
  if (
    hostname === "localhost" ||
    hostname === "[::1]" ||
    hostname === "0.0.0.0"
  ) {
    return { ok: false, error: "Blocked: localhost" };
  }

  // Block private/internal IP ranges
  const ipMatch = hostname.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (ipMatch) {
    const [, a, b] = ipMatch.map(Number);
    if (
      a === 127 || // 127.0.0.0/8
      a === 10 || // 10.0.0.0/8
      (a === 172 && b >= 16 && b <= 31) || // 172.16.0.0/12
      (a === 192 && b === 168) || // 192.168.0.0/16
      (a === 169 && b === 254) || // 169.254.0.0/16 (link-local)
      a === 0 // 0.0.0.0/8
    ) {
      return { ok: false, error: "Blocked: private/internal IP" };
    }
  }

  return { ok: true };
}

export const veganSearchTools = [
  new FunctionTool({
    name: "google_places_search",
    description:
      "Search Google Maps/Places for restaurants by name or type in a location. Returns ratings, reviews, websites, opening hours, and photo names.",
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
          'Location to include in the search, e.g. "Södermalm, Stockholm" or "Maioris Décima, Mallorca"'
        ),
    }),
    execute: async ({ query, location }) => {
      if (!GOOGLE_MAPS_API_KEY) {
        return { error: "GOOGLE_MAPS_API_KEY not configured" };
      }

      const textQuery = location ? `${query} in ${location}` : query;
      const body: Record<string, unknown> = { textQuery };

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
              photos: (p.photos ?? [])
                .slice(0, 3)
                .map((ph) => ph.name ?? ""),
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
      maxLength: z
        .number()
        .optional()
        .describe(
          "Maximum characters to return (default 10000)"
        ),
    }),
    execute: async ({ url, maxLength }) => {
      const validation = validateUrl(url);
      if (!validation.ok) {
        return { error: validation.error };
      }

      const maxLen = maxLength ?? 10_000;

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
      maxLength: z
        .number()
        .optional()
        .describe(
          "Maximum characters to return (default 15000)"
        ),
    }),
    execute: async ({ url, maxLength }) => {
      const validation = validateUrl(url);
      if (!validation.ok) {
        return { error: validation.error };
      }

      const maxLen = maxLength ?? 15_000;

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

        // Check Content-Length before downloading
        const contentLength = res.headers.get("content-length");
        if (contentLength && parseInt(contentLength, 10) > MAX_PDF_SIZE) {
          return { error: `PDF too large: ${contentLength} bytes (max ${MAX_PDF_SIZE})` };
        }

        const arrayBuffer = await res.arrayBuffer();
        if (arrayBuffer.byteLength > MAX_PDF_SIZE) {
          return { error: `PDF too large: ${arrayBuffer.byteLength} bytes (max ${MAX_PDF_SIZE})` };
        }

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
