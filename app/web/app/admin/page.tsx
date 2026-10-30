import Link from "next/link";
import { adminPage, type TableInfo } from "../../lib/admin-api";

export const dynamic = "force-dynamic";

// Dashboard: totals at a glance, from the same row counts the table browser
// uses. DRAFT wording, for review.
const CARDS: { table: string; label: string; note: string }[] = [
  { table: "identity.youth_profile", label: "Youth", note: "who have used the app" },
  { table: "identity.adult_profile", label: "Supporters", note: "who have used the app" },
  { table: "vault.document", label: "Documents", note: "in the Doc Vault, all statuses" },
  { table: "vault.audit_event", label: "Audit records", note: "Doc Vault access log" },
];

export default async function Dashboard() {
  const tables = await adminPage<TableInfo[]>("/tables");
  const rows = Object.fromEntries(tables.map((t) => [t.table, t.rows]));

  return (
    <>
      <h1>Dashboard</h1>
      <div className="admin-cards">
        {CARDS.map((c) => (
          <Link key={c.table} href={`/admin/${c.table}`} className="admin-card">
            <span className="admin-card-label">{c.label}</span>
            <span className="admin-card-number">{rows[c.table] ?? "–"}</span>
            <span className="muted">{c.note}</span>
          </Link>
        ))}
      </div>
      <p className="muted">More tools will appear in the menu as they're added.</p>
    </>
  );
}
