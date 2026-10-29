import Link from "next/link";
import { redirect } from "next/navigation";
import { vaultPage, type SharesResponse, type VaultDocument } from "../../../lib/vault-api";
import { categoryLabel, DELETING, size } from "../labels";
import { deleteDocument } from "../share-actions";
import "../vault.css";

export const dynamic = "force-dynamic";

export const metadata = { title: "Delete a document · My Housing Platform" };

// A page, not a pop-up, so the warning is easy to read and works with screen readers.
export default async function DeletePage({ searchParams }: { searchParams: Promise<{ document?: string }> }) {
  const { document: documentId } = await searchParams;
  const [documents, shares] = await Promise.all([
    vaultPage<VaultDocument[]>("/documents"),
    vaultPage<SharesResponse>("/shares"),
  ]);
  if (typeof documents === "string" || typeof shares === "string") redirect("/vault");
  const doc = documents.find((d) => d.document_id === documentId);
  if (!doc) redirect("/vault");

  // Shared by itself, or through a whole-category share.
  const shared = shares.shares.some((s) => s.document_id === doc.document_id || s.category === doc.category);

  return (
    <main className="vault">
      <p>
        <Link href="/vault">← My documents</Link>
      </p>
      <h1>{DELETING.title}</h1>
      <p className="vault-confirm">
        <b>{doc.title}</b>
        <span className="muted">
          {" "}
          · {categoryLabel(doc.category)} · {size(doc.size_bytes)}
        </span>
      </p>
      <p className="error">{DELETING.warning}</p>
      {shared && <p>{DELETING.sharedNote}</p>}
      <div className="vault-buttons">
        <form action={deleteDocument.bind(null, doc.document_id)}>
          <button type="submit" className="danger">
            {DELETING.confirm}
          </button>
        </form>
        <Link href="/vault">{DELETING.cancel}</Link>
      </div>
    </main>
  );
}
