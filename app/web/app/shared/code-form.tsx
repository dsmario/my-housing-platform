"use client";

import { useActionState } from "react";
import { makeCode, type CodeState } from "../vault/share-actions";

// DRAFT wording, for review.
export function CodeForm({ needsName }: { needsName: boolean }) {
  const [state, action, pending] = useActionState<CodeState, FormData>(makeCode, { error: null });
  const expires = state.expiresAt
    ? new Date(state.expiresAt).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })
    : null;

  return (
    <form action={action} className="vault-upload">
      <h2>Get a sharing code</h2>
      <p>Give this code to the young person. They enter it to share documents with you.</p>
      {needsName && (
        <label>
          Your name
          <input name="my_name" maxLength={60} placeholder="For example: Ms. Lopez" required />
          <span className="muted">Young people will see this name when they share with you.</span>
        </label>
      )}
      <button type="submit" disabled={pending}>
        {pending ? "Making code…" : state.code ? "Get another code" : "Get a code"}
      </button>
      {state.code && (
        <p className="notice" role="status">
          Your code: <b className="share-code">{state.code}</b>
          <br />
          It works one time, until {expires}.
        </p>
      )}
      {state.error && (
        <p className="error" role="alert">
          {state.error}
        </p>
      )}
    </form>
  );
}
