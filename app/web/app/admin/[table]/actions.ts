"use server";

import { redirect } from "next/navigation";
import {
  adminFetch,
  ApiError,
  editableColumns,
  type Column,
  type Row,
  type RowsResponse,
} from "../../../lib/admin-api";

// On an error, the typed values come back so the form can be refilled (React
// clears a form after it submits). attempt changes on every try.
export type FormState = { error: string | null; values?: Record<string, string>; attempt?: number };

class FormError extends Error {}

const SKIP = Symbol("skip");

// Turns one form field back into the value the API expects. An empty field
// means "use the default" when adding and "clear it" when editing.
function parseField(column: Column, raw: string, creating: boolean): unknown {
  if (raw.trim() === "") return creating ? SKIP : null;
  if (column.type.endsWith("[]")) {
    return raw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  }
  if (column.type === "json" || column.type === "jsonb") {
    try {
      return JSON.parse(raw);
    } catch {
      throw new FormError(`${column.name} isn't valid JSON.`);
    }
  }
  if (column.type === "boolean") return raw === "true";
  return raw;
}

export async function saveRow(
  table: string,
  rowId: string | null,
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  let savedId: string;
  const typed = Object.fromEntries(
    [...formData.entries()].filter(([k]) => k.startsWith("f:")).map(([k, v]) => [k, String(v)]),
  );
  const failed = (error: string): FormState => ({ error, values: typed, attempt: (_prev.attempt ?? 0) + 1 });
  try {
    const { columns } = await adminFetch<RowsResponse>(`/tables/${table}?limit=1`);
    const values: Record<string, unknown> = {};
    for (const column of editableColumns(columns)) {
      const value = parseField(column, String(formData.get(`f:${column.name}`) ?? ""), rowId === null);
      if (value !== SKIP) values[column.name] = value;
    }
    const row = await adminFetch<Row>(rowId ? `/tables/${table}/${rowId}` : `/tables/${table}`, {
      method: rowId ? "PATCH" : "POST",
      body: JSON.stringify(values),
    });
    savedId = String(row.id);
  } catch (e) {
    if (e instanceof FormError || e instanceof ApiError) return failed(e.message);
    throw e;
  }
  redirect(`/admin/${table}/${savedId}?saved=1`);
}

export async function deleteRow(table: string, rowId: string, _prev: FormState): Promise<FormState> {
  try {
    await adminFetch(`/tables/${table}/${rowId}`, { method: "DELETE" });
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
  redirect(`/admin/${table}`);
}
