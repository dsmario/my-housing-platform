import Link from "next/link";
import { signIn } from "../../auth";
import { ApiError, vaultFetch, type SharesResponse, type VaultDocument } from "../../lib/vault-api";
import { categoryLabel, DOWNLOAD_ERROR_OTHER, DOWNLOAD_ERRORS, SHARING, size, STATUS } from "./labels";
import { RefreshWhileChecking } from "./refresh";
import { stopSharing } from "./share-actions";
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

export default async function VaultPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; shared?: string; stopped?: string }>;
}) {
  const { error, shared, stopped } = await searchParams;
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

  const { shares } = await vaultFetch<SharesResponse>("/shares");
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
      {shared && (
        <p className="notice" role="status">
          {SHARING.shared}
        </p>
      )}
      {stopped && (
        <p className="notice" role="status">
          {SHARING.stopped}
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
                <span className="vault-actions">
                  {/* A plain link, not client-side navigation: the response is a file. */}
                  <a href={`/vault/documents/${d.document_id}`} download>
                    Download
                  </a>
                  <Link href={`/vault/share?document=${d.document_id}`}>{SHARING.shareLink}</Link>
                </span>
              ) : (
                <span />
              )}
            </li>
          ))}
        </ul>
      )}
      {documents.some((d) => d.status === "rejected") && (
        <p className="muted">
          Not accepted means the file failed our safety check, couldn't be read, or isn't a PDF, JPG, PNG or HEIC
          file.
        </p>
      )}

      <h2>{SHARING.sectionTitle}</h2>
      {shares.length === 0 ? (
        <p className="muted">{SHARING.noShares}</p>
      ) : (
        <ul className="vault-list vault-shares">
          {shares.map((s) => (
            <li key={s.share_id}>
              <span className="vault-title">{s.supporter_name ?? "Someone"}</span>
              <span>
                {s.document_title ?? SHARING.allIn(categoryLabel(s.category ?? "other"))}
                {s.category && <span className="muted"> · {SHARING.laterNote}</span>}
              </span>
              <form action={stopSharing.bind(null, s.share_id)}>
                <button type="submit">{SHARING.stop}</button>
              </form>
            </li>
          ))}
        </ul>
      )}
      <p>
        <Link href="/vault/share">Share a whole type of document</Link>
      </p>
      <RefreshWhileChecking active={checking} />
    </main>
  );
}
