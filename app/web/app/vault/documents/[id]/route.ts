import { redirect } from "next/navigation";
import type { NextRequest } from "next/server";
import { ApiError, vaultResponse } from "../../../../lib/vault-api";

// Downloads go through this server: the API checks access and decrypts on every
// request (Doc Vault §5), and no storage address reaches the browser.
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let res: Response;
  try {
    res = await vaultResponse(`/documents/${encodeURIComponent(id)}/file`);
  } catch (e) {
    // Back to the page the link was on: the youth's vault, or a supporter's shared list.
    const back = req.nextUrl.searchParams.get("back") === "shared" ? "/shared" : "/vault";
    redirect(`${back}?error=${e instanceof ApiError ? e.status : 500}`);
  }
  const headers = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  for (const name of ["content-type", "content-disposition", "content-length"]) {
    const value = res.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(res.body, { status: 200, headers });
}
