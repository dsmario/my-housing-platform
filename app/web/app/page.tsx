import Link from "next/link";
import { redirect } from "next/navigation";
import { auth, signIn, signOut } from "../auth";

// Ends the Cognito login-page session too; otherwise the next "Sign in" goes
// straight through without asking. The return address must be one of the
// client's registered logout URLs.
function cognitoLogoutUrl(): string {
  const appOrigin = new URL(process.env.AUTH_URL ?? "").origin;
  const url = new URL("/logout", process.env.AUTH_COGNITO_DOMAIN);
  url.searchParams.set("client_id", process.env.AUTH_COGNITO_ID ?? "");
  url.searchParams.set("logout_uri", appOrigin);
  return url.toString();
}

// Rendered on the server for every request, so the text is always fresh from the API.
export const dynamic = "force-dynamic";

type Message = {
  text: string;
  db_time: string;
  db_version: string;
};

type Me = {
  username: string;
  groups: string[];
  pool: string;
};

async function getMessage(): Promise<Message> {
  const res = await fetch(`${process.env.API_URL}/api/message`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API returned ${res.status}`);
  }
  return res.json();
}

async function getMe(accessToken: string): Promise<Me> {
  const res = await fetch(`${process.env.API_URL}/api/me`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    throw new Error(`API returned ${res.status}`);
  }
  return res.json();
}

export default async function Home() {
  let message: Message | null = null;
  let error: string | null = null;
  try {
    message = await getMessage();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  // Signed in only with a usable access token and no session error.
  const session = await auth();
  const accessToken = session?.user && !session.error ? session.accessToken : undefined;
  let me: Me | null = null;
  let meError: string | null = null;
  if (accessToken) {
    try {
      me = await getMe(accessToken);
    } catch (e) {
      meError = e instanceof Error ? e.message : String(e);
    }
  }

  return (
    <main>
      <h1>My Housing Platform</h1>
      {message ? (
        <>
          <p style={{ fontSize: "1.25rem" }}>{message.text}</p>
          <p style={{ color: "#555", fontSize: "0.9rem" }}>
            Path: Next.js → FastAPI → {message.db_version}
            <br />
            Database time: {message.db_time}
          </p>
        </>
      ) : (
        <p style={{ color: "#a3231a" }}>Couldn't reach the API: {error}</p>
      )}

      <hr style={{ margin: "2rem 0" }} />

      {me ? (
        <>
          <p>
            Signed in as <b>{me.username}</b>
            {me.groups.length > 0 && <> ({me.groups.join(", ")})</>}. The API verified your
            Cognito token.
          </p>
          {me.pool === "users" && me.groups.includes("youth") && (
            <p>
              <Link href="/vault">My documents</Link>
            </p>
          )}
          <form
            action={async () => {
              "use server";
              await signOut({ redirect: false });
              redirect(cognitoLogoutUrl());
            }}
          >
            <button type="submit">Sign out</button>
          </form>
        </>
      ) : (
        <>
          {meError && <p style={{ color: "#a3231a" }}>Sign-in check failed: {meError}</p>}
          <form
            action={async () => {
              "use server";
              await signIn("cognito", { redirectTo: "/" });
            }}
          >
            <button type="submit">Sign in</button>
          </form>
        </>
      )}
    </main>
  );
}
