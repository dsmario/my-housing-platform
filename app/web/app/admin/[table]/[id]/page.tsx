import Link from "next/link";
import { adminPage, editableColumns, type RowResponse } from "../../../../lib/admin-api";
import { Cell } from "../../cell";
import { ModeBadge } from "../../mode-badge";
import { deleteRow, saveRow } from "../actions";
import { DeleteButton, RowForm } from "../row-form";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ table: string; id: string }>;
  searchParams: Promise<{ saved?: string }>;
};

// Draft wording for review.
export default async function RowPage({ params, searchParams }: Props) {
  const { table, id } = await params;
  const { saved } = await searchParams;
  const data = await adminPage<RowResponse>(`/tables/${table}/${id}`);

  return (
    <>
      <p>
        <Link href={`/admin/${table}`}>← {table}</Link>
      </p>
      <h1>
        Row {id} <ModeBadge mode={data.mode} />
      </h1>
      {saved && <p className="notice">Saved.</p>}

      {data.mode === "edit" ? (
        <>
          <RowForm
            columns={editableColumns(data.columns)}
            row={data.row}
            action={saveRow.bind(null, table, id)}
            submitLabel="Save changes"
          />
          <p className="muted">Created {String(data.row.created_at ?? "")}</p>
          <hr style={{ margin: "2rem 0" }} />
          <DeleteButton action={deleteRow.bind(null, table, id)} />
        </>
      ) : (
        <div className="table-wrap">
          <table>
            <tbody>
              {data.columns.map((c) => (
                <tr key={c.name}>
                  <th>{c.name}</th>
                  <td>
                    <Cell column={c} value={data.row[c.name]} full />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
