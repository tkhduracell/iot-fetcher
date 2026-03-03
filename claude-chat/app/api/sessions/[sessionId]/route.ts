import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { getSession, deleteSession, updateSession } from "@/lib/db";

type Params = { params: Promise<{ sessionId: string }> };

export async function GET(_req: Request, { params }: Params) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return new Response("Unauthorized", { status: 401 });
  }

  const { sessionId } = await params;
  const chatSession = getSession(sessionId);
  if (!chatSession || chatSession.user_email !== session.user.email) {
    return new Response("Not found", { status: 404 });
  }

  return Response.json(chatSession);
}

export async function DELETE(_req: Request, { params }: Params) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return new Response("Unauthorized", { status: 401 });
  }

  const { sessionId } = await params;
  const chatSession = getSession(sessionId);
  if (!chatSession || chatSession.user_email !== session.user.email) {
    return new Response("Not found", { status: 404 });
  }

  deleteSession(sessionId);
  return new Response(null, { status: 204 });
}

export async function PATCH(req: Request, { params }: Params) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return new Response("Unauthorized", { status: 401 });
  }

  const { sessionId } = await params;
  const chatSession = getSession(sessionId);
  if (!chatSession || chatSession.user_email !== session.user.email) {
    return new Response("Not found", { status: 404 });
  }

  const body = await req.json();
  const { title, model } = body as { title?: string; model?: string };
  const updated = updateSession(sessionId, { title, model });
  return Response.json(updated);
}
