// Rendered on the server for every request, so the text is always fresh from the API.
export const dynamic = "force-dynamic";

type Message = {
  text: string;
  db_time: string;
  db_version: string;
};

async function getMessage(): Promise<Message> {
  const res = await fetch(`${process.env.API_URL}/api/message`, { cache: "no-store" });
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
    </main>
  );
}
