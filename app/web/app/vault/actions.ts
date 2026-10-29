"use server";

import { revalidatePath } from "next/cache";
import { ApiError, vaultFetch, type PresignedPost } from "../../lib/vault-api";

export type StartResult = { documentId: string; upload: PresignedPost } | { error: string };

// Step 1 of an upload: the API checks the limits and returns a one-time link
// for the browser to send the file straight to storage.
export async function startUpload(input: {
  title: string;
  category: string;
  contentType: string;
  size: number;
}): Promise<StartResult> {
  try {
    const body = await vaultFetch<{ document_id: string; upload: PresignedPost }>("/uploads", {
      method: "POST",
      body: JSON.stringify({
        title: input.title,
        category: input.category,
        content_type: input.contentType,
        size_bytes: input.size,
      }),
    });
    return { documentId: body.document_id, upload: body.upload };
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

// Step 3: the file is in storage; the malware check takes it from here.
export async function finishUpload(documentId: string): Promise<{ error?: string }> {
  try {
    await vaultFetch(`/uploads/${encodeURIComponent(documentId)}/done`, { method: "POST" });
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
  revalidatePath("/vault");
  return {};
}
