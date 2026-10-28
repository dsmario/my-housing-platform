// Server-only calls to the admin API (/api/admin/*), using the signed-in
// staff member's access token. The API decides who is allowed; this file
// only carries the token and turns errors into readable messages.
import { notFound } from "next/navigation";
import { auth } from "../auth";

export type Mode = "edit" | "read" | "summary";

export type Column = {
  name: string;
  type: string;
  required: boolean;
  has_default: boolean;
  references: string | null;
  hidden: boolean;
};

export type TableInfo = {
  table: string;
  schema: string;
  name: string;
  mode: Mode;
  rows: number;
};

export type Row = Record<string, unknown>;

export type RowsResponse = {
  table: string;
  mode: Mode;
  columns: Column[];
  rows: Row[];
  total: number;
  chain_broken_at?: number | null;
};

export type RowResponse = {
  table: string;
  mode: Mode;
  columns: Column[];
  row: Row;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function accessToken(): Promise<string | undefined> {
  const session = await auth();
  return session?.user && !session.error ? session.accessToken : undefined;
}

export async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await accessToken();
  if (!token) throw new ApiError(401, "Sign-in required");
  const res = await fetch(`${process.env.API_URL}/api/admin${path}`, {
    ...init,
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    throw new ApiError(
      res.status,
      typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `API returned ${res.status}`,
    );
  }
  return body as T;
}

// For pages: an unknown table or row shows the "not found" page instead of an error.
// Signed out or not an admin: stop quietly. The admin layout renders on its own
// and already shows the sign-in screen, so this page's output is never shown.
export async function adminPage<T>(path: string): Promise<T> {
  try {
    return await adminFetch<T>(path);
  } catch (e) {
    if (e instanceof ApiError && [401, 403, 404].includes(e.status)) notFound();
    throw e;
  }
}

// Columns staff can type into. id and created_at are always set by the database.
export function editableColumns(columns: Column[]): Column[] {
  return columns.filter((c) => !c.hidden && c.name !== "id" && c.name !== "created_at");
}
