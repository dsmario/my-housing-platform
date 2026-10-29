"use client";

import { useActionState, useState } from "react";
import { createShare, type ShareState } from "../share-actions";

type Option = { value: string; label: string; note?: string };

// DRAFT wording, for review.
export function ShareForm({
  people,
  what,
  needsName,
}: {
  people: { supporter: string; name: string | null }[];
  what: Option[];
  needsName: boolean;
}) {
  const [state, action, pending] = useActionState<ShareState, FormData>(createShare, { error: null });
  const [who, setWho] = useState(people.length ? people[0].supporter : "code");

  return (
    <form action={action} className="vault-upload">
      <fieldset>
        <legend>Who do you want to share with?</legend>
        {people.map((p) => (
          <label key={p.supporter} className="inline">
            <input
              type="radio"
              name="who"
              value={p.supporter}
              checked={who === p.supporter}
              onChange={() => setWho(p.supporter)}
            />{" "}
            {p.name ?? "Someone you shared with before"}
          </label>
        ))}
        <label className="inline">
          <input type="radio" name="who" value="code" checked={who === "code"} onChange={() => setWho("code")} />{" "}
          {people.length ? "Someone new" : "Enter their code"}
        </label>
      </fieldset>
      {who === "code" && (
        <label>
          Their sharing code
          <input name="code" autoComplete="off" placeholder="For example: K7QM-4TXD" maxLength={20} />
          <span className="muted">Ask the person for their code. It works one time.</span>
        </label>
      )}

      <fieldset>
        <legend>What do you want to share?</legend>
        {what.map((w, i) => (
          <label key={w.value} className="inline-block">
            <input type="radio" name="what" value={w.value} defaultChecked={i === 0} /> {w.label}
            {w.note && <span className="muted"> {w.note}</span>}
          </label>
        ))}
      </fieldset>

      {needsName && (
        <label>
          Your name
          <input name="my_name" maxLength={60} placeholder="For example: Maria" required />
          <span className="muted">The person you share with will see this name.</span>
        </label>
      )}

      <button type="submit" disabled={pending}>
        {pending ? "Sharing…" : "Share"}
      </button>
      {state.error && (
        <p key={state.attempt} className="error" role="alert">
          {state.error}
        </p>
      )}
    </form>
  );
}
