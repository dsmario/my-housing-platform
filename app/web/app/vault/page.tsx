import Link from "next/link";
import { signIn } from "../../auth";
import { ApiError, vaultFetch, type VaultDocument } from "../../lib/vault-api";
import { categoryLabel, DOWNLOAD_ERROR_OTHER, DOWNLOAD_ERRORS, size, STATUS } from "./labels";
import { RefreshWhileChecking } from "./refresh";
import { UploadForm } from "./upload-form";
import "./vault.css";

export const dynamic = "force-dynamic";

export const metadata = { title: "My documents · My Housing Platform" };

async function loadDocuments(): Promise<VaultDocument[] | "signed-out" | "not-youth"> {
  try {
    return await vaultFetch<VaultDocument[]>("/documents");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return "signed-out";
    if (e instanceof ApiError && e.status === 403) return "not-youth";
    throw e;
  }
}

export default async function VaultPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  const documents = await loadDocuments();

  if (documents === "signed-out") {
    return (
      <main className="vault">
        <h1>My documents</h1>
        <p>Sign in to see your documents.</p>
        <form
          action={async () => {
            "use server";
            await signIn("cognito", { redirectTo: "/vault" });
          }}
        >
          <button type="submit">Sign in</button>
        </form>
      </main>
    );
  }
  if (documents === "not-youth") {
    return (
      <main className="vault">
        <h1>My documents</h1>
        <p>This page is for youth accounts.</p>
        <Link href="/">Back to the home page</Link>
      </main>
    );
  }

  const checking = documents.some((d) => d.status === "uploading" || d.status === "scanning");
  return (
    <main className="vault">
      <p>
        <Link href="/">← Home</Link>
      </p>
      <h1>My documents</h1>
      <p>Keep copies of your important papers in one safe place.</p>

      {error && (
        <p className="error" role="alert">
          {DOWNLOAD_ERRORS[error] ?? DOWNLOAD_ERROR_OTHER}
        </p>
      )}

      <UploadForm />

      <h2>Your documents</h2>
      {documents.length === 0 ? (
        <p className="muted">You haven't saved any documents yet.</p>
      ) : (
        <ul className="vault-list">
          {documents.map((d) => (
            <li key={d.document_id}>
              <span className="vault-title">{d.title}</span>
              <span className="muted">
                {categoryLabel(d.category)} · {size(d.size_bytes)}
              </span>
              <span className={`vault-status vault-status-${d.status}`}>{STATUS[d.status] ?? d.status}</span>
              {d.status === "ready" ? (
                // A plain link, not client-side navigation: the response is a file.
                <a href={`/vault/documents/${d.document_id}`} download>
                  Download
                </a>
              ) : (
                <span />
              )}
            </li>
          ))}
        </ul>
      )}
      {documents.some((d) => d.status === "rejected") && (
        <p className="muted">
          Not accepted means the file failed our safety check or isn't a PDF, JPG, PNG or HEIC file.
        </p>
      )}
      <RefreshWhileChecking active={checking} />
    </main>
  );
}
