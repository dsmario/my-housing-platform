import type { NextRequest } from "next/server";
import { handlers } from "../../../auth";

export const { POST } = handlers;

// /auth/session can be read by any script on the page, so the access token is
// removed before the session leaves the server. Server code gets the token
// from auth(), which doesn't go through this route.
export async function GET(req: NextRequest) {
  const res = await handlers.GET(req);
  if (!req.nextUrl.pathname.endsWith("/session") || !res.ok) return res;

  const session = await res.json();
  if (session) delete session.accessToken;
  const headers = new Headers(res.headers); // keeps any refreshed session cookie
  headers.delete("content-length");
  return Response.json(session, { status: res.status, headers });
}
