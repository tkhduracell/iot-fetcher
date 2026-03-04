import { FunctionTool } from "@google/adk";
import { z } from "zod";

function getServiceAccountCredentials() {
  const key = process.env.GOOGLE_SHEETS_SERVICE_ACCOUNT_KEY;
  if (!key) return null;
  try {
    return JSON.parse(key);
  } catch {
    return null;
  }
}

async function getAccessToken(): Promise<string | null> {
  const creds = getServiceAccountCredentials();
  if (!creds) return null;

  // Use googleapis for service account auth
  try {
    const { google } = await import("googleapis");
    const auth = new google.auth.GoogleAuth({
      credentials: creds,
      scopes: [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
      ],
    });
    const client = await auth.getClient();
    const token = await client.getAccessToken();
    return token?.token ?? null;
  } catch (err) {
    console.error("Google Sheets auth error:", err);
    return null;
  }
}

export const googleSheetsTools = [
  new FunctionTool({
    name: "sheets_list",
    description:
      "List Google Sheets spreadsheets accessible to the service account. Returns spreadsheet IDs and titles.",
    parameters: z.object({}),
    execute: async () => {
      const token = await getAccessToken();
      if (!token) {
        return { error: "GOOGLE_SHEETS_SERVICE_ACCOUNT_KEY not configured" };
      }

      try {
        const res = await fetch(
          "https://www.googleapis.com/drive/v3/files?q=mimeType='application/vnd.google-apps.spreadsheet'&fields=files(id,name,modifiedTime)",
          { headers: { Authorization: `Bearer ${token}` } }
        );

        if (!res.ok) {
          return { error: `HTTP ${res.status}: ${await res.text()}` };
        }

        const data = await res.json();
        return {
          spreadsheets: (data.files ?? []).map(
            (f: { id: string; name: string; modifiedTime: string }) => ({
              id: f.id,
              name: f.name,
              modifiedTime: f.modifiedTime,
            })
          ),
        };
      } catch (err) {
        return { error: String(err) };
      }
    },
  }),

  new FunctionTool({
    name: "sheets_read",
    description:
      "Read data from a Google Sheets spreadsheet. Returns cell values from the specified range.",
    parameters: z.object({
      spreadsheetId: z.string().describe("The spreadsheet ID"),
      range: z
        .string()
        .optional()
        .describe("A1 notation range, e.g. 'Sheet1!A1:D10'. Defaults to first sheet."),
    }),
    execute: async ({ spreadsheetId, range }) => {
      const token = await getAccessToken();
      if (!token) {
        return { error: "GOOGLE_SHEETS_SERVICE_ACCOUNT_KEY not configured" };
      }

      const rangeParam = range ? `/${encodeURIComponent(range)}` : "/Sheet1";
      const url = `https://sheets.googleapis.com/v4/spreadsheets/${spreadsheetId}/values${rangeParam}`;

      try {
        const res = await fetch(url, {
          headers: { Authorization: `Bearer ${token}` },
        });

        if (!res.ok) {
          return { error: `HTTP ${res.status}: ${await res.text()}` };
        }

        const data = await res.json();
        return {
          range: data.range,
          values: data.values,
          rows: data.values?.length ?? 0,
        };
      } catch (err) {
        return { error: String(err) };
      }
    },
  }),
];
