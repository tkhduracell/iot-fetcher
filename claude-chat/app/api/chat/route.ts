import { LlmAgent, InMemoryRunner } from "@google/adk";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { homeAutomationTools } from "@/lib/mcp/home-automation";

// ADK reads GOOGLE_GENAI_API_KEY or GEMINI_API_KEY
if (process.env.GEMINI_API_KEY && !process.env.GOOGLE_GENAI_API_KEY) {
  process.env.GOOGLE_GENAI_API_KEY = process.env.GEMINI_API_KEY;
}

const SYSTEM_PROMPT = `You are a helpful home automation assistant for Filip's smart home.

You have access to tools for:
- **Sonos**: Control music playback, volume, favourites across rooms
- **Metrics**: Query VictoriaMetrics for home sensor data (energy, temperature, devices)

Guidelines:
- Be concise and helpful
- When asked about music, check what's playing first with sonos_get_zones
- When asked to list metrics or about available metrics, call list_metrics immediately
- For metrics queries, use list_metrics to discover what's available if unsure
- Present data in a readable format, not raw JSON
- If a tool call fails, explain the error simply`;

const agent = new LlmAgent({
  name: "home_assistant",
  model: "gemini-3-flash-preview",
  instruction: SYSTEM_PROMPT,
  tools: homeAutomationTools,
});

const runner = new InMemoryRunner({
  agent,
  appName: "home-automation-chat",
});

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return new Response("Unauthorized", { status: 401 });
  }

  const body = await req.json();
  const { message, sessionId } = body as {
    message: string;
    sessionId?: string;
  };

  if (!message || typeof message !== "string") {
    return new Response("Missing message", { status: 400 });
  }

  // Get or create a session for this user
  const userId = session.user?.email ?? "anonymous";
  let adkSession = sessionId
    ? await runner.sessionService.getSession({
        appName: "home-automation-chat",
        userId,
        sessionId,
      })
    : null;
  if (!adkSession) {
    adkSession = await runner.sessionService.createSession({
      appName: "home-automation-chat",
      userId,
    });
  }

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      function send(data: Record<string, unknown>) {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(data)}\n\n`)
        );
      }

      try {
        const userMessage = {
          parts: [{ text: message }],
        };

        // Send session ID to client
        send({ type: "session_id", sessionId: adkSession.id });

        for await (const event of runner.runAsync({
          userId,
          sessionId: adkSession.id,
          newMessage: userMessage,
        })) {
          if (!event.content?.parts) continue;

          for (const part of event.content.parts) {
            // Text response
            if ("text" in part && part.text) {
              if (event.author === "home_assistant") {
                send({ type: "text", text: part.text });
              }
            }

            // Function call (tool use)
            if ("functionCall" in part && part.functionCall) {
              send({
                type: "tool_use",
                id: part.functionCall.id ?? part.functionCall.name,
                name: part.functionCall.name,
                input: part.functionCall.args,
              });
            }

            // Function response (tool result)
            if ("functionResponse" in part && part.functionResponse) {
              send({
                type: "tool_result",
                id: part.functionResponse.id ?? part.functionResponse.name,
                result: JSON.stringify(part.functionResponse.response),
              });
            }
          }
        }

        send({ type: "done" });
      } catch (err) {
        console.error("Chat error:", err);
        send({
          type: "error",
          error: err instanceof Error ? err.message : String(err),
        });
      } finally {
        controller.enqueue(encoder.encode("data: [DONE]\n\n"));
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
