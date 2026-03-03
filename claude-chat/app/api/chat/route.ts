import { query, type SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { getServerSession } from "next-auth";
import { authOptions } from "../auth/[...nextauth]/route";
import { createHomeAutomationServer } from "@/lib/mcp/home-automation";

const SYSTEM_PROMPT = `You are a helpful home automation assistant for Filip's smart home.

You have access to tools for:
- **Sonos**: Control music playback, volume, favourites across rooms
- **Roborock**: Start vacuum cleaning in specific zones or full-house
- **Metrics**: Query VictoriaMetrics for home sensor data (energy, temperature, devices)

Guidelines:
- Be concise and helpful
- When asked about music, check what's playing first with sonos_get_zones
- When asked to vacuum, list zones first if the user hasn't specified one
- For metrics, use list_metrics to discover what's available if unsure
- Present data in a readable format, not raw JSON
- If a tool call fails, explain the error simply`;

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

  const homeServer = createHomeAutomationServer();

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      function send(data: Record<string, unknown>) {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(data)}\n\n`)
        );
      }

      try {
        const options: Record<string, unknown> = {
          model: "claude-sonnet-4-6",
          systemPrompt: SYSTEM_PROMPT,
          mcpServers: {
            "home-automation": homeServer,
          },
          allowedTools: ["mcp__home-automation__*"],
          permissionMode: "bypassPermissions",
          allowDangerouslySkipPermissions: true,
          maxTurns: 15,
        };

        if (sessionId) {
          options.resume = sessionId;
        }

        const q = query({ prompt: message, options });

        for await (const msg of q as AsyncIterable<SDKMessage>) {
          // Capture session ID from init
          if (
            msg.type === "system" &&
            "subtype" in msg &&
            msg.subtype === "init"
          ) {
            send({ type: "session_id", sessionId: msg.session_id });
          }

          // Stream assistant text
          if (msg.type === "assistant" && "message" in msg) {
            const content = (
              msg as { message: { content: Array<{ type: string; text?: string; name?: string; id?: string; input?: unknown }> } }
            ).message.content;

            for (const block of content) {
              if (block.type === "text" && block.text) {
                send({ type: "text", text: block.text });
              }
              if (block.type === "tool_use") {
                send({
                  type: "tool_use",
                  id: block.id,
                  name: block.name,
                  input: block.input,
                });
              }
            }
          }

          // Tool results
          if (msg.type === "tool_result" && "content" in msg) {
            const toolMsg = msg as { tool_use_id?: string; content?: Array<{ type: string; text?: string }> };
            const resultText = toolMsg.content
              ?.filter((b) => b.type === "text")
              .map((b) => b.text)
              .join("");
            send({
              type: "tool_result",
              id: toolMsg.tool_use_id,
              result: resultText,
            });
          }

          // Final result
          if (msg.type === "result") {
            const resultMsg = msg as { subtype?: string; result?: string; session_id?: string };
            if (resultMsg.subtype === "success" && resultMsg.result) {
              send({ type: "text", text: resultMsg.result });
            }
            send({
              type: "session_id",
              sessionId: resultMsg.session_id ?? msg.session_id,
            });
          }
        }

        send({ type: "done" });
      } catch (err) {
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
