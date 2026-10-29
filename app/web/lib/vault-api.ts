// Server-only calls to the Doc Vault API (/api/vault/*), using the signed-in
// youth's access token. The token never reaches the browser.
//
// The API writes an audit row for uploads and downloads. So that row names the
// youth's address and browser rather than this server's, they're passed along,
// with API_FORWARD_SECRET to show they come from here.
import { headers } from "next/headers";
import { accessToken, ApiError } from "./admin-api";

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

// Behind the AWS load balancer, the last x-forwarded-for entry is the address it
// saw. Earlier entries come from the browser and can't be trusted.
async function clientHeaders(): Promise<Record<string, string>> {
  const h = await headers();
  const forwarded = h.get("x-forwarded-for")?.split(",").map((s) => s.trim()).filter(Boolean) ?? [];
  const out: Record<string, string> = { "X-MHP-Web-Secret": process.env.API_FORWARD_SECRET ?? "" };
  if (forwarded.length) out["X-MHP-Client-IP"] = forwarded[forwarded.length - 1];
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
