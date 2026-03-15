import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { createSession, listSessions } from "@/lib/db";
import { randomUUID } from "crypto";

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return new Response("Unauthorized", { status: 401 });
  }

  const sessions = listSessions(session.user.email);
  return Response.json(sessions);
}

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return new Response("Unauthorized", { status: 401 });
  }

  const body = await req.json();
  const { persona, model } = body as { persona: string; model?: string };

  if (!persona) {
    return new Response("Missing persona", { status: 400 });
  }

  const id = randomUUID();
  const created = createSession(id, session.user.email, persona, model);
  return Response.json(created, { status: 201 });
}
