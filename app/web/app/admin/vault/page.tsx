import Link from "next/link";
import { adminPage, type VaultHealth } from "../../../lib/admin-api";
import { count, utc } from "../format";

export const dynamic = "force-dynamic";

// Doc Vault at a glance: is the audit log intact and sealed, and are deleted
// files being wiped. Totals only; nothing here names a youth or a document.

const STATUS_LABELS: Record<keyof VaultHealth["documents"]["by_status"], string> = {
  ready: "Ready",
  uploading: "Upload started, not finished",
  scanning: "Being checked for viruses",
  rejected: "Rejected (wrong file type, too big, or unsafe)",
  deleted: "Deleted by the youth",
  expired: "Upload didn't finish (expired)",
};

export default async function VaultHealthPage() {
  const { audit, documents } = await adminPage<VaultHealth>("/vault-health");
  const allGood =
    audit.chain_broken_at == null &&
    !audit.chain_check_overdue &&
    !audit.seal_overdue &&
    documents.purge_overdue === 0 &&
    documents.stuck_checking === 0;

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
        <b>{count(audit.total, "audit record", "audit records")}</b>.{" "}
        <Link href="/admin/vault.audit_event">Details</Link>
      </p>
      {audit.chain_checked_at && <p>Last full check: {utc(audit.chain_checked_at)}.</p>}
      {audit.chain_check_overdue && (
        <p className="error">
          The worker hasn&apos;t checked the audit log for over {audit.chain_check_overdue_hours} hours. Check
          the vault worker.
        </p>
      )}
      {!audit.chain_checked_at ? null : audit.chain_broken_at == null ? (
        <p className="notice">Integrity check passed. No record has been changed or removed.</p>
      ) : (
        <p className="error">
          Integrity check failed at record {audit.chain_broken_at}. A record may have been changed or removed.
          Daily seals are paused until someone checks this. Report this to the security lead.
        </p>
      )}

      <h2>Daily seal</h2>
      <p className="muted">
        Once a day, new audit records are copied to locked storage. No one can change or delete them there.
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
          <b>{count(audit.unsealed, "record is", "records are")}</b> waiting for the next seal
          {audit.oldest_unsealed_at && <> (oldest: {utc(audit.oldest_unsealed_at)})</>}.
        </p>
      )}
      {audit.seal_overdue && (
        <p className="error">
          The seal is late. Some records have waited more than {audit.seal_overdue_hours} hours. Check the
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
      {documents.stuck_checking > 0 && (
        <p className="error">
          {count(documents.stuck_checking, "document has", "documents have")} been &ldquo;checking&rdquo; for over{" "}
          {documents.stuck_checking_minutes} minutes with no virus scan result. Check the scan queue and the vault
          worker.
        </p>
      )}
      {documents.purge_waiting > 0 && (
        <p>
          <b>{count(documents.purge_waiting, "deleted file is", "deleted files are")}</b> waiting to be wiped
          from storage. This normally takes a few minutes.
        </p>
      )}
      {documents.purge_overdue > 0 && (
        <p className="error">
          {count(documents.purge_overdue, "deleted file has", "deleted files have")} waited more than{" "}
          {documents.purge_overdue_minutes} minutes to be wiped. Check the vault worker.
        </p>
      )}
    </>
  );
}
