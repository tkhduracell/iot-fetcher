import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { getSession, getMessages } from "@/lib/db";

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

  const messages = getMessages(sessionId);
  return Response.json(messages);
}
