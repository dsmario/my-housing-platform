"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { finishUpload, startUpload } from "./actions";
import { fileType } from "./file-type";
import { CATEGORIES, MAX_BYTES } from "./labels";

export function UploadForm() {
  const router = useRouter();
  const form = useRef<HTMLFormElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setDone(false);
    const data = new FormData(e.currentTarget);
    const file = data.get("file");
    const title = String(data.get("title") ?? "").trim();
    const category = String(data.get("category") ?? "");
    if (!(file instanceof File) || file.size === 0) return setError("Choose a file.");
    if (file.size > MAX_BYTES) return setError("This file is too large.");

    setBusy(true);
    try {
      // 1. Ask for a one-time upload link. The API checks type, size and limits.
      const started = await startUpload({ title: title || file.name, category, contentType: fileType(file), size: file.size });
      if ("error" in started) return setError(started.error);

      // 2. Send the file straight to storage. The file goes last, as S3 requires.
      const post = new FormData();
      for (const [k, v] of Object.entries(started.upload.fields)) post.append(k, v);
      post.append("file", file);
      const res = await fetch(started.upload.url, { method: "POST", body: post });
      if (!res.ok) return setError("The upload didn't finish. Try again.");

      // 3. Tell the API the file is there, so the check can start.
      const finished = await finishUpload(started.documentId);
      if (finished.error) return setError(finished.error);

      form.current?.reset();
      setDone(true);
      router.refresh();
    } catch {
      setError("The upload didn't finish. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={form} onSubmit={submit} className="vault-upload">
      <h2>Add a document</h2>
      <label>
        Name
        <input name="title" maxLength={200} placeholder="For example: Birth certificate" />
      </label>
      <fieldset>
        <legend>Type</legend>
        {CATEGORIES.map(([key, label], i) => (
          <label key={key} className="inline">
            <input type="radio" name="category" value={key} defaultChecked={i === 0} /> {label}
          </label>
        ))}
      </fieldset>
      <label>
        File
        <input type="file" name="file" accept=".pdf,.jpg,.jpeg,.png,.heic,application/pdf,image/jpeg,image/png,image/heic" required />
      </label>
      <p className="muted">PDF, JPG, PNG or HEIC. Up to 15 MB.</p>
      <button type="submit" disabled={busy}>
        {busy ? "Saving…" : "Save document"}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {done && (
        <p className="notice" role="status">
          Got it. We're checking your file. It will show as Saved when it's ready.
        </p>
      )}
    </form>
  );
}
