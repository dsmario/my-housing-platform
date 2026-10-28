import Link from "next/link";

// Draft wording for review.
export default function AdminNotFound() {
  return (
    <>
      <h1>Not found</h1>
      <p>That table or row doesn't exist.</p>
      <p>
        <Link href="/admin">Back to all tables</Link>
      </p>
    </>
  );
}
