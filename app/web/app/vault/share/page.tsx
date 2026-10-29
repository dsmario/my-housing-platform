import Link from "next/link";
import { redirect } from "next/navigation";
import { vaultPage, type SharesResponse, type VaultDocument } from "../../../lib/vault-api";
import { categoryLabel, CATEGORIES, SHARING } from "../labels";
import "../vault.css";
import { ShareForm } from "./share-form";

export const dynamic = "force-dynamic";

export const metadata = { title: "Share a document · My Housing Platform" };

// DRAFT wording, for review.
export default async function SharePage({ searchParams }: { searchParams: Promise<{ document?: string }> }) {
  const { document: documentId } = await searchParams;
  const [shares, documents] = await Promise.all([
    vaultPage<SharesResponse>("/shares"),
    vaultPage<VaultDocument[]>("/documents"),
  ]);
  if (typeof shares === "string" || typeof documents === "string") redirect("/vault");

  const doc = documents.find((d) => d.document_id === documentId && d.status !== "rejected");
  const what = doc
    ? [
        { value: `document:${doc.document_id}`, label: `Only "${doc.title}"` },
        {
          value: `category:${doc.category}`,
          label: SHARING.allIn(categoryLabel(doc.category)),
          note: SHARING.laterNote,
        },
      ]
    : CATEGORIES.map(([key, label]) => ({ value: `category:${key}`, label: SHARING.allIn(label), note: SHARING.laterNote }));

  return (
    <main className="vault">
      <p>
        <Link href="/vault">← My documents</Link>
      </p>
      <h1>Share</h1>
      <p>The person can open and download what you share. You can stop sharing at any time.</p>
      <ShareForm people={shares.people} what={what} needsName={!shares.my_name} />
    </main>
  );
}
