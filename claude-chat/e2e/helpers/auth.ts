import { encode } from "next-auth/jwt";
import type { BrowserContext } from "@playwright/test";

/** Must match the NEXTAUTH_SECRET used when starting the test server */
const TEST_SECRET =
  process.env.NEXTAUTH_SECRET ?? "test-secret-for-playwright";

export const TEST_USER = {
  name: "Test User",
  email: "test@example.com",
  picture: "https://example.com/avatar.png",
};

/**
 * Generate a NextAuth v4 JWT session cookie value.
 */
export async function getAuthCookie(
  user: { name: string; email: string; sub?: string } = TEST_USER
): Promise<{ name: string; value: string }> {
  const token = await encode({
    token: {
      name: user.name,
      email: user.email,
      picture: (user as Record<string, string>).picture,
      sub: user.sub ?? user.email,
    },
    secret: TEST_SECRET,
    maxAge: 30 * 24 * 60 * 60, // 30 days
  });

  return {
    name: "next-auth.session-token",
    value: token,
  };
}

/**
 * Set the NextAuth session cookie on a Playwright BrowserContext,
 * making subsequent page navigations appear authenticated.
 *
 * Call this before navigating to any page.
 */
export async function authenticateContext(
  context: BrowserContext,
  baseURL: string,
  user?: { name: string; email: string; sub?: string }
): Promise<void> {
  const cookie = await getAuthCookie(user);
  const url = new URL(baseURL);

  await context.addCookies([
    {
      ...cookie,
      domain: url.hostname,
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
      secure: false,
    },
  ]);
}
