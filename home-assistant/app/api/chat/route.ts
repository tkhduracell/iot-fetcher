import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { getSession as getDbSession, getMessages, addMessage } from "@/lib/db";
import { getRunner } from "@/lib/agents";
import { getPersona } from "@/lib/personas";
import { createEvent } from "@google/adk";

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return new Response("Unauthorized", { status: 401 });
  }

  const body = await req.json();
  const { message, sessionId } = body as {
    message: string;
    sessionId: string;
  };

  if (!message || typeof message !== "string") {
    return new Response("Missing message", { status: 400 });
  }
  if (!sessionId) {
    return new Response("Missing sessionId", { status: 400 });
  }

  // Load session from DB
  const dbSession = getDbSession(sessionId);
  if (!dbSession || dbSession.user_email !== session.user.email) {
    return new Response("Session not found", { status: 404 });
  }

  const persona = getPersona(dbSession.persona);
  if (!persona) {
    return new Response("Unknown persona", { status: 400 });
  }

  const userId = session.user.email;
  const runner = getRunner(sessionId, persona, dbSession.model);

  // Get or create ADK session
  let adkSession = await runner.sessionService
    .getSession({
      appName: "home-automation-chat",
      userId,
      sessionId,
    })
    .catch(() => null);

  if (!adkSession) {
    adkSession = await runner.sessionService.createSession({
      appName: "home-automation-chat",
      userId,
      sessionId,
    });

    // Replay existing messages into ADK session if resuming from DB
    const existingMessages = getMessages(sessionId);
    if (existingMessages.length > 0) {
      for (const msg of existingMessages) {
        adkSession.events.push(createEvent({
          author: msg.role === "user" ? "user" : persona.id.replace(/-/g, "_"),
          content: {
            role: msg.role === "user" ? "user" : "model",
            parts: [{ text: msg.content || "(tool interaction)" }],
          },
        }));
      }
    }
  }

  // Save user message to DB
  addMessage(sessionId, "user", message);

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      function send(data: Record<string, unknown>) {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(data)}\n\n`)
        );
      }

      let fullText = "";
      const toolCalls: { id: string; name: string; input: unknown; result?: string }[] = [];

      try {
        send({ type: "session_id", sessionId });

        for await (const event of runner.runAsync({
          userId,
          sessionId,
          newMessage: { role: "user", parts: [{ text: message }] },
        })) {
          if (!event.content?.parts) continue;

          const agentName = persona.id.replace(/-/g, "_");

          for (const part of event.content.parts) {
            if ("text" in part && part.text) {
              if (event.author === agentName) {
                send({ type: "text", text: part.text });
                fullText += part.text;
              }
            }

            if ("functionCall" in part && part.functionCall) {
              const tc = {
                id: (part.functionCall.id ?? part.functionCall.name) as string,
                name: part.functionCall.name as string,
                input: part.functionCall.args as unknown,
              };
              toolCalls.push(tc);
              send({
                type: "tool_use",
                id: tc.id,
                name: tc.name,
                input: tc.input,
              });
            }

            if ("functionResponse" in part && part.functionResponse) {
              const id = part.functionResponse.id ?? part.functionResponse.name;
              const result = JSON.stringify(part.functionResponse.response);
              const tc = toolCalls.find((t) => t.id === id);
              if (tc) tc.result = result;
              send({ type: "tool_result", id, result });
            }
          }
        }

        // Save assistant message to DB
        addMessage(
          sessionId,
          "assistant",
          fullText,
          toolCalls.length > 0 ? toolCalls : undefined
        );

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
