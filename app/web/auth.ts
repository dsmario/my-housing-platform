// Sign-in with Cognito through Auth.js. Routes live under /auth, because the ALB
// sends /api/* to FastAPI.
//
// Two providers, one per Cognito pool:
//   cognito        users pool (youth, supporters). AUTH_SECRET, AUTH_URL,
//                  AUTH_COGNITO_ID, AUTH_COGNITO_SECRET, AUTH_COGNITO_ISSUER, and
//                  AUTH_COGNITO_DOMAIN (the login domain, for token refresh).
//   cognito-staff  staff pool, for /admin. The same four AUTH_STAFF_COGNITO_*
//                  values. Left out when AUTH_STAFF_COGNITO_ID is empty.
import NextAuth from "next-auth";
import type { JWT } from "next-auth/jwt";
import type { Provider } from "next-auth/providers";
import Cognito from "next-auth/providers/cognito";

type PoolConfig = { clientId: string; clientSecret: string; domain: string };

function poolConfig(provider: string | undefined): PoolConfig {
  if (provider === "cognito-staff") {
    return {
      clientId: process.env.AUTH_STAFF_COGNITO_ID ?? "",
      clientSecret: process.env.AUTH_STAFF_COGNITO_SECRET ?? "",
      domain: process.env.AUTH_STAFF_COGNITO_DOMAIN ?? "",
    };
  }
  return {
    clientId: process.env.AUTH_COGNITO_ID ?? "",
    clientSecret: process.env.AUTH_COGNITO_SECRET ?? "",
    domain: process.env.AUTH_COGNITO_DOMAIN ?? "",
  };
}

export const staffSignInEnabled = Boolean(process.env.AUTH_STAFF_COGNITO_ID);

async function refreshAccessToken(token: JWT): Promise<JWT> {
  try {
    const pool = poolConfig(token.provider);
    const basic = Buffer.from(`${pool.clientId}:${pool.clientSecret}`).toString("base64");
    const res = await fetch(`${pool.domain}/oauth2/token`, {
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

const providers: Provider[] = [Cognito];
if (staffSignInEnabled) {
  providers.push(
    Cognito({
      id: "cognito-staff",
      name: "Staff",
      clientId: process.env.AUTH_STAFF_COGNITO_ID,
      clientSecret: process.env.AUTH_STAFF_COGNITO_SECRET,
      issuer: process.env.AUTH_STAFF_COGNITO_ISSUER,
    }),
  );
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  basePath: "/auth",
  trustHost: true,
  providers,
  session: { strategy: "jwt", maxAge: 8 * 60 * 60 },
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        return {
          ...token,
          provider: account.provider,
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
      session.provider = token.provider;
      session.error = token.error;
      return session;
    },
  },
});
