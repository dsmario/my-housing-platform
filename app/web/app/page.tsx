import { auth, signIn, signOut } from "../auth";

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
          <form
            action={async () => {
              "use server";
              await signOut({ redirectTo: "/" });
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
