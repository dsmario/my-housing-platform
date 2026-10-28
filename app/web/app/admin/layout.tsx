import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { auth, signIn, signOut, staffSignInEnabled } from "../../auth";
import { accessToken } from "../../lib/admin-api";
import "./admin.css";

export const dynamic = "force-dynamic";

type Me = { username: string; groups: string[]; pool: string };

async function getMe(): Promise<Me | null> {
  const token = await accessToken();
  if (!token) return null;
  const res = await fetch(`${process.env.API_URL}/api/me`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.ok ? res.json() : null;
}

function staffLogoutUrl(): string {
  const appOrigin = new URL(process.env.AUTH_URL ?? "").origin;
  const url = new URL("/logout", process.env.AUTH_STAFF_COGNITO_DOMAIN);
  url.searchParams.set("client_id", process.env.AUTH_STAFF_COGNITO_ID ?? "");
  url.searchParams.set("logout_uri", `${appOrigin}/admin`);
  return url.toString();
}

function SignOutButton({ staff }: { staff: boolean }) {
  return (
    <form
      action={async () => {
        "use server";
        await signOut({ redirect: false });
        redirect(staff ? staffLogoutUrl() : "/admin");
      }}
    >
      <button type="submit">Sign out</button>
    </form>
  );
}

export default async function AdminLayout({ children }: { children: ReactNode }) {
  const me = await getMe();
  const session = await auth();
  const isStaffSession = session?.provider === "cognito-staff";

  if (me?.pool === "staff" && me.groups.includes("admin")) {
    return (
      <div className="admin">
        <header className="admin-header">
          <Link href="/admin">
            <b>MHP admin</b>
          </Link>
          <span className="muted">
            Signed in as {me.username} (staff admin)
          </span>
          <SignOutButton staff />
        </header>
        {children}
      </div>
    );
  }

  // Draft wording for review.
  return (
    <div className="admin">
      <h1>MHP admin</h1>
      {me ? (
        <>
          <p>
            You're signed in as <b>{me.username}</b>, but this account can't use the admin pages.
            You need a staff account in the <b>admin</b> group.
          </p>
          <SignOutButton staff={isStaffSession} />
        </>
      ) : staffSignInEnabled ? (
        <>
          <p>Sign in with your staff account to continue.</p>
          <form
            action={async () => {
              "use server";
              await signIn("cognito-staff", { redirectTo: "/admin" });
            }}
          >
            <button type="submit">Staff sign in</button>
          </form>
        </>
      ) : (
        <p>
          Staff sign-in isn't set up here yet. Add the AUTH_STAFF_COGNITO_* settings to turn it on.
        </p>
      )}
    </div>
  );
}
