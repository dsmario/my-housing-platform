import Link from "next/link";
import { adminPage, editableColumns, type RowsResponse } from "../../../../lib/admin-api";
import { saveRow } from "../actions";
import { RowForm } from "../row-form";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ table: string }> };

// Draft wording for review.
export default async function NewRowPage({ params }: Props) {
  const { table } = await params;
  const data = await adminPage<RowsResponse>(`/tables/${table}?limit=1`);

  return (
    <>
      <p>
        <Link href={`/admin/${table}`}>← {table}</Link>
      </p>
      <h1>Add a row to {table}</h1>
      {data.mode === "edit" ? (
        <RowForm
          columns={editableColumns(data.columns)}
          row={null}
          action={saveRow.bind(null, table, null)}
          submitLabel="Add row"
        />
      ) : (
        <p className="error">This table is view only.</p>
      )}
    </>
  );
}
