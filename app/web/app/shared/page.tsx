import Link from "next/link";
import { signIn } from "../../auth";
import { vaultPage, type SharedResponse } from "../../lib/vault-api";
import { categoryLabel, DOWNLOAD_ERROR_OTHER, DOWNLOAD_ERRORS, size } from "../vault/labels";
import "../vault/vault.css";
import { CodeForm } from "./code-form";

export const dynamic = "force-dynamic";

export const metadata = { title: "Shared with me · My Housing Platform" };

// For supporters: documents young people have shared with them (Doc Vault §6).
// Opening this page is recorded in the audit log. DRAFT wording, for review.
export default async function SharedPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  const shared = await vaultPage<SharedResponse>("/shared");

  if (shared === "signed-out") {
    return (
      <main className="vault">
        <h1>Shared with me</h1>
        <p>Sign in to see documents shared with you.</p>
        <form
          action={async () => {
            "use server";
            await signIn("cognito", { redirectTo: "/shared" });
          }}
        >
          <button type="submit">Sign in</button>
        </form>
      </main>
    );
  }
  if (shared === "not-allowed") {
    return (
      <main className="vault">
        <h1>Shared with me</h1>
        <p>This page is for supporter accounts.</p>
        <Link href="/">Back to the home page</Link>
      </main>
    );
  }

  return (
    <main className="vault">
      <p>
        <Link href="/">← Home</Link>
      </p>
      <h1>Shared with me</h1>
      <p>Documents young people have shared with you. They can stop sharing at any time.</p>

      {error && (
        <p className="error" role="alert">
          {DOWNLOAD_ERRORS[error] ?? DOWNLOAD_ERROR_OTHER}
        </p>
      )}

      <CodeForm needsName={!shared.my_name} />

      {shared.from.length === 0 ? (
        <p className="muted">Nothing has been shared with you yet.</p>
      ) : (
        shared.from.map((group, i) => (
          <section key={i}>
            <h2>From {group.from ?? "a young person"}</h2>
            <ul className="vault-list">
              {group.documents.map((d) => (
                <li key={d.document_id}>
                  <span className="vault-title">{d.title}</span>
                  <span className="muted">
                    {categoryLabel(d.category)} · {size(d.size_bytes)}
                  </span>
                  <span />
                  <a href={`/vault/documents/${d.document_id}?back=shared`} download>
                    Download
                  </a>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </main>
  );
}
