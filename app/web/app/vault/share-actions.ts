"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { ApiError, vaultFetch, vaultResponse } from "../../lib/vault-api";

export type ShareState = { error: string | null; attempt?: number };

// Youth: share a document or a whole category, with a code or with someone
// they've shared with before.
export async function createShare(_prev: ShareState, form: FormData): Promise<ShareState> {
  const who = String(form.get("who") ?? "");
  const what = String(form.get("what") ?? "");
  const code = String(form.get("code") ?? "").trim();
  const myName = String(form.get("my_name") ?? "").trim();
  const failed = (error: string): ShareState => ({ error, attempt: (_prev.attempt ?? 0) + 1 });

  if (who === "code" && !code) return failed("Enter the code.");
  const body: Record<string, string> = {};
  if (who === "code") body.code = code;
  else if (who) body.supporter = who;
  else return failed("Choose who to share with.");
  if (what.startsWith("document:")) body.document_id = what.slice("document:".length);
  else if (what.startsWith("category:")) body.category = what.slice("category:".length);
  else return failed("Choose what to share.");
  if (myName) body.my_name = myName;

  try {
    await vaultFetch("/shares", { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    if (e instanceof ApiError) return failed(e.message);
    throw e;
  }
  revalidatePath("/vault");
  redirect("/vault?shared=1");
}

// Youth: stop sharing. The API applies it to the supporter's very next request.
export async function stopSharing(shareId: string): Promise<void> {
  try {
    await vaultResponse(`/shares/${encodeURIComponent(shareId)}`, { method: "DELETE" });
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    redirect(`/vault?error=${e.status}`);
  }
  revalidatePath("/vault");
  redirect("/vault?stopped=1");
}

export type CodeState = { error: string | null; code?: string; expiresAt?: string };

// Supporter: a one-use code to give to a youth.
export async function makeCode(_prev: CodeState, form: FormData): Promise<CodeState> {
  const myName = String(form.get("my_name") ?? "").trim();
  try {
    const body = await vaultFetch<{ code: string; expires_at: string }>("/share-codes", {
      method: "POST",
      body: JSON.stringify(myName ? { my_name: myName } : {}),
    });
    revalidatePath("/shared");
    return { error: null, code: body.code, expiresAt: body.expires_at };
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}
