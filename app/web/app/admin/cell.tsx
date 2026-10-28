import Link from "next/link";
import type { Column } from "../../lib/admin-api";

export function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// One value, as shown in a list or on a detail page. Links follow foreign
// keys to the row they point at.
export function Cell({ column, value, full = false }: { column: Column; value: unknown; full?: boolean }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  if (column.hidden) return <span className="muted">hidden</span>;
  if (column.references && typeof value === "string") {
    return <Link href={`/admin/${column.references}?id=${encodeURIComponent(value)}`}>{value}</Link>;
  }
  if (full && typeof value === "object") {
    return <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(value, null, 2)}</pre>;
  }
  return <>{text(value)}</>;
}
