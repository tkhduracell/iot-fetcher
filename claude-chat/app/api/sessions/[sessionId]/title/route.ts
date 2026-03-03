import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { getSession, getMessages, updateSession } from "@/lib/db";
import { GoogleGenAI } from "@google/genai";

type Params = { params: Promise<{ sessionId: string }> };

export async function POST(_req: Request, { params }: Params) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return new Response("Unauthorized", { status: 401 });
  }

  const { sessionId } = await params;
  const chatSession = getSession(sessionId);
  if (!chatSession || chatSession.user_email !== session.user.email) {
    return new Response("Not found", { status: 404 });
  }

  // Already has a title
  if (chatSession.title) {
    return Response.json({ title: chatSession.title });
  }

  const messages = getMessages(sessionId);
  if (messages.length === 0) {
    return Response.json({ title: "" });
  }

  // Use first few messages to generate a title
  const context = messages
    .slice(0, 4)
    .map((m) => `${m.role}: ${m.content.slice(0, 200)}`)
    .join("\n");

  try {
    const apiKey = process.env.GOOGLE_GENAI_API_KEY ?? process.env.GEMINI_API_KEY;
    if (!apiKey) {
      return Response.json({ title: messages[0]?.content.slice(0, 50) ?? "New chat" });
    }

    const genai = new GoogleGenAI({ apiKey });
    const result = await genai.models.generateContent({
      model: "gemini-2.0-flash",
      contents: `Generate a very short title (3-6 words, no quotes) for this conversation:\n\n${context}`,
    });

    const title = result.text?.trim().replace(/^["']|["']$/g, "") ?? "New chat";
    updateSession(sessionId, { title });
    return Response.json({ title });
  } catch (err) {
    console.error("Title generation error:", err);
    const fallback = messages[0]?.content.slice(0, 50) ?? "New chat";
    updateSession(sessionId, { title: fallback });
    return Response.json({ title: fallback });
  }
}
