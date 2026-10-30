import Link from "next/link";
import { adminPage, type RowsResponse } from "../../../lib/admin-api";
import { Cell, text } from "../cell";
import { ModeBadge } from "../mode-badge";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;

type Props = {
  params: Promise<{ table: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function TablePage({ params, searchParams }: Props) {
  const { table } = await params;
  const query = await searchParams;
  const offset = Math.max(0, Number(query.offset) || 0);

  // Every other query parameter is a column filter, passed through to the API.
  const filters: [string, string][] = Object.entries(query)
    .filter(([key, value]) => key !== "offset" && typeof value === "string")
    .map(([key, value]) => [key, value as string]);
  const apiQuery = new URLSearchParams([...filters, ["offset", String(offset)], ["limit", String(PAGE_SIZE)]]);
  const data = await adminPage<RowsResponse>(`/tables/${table}?${apiQuery}`);

  const pageLink = (newOffset: number) =>
    `/admin/${table}?${new URLSearchParams([...filters, ["offset", String(newOffset)]])}`;

  // Draft wording for review.
  return (
    <>
      <p>
        <Link href="/admin/tables">← All tables</Link>
      </p>
      <h1>
        {table} <ModeBadge mode={data.mode} />
      </h1>

      {data.mode === "summary" ? (
        <>
          <p>
            <b>{data.total}</b> audit records.
          </p>
          {data.chain_broken_at == null ? (
            <p className="notice">Integrity check passed. No record has been changed or removed.</p>
          ) : (
            <p className="error">
              Integrity check failed at record {data.chain_broken_at}. A record may have been changed or
              removed. Report this to the security lead.
            </p>
          )}
          <p className="muted">
            Who viewed which document isn't shown here. Looking up one youth's history needs a logged
            reason and a second approver.
          </p>
        </>
      ) : (
        <>
          {filters.length > 0 && (
            <p className="notice">
              Showing rows where {filters.map(([k, v]) => `${k} = ${v}`).join(" and ")}.{" "}
              <Link href={`/admin/${table}`}>Show all rows</Link>
            </p>
          )}
          {data.mode === "edit" && (
            <p>
              <Link href={`/admin/${table}/new`}>+ Add a row</Link>
            </p>
          )}
          {data.rows.length === 0 ? (
            <p className="muted">No rows.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {data.columns.map((c) => (
                      <th key={c.name}>{c.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={String(row.id)}>
                      {data.columns.map((c) => (
                        <td key={c.name} className="cell" title={c.hidden ? undefined : text(row[c.name])}>
                          {c.name === "id" ? (
                            <Link href={`/admin/${table}/${row.id}`}>{String(row.id)}</Link>
                          ) : (
                            <Cell column={c} value={row[c.name]} />
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="pager">
            {offset > 0 && <Link href={pageLink(Math.max(0, offset - PAGE_SIZE))}>← Newer</Link>}
            <span className="muted">
              {data.total === 0
                ? "0 rows"
                : `Rows ${offset + 1}–${Math.min(offset + PAGE_SIZE, data.total)} of ${data.total}`}
            </span>
            {offset + PAGE_SIZE < data.total && <Link href={pageLink(offset + PAGE_SIZE)}>Older →</Link>}
          </div>
        </>
      )}
    </>
  );
}
