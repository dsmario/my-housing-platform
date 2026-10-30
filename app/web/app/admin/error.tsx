"use client";

import Link from "next/link";

// Draft wording for review.
export default function AdminError({ error }: { error: Error & { digest?: string } }) {
  return (
    <div className="admin">
      <h1>Something went wrong</h1>
      <p className="error">This page couldn't load. It may not exist, or the API may be down.</p>
      {error.digest && <p className="muted">Reference: {error.digest}</p>}
      <p>
        <Link href="/admin/tables">Back to all tables</Link>
      </p>
    </div>
  );
}
