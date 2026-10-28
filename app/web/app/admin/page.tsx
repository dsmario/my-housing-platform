import Link from "next/link";
import { adminPage, type TableInfo } from "../../lib/admin-api";
import { ModeBadge } from "./mode-badge";

export const dynamic = "force-dynamic";

// Draft wording for review.
const SCHEMAS: Record<string, string> = {
  identity: "Identity and sharing",
  planning: "Planning and rules",
  vault: "Doc Vault",
};

export default async function AdminHome() {
  const tables = await adminPage<TableInfo[]>("/tables");
  const schemas = [...new Set(tables.map((t) => t.schema))];

  return (
    <>
      <h1>Tables</h1>
      <p className="muted">
        Configuration tables can be edited. Youth records and history are view only. The Doc Vault
        audit log shows totals only.
      </p>
      {schemas.map((schema) => (
        <section key={schema}>
          <h2>{SCHEMAS[schema] ?? schema}</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Table</th>
                  <th>Access</th>
                  <th>Rows</th>
                </tr>
              </thead>
              <tbody>
                {tables
                  .filter((t) => t.schema === schema)
                  .map((t) => (
                    <tr key={t.table}>
                      <td>
                        <Link href={`/admin/${t.table}`}>{t.name}</Link>
                      </td>
                      <td>
                        <ModeBadge mode={t.mode} />
                      </td>
                      <td>{t.rows}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </>
  );
}
