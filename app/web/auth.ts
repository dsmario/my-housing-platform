// Sign-in with Cognito through Auth.js. Routes live under /auth, because the ALB
// sends /api/* to FastAPI.
//
// Environment: AUTH_SECRET, AUTH_URL, AUTH_COGNITO_ID, AUTH_COGNITO_SECRET,
// AUTH_COGNITO_ISSUER, and AUTH_COGNITO_DOMAIN (the login domain, for token refresh).
import NextAuth from "next-auth";
import type { JWT } from "next-auth/jwt";
import Cognito from "next-auth/providers/cognito";

async function refreshAccessToken(token: JWT): Promise<JWT> {
  try {
    const basic = Buffer.from(
      `${process.env.AUTH_COGNITO_ID}:${process.env.AUTH_COGNITO_SECRET}`,
    ).toString("base64");
    const res = await fetch(`${process.env.AUTH_COGNITO_DOMAIN}/oauth2/token`, {
      method: "POST",
      headers: {
        Authorization: `Basic ${basic}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        refresh_token: token.refreshToken ?? "",
      }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
    return {
      ...token,
      accessToken: body.access_token,
      expiresAt: Math.floor(Date.now() / 1000) + body.expires_in,
      error: undefined,
    };
  } catch {
    return { ...token, accessToken: undefined, error: "RefreshFailed" };
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  basePath: "/auth",
  trustHost: true,
  providers: [Cognito],
  session: { strategy: "jwt", maxAge: 8 * 60 * 60 },
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        return {
          ...token,
          accessToken: account.access_token,
          refreshToken: account.refresh_token,
          expiresAt: account.expires_at,
        };
      }
      if (token.expiresAt && Date.now() < (token.expiresAt - 60) * 1000) {
        return token;
      }
      return refreshAccessToken(token);
    },
    async session({ session, token }) {
      // The access token goes to server code calling the API. Follow-up: keep it
      // out of the browser-readable session endpoint.
      session.accessToken = token.accessToken;
      session.error = token.error;
      return session;
    },
  },
});
