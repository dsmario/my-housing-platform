import Link from "next/link";
import { adminPage, type VaultHealth } from "../../../lib/admin-api";

export const dynamic = "force-dynamic";

// Doc Vault at a glance: is the audit log intact and sealed, and are deleted
// files being wiped. Totals only; nothing here names a youth or a document.
// DRAFT wording, for review.

const STATUS_LABELS: Record<keyof VaultHealth["documents"]["by_status"], string> = {
  ready: "Ready",
  uploading: "Uploading",
  scanning: "Being checked for viruses",
  rejected: "Rejected (not allowed or unsafe)",
  deleted: "Deleted by the youth",
};

// "2026-10-30 02:00 UTC": the same on every screen, and matches the AWS alarms.
function utc(iso: string): string {
  return `${iso.slice(0, 16).replace("T", " ")} UTC`;
}

export default async function VaultHealthPage() {
  const { audit, documents } = await adminPage<VaultHealth>("/vault-health");
  const allGood = audit.chain_broken_at == null && !audit.seal_overdue && documents.purge_overdue === 0;

  return (
    <>
      <h1>Vault health</h1>
      {allGood ? (
        <p className="notice">All checks passed.</p>
      ) : (
        <p className="error">Something needs attention. See the red notes below.</p>
      )}

      <h2>Audit log</h2>
      <p>
        <b>{audit.total}</b> audit records.{" "}
        <Link href="/admin/vault.audit_event">Details</Link>
      </p>
      {audit.chain_broken_at == null ? (
        <p className="notice">Integrity check passed. No record has been changed or removed.</p>
      ) : (
        <p className="error">
          Integrity check failed at record {audit.chain_broken_at}. A record may have been changed or removed.
          Daily seals are stopped until this is checked. Report this to the security lead.
        </p>
      )}

      <h2>Daily seal</h2>
      <p className="muted">
        Each night the audit records are copied to locked storage that no one can change or delete.
      </p>
      {audit.last_seal ? (
        <p>
          Last seal: <b>{utc(audit.last_seal.sealed_at)}</b>, records {audit.last_seal.first_event_id} to{" "}
          {audit.last_seal.last_event_id}. <Link href="/admin/vault.audit_seal">All seals</Link>
        </p>
      ) : (
        <p>No seal yet.</p>
      )}
      {audit.unsealed > 0 && (
        <p>
          <b>{audit.unsealed}</b> newer records will be in the next seal
          {audit.oldest_unsealed_at && <> (oldest from {utc(audit.oldest_unsealed_at)})</>}.
        </p>
      )}
      {audit.seal_overdue && (
        <p className="error">
          The seal is late: some records have waited more than {audit.seal_overdue_hours} hours. Check the
          vault worker.
        </p>
      )}

      <h2>Documents</h2>
      <div className="table-wrap">
        <table>
          <tbody>
            {(Object.keys(STATUS_LABELS) as (keyof typeof STATUS_LABELS)[]).map((s) => (
              <tr key={s}>
                <td>{STATUS_LABELS[s]}</td>
                <td>{documents.by_status[s]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {documents.purge_waiting > 0 && (
        <p>
          <b>{documents.purge_waiting}</b> deleted files are waiting to be wiped from storage. This
          normally takes a few minutes.
        </p>
      )}
      {documents.purge_overdue > 0 && (
        <p className="error">
          {documents.purge_overdue} deleted files have waited more than {documents.purge_overdue_minutes}{" "}
          minutes to be wiped. Check the vault worker.
        </p>
      )}
    </>
  );
}
