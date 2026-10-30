import Link from "next/link";
import { adminPage, type TableInfo } from "../../../lib/admin-api";

export const dynamic = "force-dynamic";

// Every table staff can edit, in one place. Taken from the API's own list, so a
// table that becomes editable shows up here without code changes.
// DRAFT wording, for review.
export default async function RulesPage() {
  const tables = (await adminPage<TableInfo[]>("/tables")).filter((t) => t.mode === "edit");

  return (
    <>
      <h1>Plan settings</h1>
      <p className="muted">
        Settings for housing plans: rule versions, and the formats for task results and readiness checks.
        These are the only tables staff can change.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Table</th>
              <th>Rows</th>
            </tr>
          </thead>
          <tbody>
            {tables.map((t) => (
              <tr key={t.table}>
                <td>
                  <Link href={`/admin/${t.table}`}>{t.name}</Link>
                </td>
                <td>{t.rows}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
