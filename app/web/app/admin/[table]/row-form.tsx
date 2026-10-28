"use client";

import { useActionState } from "react";
import type { Column, Row } from "../../../lib/admin-api";
import type { FormState } from "./actions";

type Action = (state: FormState, formData: FormData) => Promise<FormState>;

function initialText(column: Column, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join("\n");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

// Draft wording for review.
function hint(column: Column, creating: boolean): string {
  const parts: string[] = [column.type];
  if (column.required) parts.push("required");
  else if (creating && column.has_default) parts.push("leave empty to use the default");
  if (column.type.endsWith("[]")) parts.push("one per line");
  if (column.references) parts.push(`id of a row in ${column.references}`);
  return parts.join(" · ");
}

function Field({
  column,
  value,
  typed,
  creating,
}: {
  column: Column;
  value: unknown;
  typed: string | undefined;
  creating: boolean;
}) {
  const name = `f:${column.name}`;
  const text = typed ?? initialText(column, value);
  let input;
  if (column.type === "boolean") {
    input = (
      <select id={name} name={name} defaultValue={text}>
        <option value="">—</option>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  } else if (column.type === "json" || column.type === "jsonb" || column.type.endsWith("[]")) {
    input = <textarea id={name} name={name} defaultValue={text} rows={Math.min(16, text.split("\n").length + 2)} />;
  } else {
    input = <input id={name} name={name} defaultValue={text} required={column.required} />;
  }
  return (
    <div className="field">
      <label htmlFor={name}>
        <b>{column.name}</b>
      </label>
      {input}
      <small>{hint(column, creating)}</small>
    </div>
  );
}

export function RowForm({
  columns,
  row,
  action,
  submitLabel,
}: {
  columns: Column[];
  row: Row | null;
  action: Action;
  submitLabel: string;
}) {
  const [state, formAction, pending] = useActionState(action, { error: null });
  return (
    // A new key after each failed try remounts the fields with what was typed.
    <form action={formAction} key={state.attempt ?? 0}>
      {state.error && <p className="error">{state.error}</p>}
      {columns.map((c) => (
        <Field
          key={c.name}
          column={c}
          value={row?.[c.name]}
          typed={state.values?.[`f:${c.name}`]}
          creating={row === null}
        />
      ))}
      <div className="actions">
        <button type="submit" disabled={pending}>
          {pending ? "Saving…" : submitLabel}
        </button>
      </div>
    </form>
  );
}

export function DeleteButton({ action }: { action: (state: FormState) => Promise<FormState> }) {
  const [state, formAction, pending] = useActionState(action, { error: null });
  return (
    <form
      action={formAction}
      onSubmit={(e) => {
        if (!confirm("Delete this row? This can't be undone.")) e.preventDefault();
      }}
    >
      {state.error && <p className="error">{state.error}</p>}
      <button type="submit" className="danger" disabled={pending}>
        {pending ? "Deleting…" : "Delete row"}
      </button>
    </form>
  );
}
