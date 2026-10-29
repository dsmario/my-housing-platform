// Server-only calls to the Doc Vault API (/api/vault/*), using the signed-in
// youth's access token. The token never reaches the browser.
//
// The API writes an audit row for uploads and downloads. So that row names the
// youth's address and browser rather than this server's, they're passed along,
// with API_FORWARD_SECRET to show they come from here.
import { headers } from "next/headers";
import { accessToken, ApiError } from "./admin-api";
import { lastForwardedIp } from "./forwarded";

export { ApiError };

export type VaultDocument = {
  document_id: string;
  title: string;
  category: string;
  content_type: string;
  size_bytes: number;
  status: "uploading" | "scanning" | "ready" | "rejected" | "deleted";
  created_at: string;
};

export type PresignedPost = { url: string; fields: Record<string, string> };

async function clientHeaders(): Promise<Record<string, string>> {
  const h = await headers();
  const out: Record<string, string> = { "X-MHP-Web-Secret": process.env.API_FORWARD_SECRET ?? "" };
  const ip = lastForwardedIp(h.get("x-forwarded-for"));
  if (ip) out["X-MHP-Client-IP"] = ip;
  const agent = h.get("user-agent");
  if (agent) out["X-MHP-Client-UA"] = agent;
  return out;
}

// The raw response, for downloads. Throws ApiError on a refusal.
export async function vaultResponse(path: string, init?: RequestInit): Promise<Response> {
  const token = await accessToken();
  if (!token) throw new ApiError(401, "Sign-in required");
  const res = await fetch(`${process.env.API_URL}/api/vault${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      ...(await clientHeaders()),
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : `API returned ${res.status}`);
  }
  return res;
}

export async function vaultFetch<T>(path: string, init?: RequestInit): Promise<T> {
  return (await vaultResponse(path, init)).json() as Promise<T>;
}

// Sharing (Doc Vault §6) -----------------------------------------------------

export type Profile = { role: "youth" | "supporter"; display_name: string | null };

export type Share = {
  share_id: string;
  supporter: string;
  supporter_name: string | null;
  document_id: string | null;
  document_title: string | null;
  category: string | null;
  created_at: string;
};

export type SharesResponse = {
  my_name: string | null;
  shares: Share[];
  people: { supporter: string; name: string | null }[];
};

export type SharedResponse = {
  my_name: string | null;
  from: { from: string | null; documents: Omit<VaultDocument, "status">[] }[];
};

// For pages: signed out or the wrong kind of account, instead of an error.
export async function vaultPage<T>(path: string): Promise<T | "signed-out" | "not-allowed"> {
  try {
    return await vaultFetch<T>(path);
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return "signed-out";
    if (e instanceof ApiError && e.status === 403) return "not-allowed";
    throw e;
  }
}
