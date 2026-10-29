"""Doc Vault API (Doc Vault §3, §5, §6, §9, §10).

  GET  /api/vault/documents                the youth's own documents
  GET  /api/vault/documents/{id}/file      decrypted and streamed by the API (§5), for
                                           the owner or a supporter with a current share
  GET  /api/vault/profile                  role and display name
  DELETE /api/vault/documents/{id}         youth: delete, can't be undone (§10)

Sharing (§6), at the end of this file:
  POST   /api/vault/share-codes            supporter: a one-use code to give a youth
  POST   /api/vault/shares                 youth: share a document or category
  GET    /api/vault/shares                 youth: current shares and known people
  DELETE /api/vault/shares/{id}            youth: stop sharing, at once
  GET    /api/vault/shared                 supporter: documents shared with them

Upload, step by step:
  1. POST /api/vault/uploads        checks type, size, quota and rate; creates the
                                    document (status uploading) and returns a
                                    presigned POST for the quarantine bucket.
  2. The browser posts the file straight to quarantine. The POST is locked to one
     object key, the exact size and type, and expires after UPLOAD_LINK_SECONDS.
  3. POST /api/vault/uploads/{id}/done   status scanning. The API never reads
                                    quarantine; the worker takes it from here.

Limits are configuration (§9), read on every request:
  VAULT_ALLOWED_TYPES     comma-separated content types
  VAULT_CATEGORIES        comma-separated category keys
  VAULT_MAX_BYTES         per file
  VAULT_QUOTA_BYTES       per youth, all documents not rejected or deleted
  VAULT_UPLOADS_PER_HOUR  per youth

Storage: VAULT_QUARANTINE_BUCKET. VAULT_S3_PUBLIC_ENDPOINT is where browsers
reach S3 when it isn't AWS (local SeaweedFS); unset in AWS.

Audit rows record the youth's address and browser. Requests come through the web
server, which passes them in X-MHP-Client-IP and X-MHP-Client-UA. Those are
believed only with the shared secret in X-MHP-Web-Secret (API_FORWARD_SECRET), so
a direct caller can't write a made-up address into the audit log.
"""

import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import uuid
from functools import lru_cache
from urllib.parse import quote

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

import vault_keys
import vault_worker
from db import connect
from people import (
    Supporter,
    Youth,
    current_reader,
    current_supporter,
    current_youth,
    display_name,
    set_display_name,
)

# Log lines starting VAULT_ are counted by CloudWatch alarms (§11, terraform/app/alarms.tf).
# They carry no names or titles.
log = logging.getLogger("vault")
router = APIRouter(prefix="/api/vault")

UPLOAD_LINK_SECONDS = 300
SHARE_PURPOSE = "doc_vault"

DEFAULTS = {
    "VAULT_ALLOWED_TYPES": "application/pdf,image/jpeg,image/png,image/heic",
    "VAULT_CATEGORIES": "id_documents,income,housing,other",
    "VAULT_MAX_BYTES": str(15 * 1024 * 1024),
    "VAULT_QUOTA_BYTES": str(200 * 1024 * 1024),
    "VAULT_UPLOADS_PER_HOUR": "20",
}


def setting(name: str) -> str:
    return os.environ.get(name) or DEFAULTS[name]


def setting_list(name: str) -> list[str]:
    return [v.strip() for v in setting(name).split(",") if v.strip()]


@lru_cache
def presign_client():
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("VAULT_S3_PUBLIC_ENDPOINT")
    if endpoint:
        return boto3.client(
            "s3", endpoint_url=endpoint, config=Config(signature_version="s3v4", s3={"addressing_style": "path"})
        )
    # AWS: the regional address, so S3 never redirects a browser's POST.
    region = boto3.session.Session().region_name
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def from_web_server(request: Request) -> bool:
    secret = os.environ.get("API_FORWARD_SECRET", "")
    given = request.headers.get("x-mhp-web-secret", "")
    return bool(secret) and hmac.compare_digest(given.encode(), secret.encode())


def as_ip(value: str | None) -> str | None:
    """The value if it's an IP address (test clients send a name), else None."""
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def client_details(request: Request) -> tuple[str | None, str | None]:
    """(IP address, user agent) of the person, not of the web server in between."""
    if from_web_server(request):
        return as_ip(request.headers.get("x-mhp-client-ip")), request.headers.get("x-mhp-client-ua")
    return as_ip(request.client.host if request.client else None), request.headers.get("user-agent")


def audit(
    conn: psycopg.Connection,
    request: Request,
    action: str,
    outcome: str,
    actor: Youth | Supporter,
    owner_pseudonym_id: str | None,
    document_id: str | None = None,
    detail: dict | None = None,
    share_grant_id: str | None = None,
) -> None:
    """One row in the append-only audit log (§7): who acted, whose document. The caller commits."""
    ip, user_agent = client_details(request)
    conn.execute(
        "INSERT INTO vault.audit_event"
        " (action, outcome, actor_pseudonym_id, youth_pseudonym_id, document_id, share_grant_id,"
        "  ip_address, user_agent, detail)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            action,
            outcome,
            actor.pseudonym_id,
            owner_pseudonym_id,
            document_id,
            share_grant_id,
            ip,
            user_agent,
            Jsonb(detail) if detail else None,
        ),
    )


class UploadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str
    content_type: str
    size_bytes: int = Field(gt=0)


def refusal(conn, request: Request, youth: Youth, req: UploadRequest) -> HTTPException | None:
    """Why this upload can't go ahead, or None. Holds a per-youth lock so two
    requests at once can't both squeeze under the quota or rate limit."""
    if req.category not in setting_list("VAULT_CATEGORIES"):
        return HTTPException(422, "Choose one of the listed categories.")
    if req.content_type not in setting_list("VAULT_ALLOWED_TYPES"):
        return HTTPException(415, "This file type isn't accepted. Use PDF, JPG, PNG or HEIC.")
    if req.size_bytes > int(setting("VAULT_MAX_BYTES")):
        return HTTPException(413, "This file is too large.")

    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"vault-upload:{youth.pseudonym_id}",))
    used, recent = conn.execute(
        "SELECT coalesce(sum(size_bytes) FILTER (WHERE status NOT IN ('rejected', 'deleted')), 0),"
        "       count(*) FILTER (WHERE created_at > now() - interval '1 hour')"
        "  FROM vault.document WHERE owner_pseudonym_id = %s",
        (youth.pseudonym_id,),
    ).fetchone()
    if used + req.size_bytes > int(setting("VAULT_QUOTA_BYTES")):
        return HTTPException(413, "Your vault is full. Delete a document to make room.")
    if recent >= int(setting("VAULT_UPLOADS_PER_HOUR")):
        return HTTPException(429, "Too many uploads. Try again in an hour.")
    return None


@router.post("/uploads", status_code=201)
def start_upload(req: UploadRequest, request: Request, youth: Youth = Depends(current_youth)):
    document_id = str(uuid.uuid4())
    object_key = f"uploads/{document_id}"
    request_detail = {"content_type": req.content_type, "size_bytes": req.size_bytes, "category": req.category}

    with connect() as conn:
        problem = refusal(conn, request, youth, req)
        if problem is not None:
            detail = {**request_detail, "reason": problem.status_code}
            audit(conn, request, "upload", "denied", youth, youth.pseudonym_id, detail=detail)
            conn.commit()
            raise problem
        conn.execute(
            "INSERT INTO vault.document"
            " (id, owner_pseudonym_id, document_category_key, title, content_type, size_bytes,"
            "  status, quarantine_object_key)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'uploading', %s)",
            (document_id, youth.pseudonym_id, req.category, req.title, req.content_type, req.size_bytes, object_key),
        )
        audit(conn, request, "upload", "allowed", youth, youth.pseudonym_id, document_id, request_detail)

    upload = presign_client().generate_presigned_post(
        os.environ["VAULT_QUARANTINE_BUCKET"],
        object_key,
        Fields={"Content-Type": req.content_type},
        Conditions=[
            {"Content-Type": req.content_type},
            ["content-length-range", req.size_bytes, req.size_bytes],
        ],
        ExpiresIn=UPLOAD_LINK_SECONDS,
    )
    return {"document_id": document_id, "upload": upload, "expires_in": UPLOAD_LINK_SECONDS}


@router.post("/uploads/{document_id}/done")
def finish_upload(document_id: uuid.UUID, youth: Youth = Depends(current_youth)):
    with connect() as conn:
        row = conn.execute(
            "UPDATE vault.document SET status = 'scanning', status_changed_at = now()"
            " WHERE id = %s AND owner_pseudonym_id = %s AND status = 'uploading'"
            " RETURNING status",
            (document_id, youth.pseudonym_id),
        ).fetchone() or conn.execute(
            "SELECT status FROM vault.document WHERE id = %s AND owner_pseudonym_id = %s",
            (document_id, youth.pseudonym_id),
        ).fetchone()
    # Someone else's document looks the same as no document.
    if row is None:
        raise HTTPException(404, "Document not found.")
    return {"document_id": str(document_id), "status": row[0]}


# Listing and download (§5) ------------------------------------------------------

storage = lru_cache(vault_worker.s3_client)

EXTENSIONS = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic"}
CANNOT_OPEN = "You can't open this document."


@router.get("/documents")
def list_documents(youth: Youth = Depends(current_youth)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, document_category_key, content_type, size_bytes, status, created_at"
            "  FROM vault.document WHERE owner_pseudonym_id = %s AND status <> 'deleted'"
            " ORDER BY created_at DESC",
            (youth.pseudonym_id,),
        ).fetchall()
    return [
        {
            "document_id": str(r[0]),
            "title": r[1],
            "category": r[2],
            "content_type": r[3],
            "size_bytes": r[4],
            "status": r[5],
            "created_at": r[6].isoformat(),
        }
        for r in rows
    ]


VIEW_LOG_SECONDS = 300
REFUSAL_BURST = 5  # refused downloads by one person ...
REFUSAL_BURST_MINUTES = 10  # ... within this many minutes raise an alarm (§11)


def refusal_burst(conn: psycopg.Connection, reader: Youth | Supporter) -> None:
    """§11: denied requests above baseline for one person (possible misuse of a
    share, or ID guessing). Logged once, when the count reaches REFUSAL_BURST."""
    count = conn.execute(
        "SELECT count(*) FROM vault.audit_event WHERE action = 'download' AND outcome = 'denied'"
        "   AND actor_pseudonym_id = %s AND occurred_at > now() - make_interval(mins => %s)",
        (reader.pseudonym_id, REFUSAL_BURST_MINUTES),
    ).fetchone()[0]
    if count == REFUSAL_BURST:
        log.warning("VAULT_REFUSALS_ONE_PERSON actor=%s count=%d minutes=%d", reader.pseudonym_id, count, REFUSAL_BURST_MINUTES)


def download_name(title: str, content_type: str) -> str:
    """The title with the right ending. Cleaning can change the type (HEIC is
    stored as JPEG), so an upload's old ending is swapped, not added to."""
    ext = EXTENSIONS.get(content_type, "")
    endings = {ext, ".jpeg"} if ext == ".jpg" else {ext}
    stem, dot, old = title.rpartition(".")
    if "." + old.lower() in endings:
        name = title
    elif dot and stem and "." + old.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif"}:
        name = stem + ext
    else:
        name = title + ext
    return quote(name, safe="")


def active_share(conn: psycopg.Connection, owner: str, reader: str, document_id: str, category: str) -> str | None:
    """Policy Decision Point for supporters (§5, §6): the id of a current share
    from the owner to the reader covering this document, by itself or by its
    category, or None. Read live on every request, so a revoke applies at once.
    The youth's account must still be active."""
    row = conn.execute(
        "SELECT g.id FROM identity.share_grant g"
        "  JOIN identity.share_grant_scope s ON s.share_grant_id = g.id"
        "  JOIN identity.subject_pseudonym p ON p.id = g.youth_pseudonym_id"
        "  JOIN identity.account_subject a ON a.id = p.subject_id"
        " WHERE g.youth_pseudonym_id = %s AND g.grantee_pseudonym_id = %s"
        "   AND g.purpose_code = %s AND g.revoked_at IS NULL AND a.account_state = 'active'"
        "   AND ((s.field_group_key = 'vault.document' AND s.object_id = %s)"
        "     OR s.field_group_key = 'vault.category:' || %s)"
        " ORDER BY g.created_at LIMIT 1",
        (owner, reader, SHARE_PURPOSE, document_id, category),
    ).fetchone()
    return str(row[0]) if row else None


@router.get("/documents/{document_id}/file")
def download(document_id: uuid.UUID, request: Request, reader: Youth | Supporter = Depends(current_reader)):
    """Policy check, then the API decrypts and streams the file (§5). No storage
    URL ever reaches the browser, so a revoked share takes effect on the next request."""
    doc = str(document_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT owner_pseudonym_id, status, title, content_type, vault_object_key, wrapped_document_key,"
            "       document_category_key"
            "  FROM vault.document WHERE id = %s",
            (doc,),
        ).fetchone()
        owner = str(row[0]) if row else None
        grant = None

        def deny(status: int, message: str, reason: str) -> HTTPException:
            log.warning("VAULT_DOWNLOAD_DENIED reason=%s", reason)
            audit(conn, request, "download", "denied", reader, owner, doc, {"reason": reason}, grant)
            refusal_burst(conn, reader)
            conn.commit()
            return HTTPException(status, message)

        # Policy decision: the owner, or a supporter with a current share.
        # Someone else's document and a missing one get the same answer.
        if row is None:
            raise deny(403, CANNOT_OPEN, "no such document")
        _, status, title, content_type, vault_key, wrapped, category = row
        if owner != reader.pseudonym_id:
            if isinstance(reader, Supporter):
                grant = active_share(conn, owner, reader.pseudonym_id, doc, category)
            if grant is None:
                raise deny(403, CANNOT_OPEN, "not shared with requester")
        if status != "ready" and grant:
            # A share only ever shows saved documents; nothing else, not even that it exists.
            raise deny(403, CANNOT_OPEN, f"status {status}")
        if status == "deleted":
            raise deny(410, "This document can no longer be opened.", "deleted")
        if status != "ready":
            raise deny(409, "This document isn't ready yet.", f"status {status}")

        try:
            blob = storage().get_object(Bucket=os.environ["VAULT_BUCKET"], Key=vault_key)["Body"].read()
        except Exception:  # noqa: BLE001 - e.g. purged after deletion (§10), and the record restored from a backup
            log.exception("file missing for document %s", doc)
            raise deny(410, "This document can no longer be opened.", "file missing") from None
        try:
            youth_key = vault_keys.youth_data_key(conn, owner)
            document_key = vault_keys.unwrap_document_key(youth_key, wrapped, doc)
            data = vault_keys.decrypt_file(document_key, blob, doc)
        except vault_keys.KeyDestroyed:
            raise deny(410, "This document can no longer be opened.", "key destroyed") from None
        except vault_keys.DecryptFailed:
            log.error("VAULT_DECRYPT_FAILED document=%s", doc)
            raise deny(500, "Something went wrong. Try again later.", "decrypt failed") from None

        audit(conn, request, "download", "allowed", reader, owner, doc, share_grant_id=grant)

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"attachment; filename=\"document{EXTENSIONS.get(content_type, '')}\";"
            f" filename*=UTF-8''{download_name(title, content_type)}",
        },
    )


# Deleting (§10) ------------------------------------------------------------------


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, request: Request, youth: Youth = Depends(current_youth)):
    """In one transaction: the document is marked deleted, its wrapped key is
    removed (so it can't be decrypted again), and shares of this one document
    stop. The worker then purges every stored version of the file (vault_worker.purge_deleted)."""
    doc = str(document_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM vault.document WHERE id = %s AND owner_pseudonym_id = %s FOR UPDATE",
            (doc, youth.pseudonym_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Document not found.")
        if row[0] == "deleted":
            return Response(status_code=204)
        conn.execute(
            "UPDATE vault.document SET status = 'deleted', wrapped_document_key = NULL,"
            " deleted_at = now(), status_changed_at = now() WHERE id = %s",
            (doc,),
        )
        stopped = conn.execute(
            "UPDATE identity.share_grant g SET revoked_at = now() FROM identity.share_grant_scope s"
            " WHERE s.share_grant_id = g.id AND g.youth_pseudonym_id = %s AND g.purpose_code = %s"
            "   AND g.revoked_at IS NULL AND s.field_group_key = 'vault.document' AND s.object_id = %s"
            " RETURNING g.id",
            (youth.pseudonym_id, SHARE_PURPOSE, doc),
        ).fetchall()
        audit(conn, request, "delete", "allowed", youth, youth.pseudonym_id, doc, {"shares_stopped": len(stopped)})
        for (grant,) in stopped:
            audit(
                conn, request, "revoke", "allowed", youth, youth.pseudonym_id, doc, {"reason": "document deleted"}, str(grant)
            )
    return Response(status_code=204)


# Sharing (§6) ------------------------------------------------------------------
#
# A supporter makes a one-use code and gives it to the youth. The youth enters it
# to share one document or a whole category. After that, the youth can share more
# with the same person without a new code. Each share is its own share_grant with
# one scope, so stopping one never touches another, and nothing is ever deleted.
# Supporters have no sharing routes: re-sharing isn't possible.

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I/L to mix up
CODE_LENGTH = 8
MAX_OPEN_CODES = 5
CODE_TRIES_PER_HOUR = 10
BAD_CODE = "That code didn't work. Check it and try again."


def code_hash(code: str) -> bytes:
    """Codes are stored only as a hash. Case, spaces and dashes don't matter."""
    return hashlib.sha256("".join(c for c in code.upper() if c.isalnum()).encode()).digest()


def ensure_name(conn: psycopg.Connection, subject_id: str, my_name: str | None) -> str:
    """The name shown to the people this person shares with: the one given now, or the saved one."""
    name = " ".join((my_name or "").split())
    if name:
        set_display_name(conn, subject_id, name)
        return name
    saved = display_name(conn, subject_id)
    if not saved:
        raise HTTPException(422, "Add your name first.")
    return saved


@router.get("/profile")
def profile(reader: Youth | Supporter = Depends(current_reader)):
    with connect() as conn:
        name = display_name(conn, reader.subject_id)
    return {"role": "supporter" if isinstance(reader, Supporter) else "youth", "display_name": name}


class CodeRequest(BaseModel):
    my_name: str | None = Field(default=None, max_length=60)


@router.post("/share-codes", status_code=201)
def new_share_code(req: CodeRequest, supporter: Supporter = Depends(current_supporter)):
    with connect() as conn:
        ensure_name(conn, supporter.subject_id, req.my_name)
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"vault-codes:{supporter.pseudonym_id}",))
        open_codes = conn.execute(
            "SELECT count(*) FROM vault.share_code"
            " WHERE supporter_pseudonym_id = %s AND used_at IS NULL AND expires_at > now()",
            (supporter.pseudonym_id,),
        ).fetchone()[0]
        if open_codes >= MAX_OPEN_CODES:
            raise HTTPException(429, "You have enough codes for now. Use one, or wait a day.")
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        expires_at = conn.execute(
            "INSERT INTO vault.share_code (supporter_pseudonym_id, code_hash, expires_at)"
            " VALUES (%s, %s, now() + interval '24 hours') RETURNING expires_at",
            (supporter.pseudonym_id, code_hash(code)),
        ).fetchone()[0]
    return {"code": f"{code[:4]}-{code[4:]}", "expires_at": expires_at.isoformat()}


def known_supporters(conn: psycopg.Connection, youth: Youth) -> dict[str, str | None]:
    """People this youth has shared with before: pseudonym id -> their display name."""
    rows = conn.execute(
        "SELECT DISTINCT g.grantee_pseudonym_id, a.display_name FROM identity.share_grant g"
        "  JOIN identity.subject_pseudonym p ON p.id = g.grantee_pseudonym_id"
        "  JOIN identity.account_subject a ON a.id = p.subject_id"
        " WHERE g.youth_pseudonym_id = %s AND g.purpose_code = %s AND a.account_state = 'active'",
        (youth.pseudonym_id, SHARE_PURPOSE),
    ).fetchall()
    return {str(r[0]): r[1] for r in rows}


class ShareRequest(BaseModel):
    code: str | None = Field(default=None, max_length=20)
    supporter: uuid.UUID | None = None  # someone shared with before
    document_id: uuid.UUID | None = None
    category: str | None = None
    my_name: str | None = Field(default=None, max_length=60)


@router.post("/shares", status_code=201)
def share(req: ShareRequest, request: Request, youth: Youth = Depends(current_youth)):
    if (req.code is None) == (req.supporter is None):
        raise HTTPException(422, "Enter a code, or choose someone you've shared with before.")
    if (req.document_id is None) == (req.category is None):
        raise HTTPException(422, "Choose what to share.")
    doc = str(req.document_id) if req.document_id else None

    with connect() as conn:
        ensure_name(conn, youth.subject_id, req.my_name)
        if doc:
            row = conn.execute(
                "SELECT status FROM vault.document WHERE id = %s AND owner_pseudonym_id = %s",
                (doc, youth.pseudonym_id),
            ).fetchone()
            if row is None or row[0] in ("rejected", "deleted"):
                raise HTTPException(404, "Document not found.")
            scope, object_id = "vault.document", doc
        else:
            if req.category not in setting_list("VAULT_CATEGORIES"):
                raise HTTPException(422, "Choose one of the listed categories.")
            scope, object_id = f"vault.category:{req.category}", None

        code_id = None
        if req.code is not None:
            tries = conn.execute(
                "SELECT count(*) FROM vault.audit_event"
                " WHERE youth_pseudonym_id = %s AND action = 'share' AND outcome = 'denied'"
                "   AND detail->>'reason' = 'bad code' AND occurred_at > now() - interval '1 hour'",
                (youth.pseudonym_id,),
            ).fetchone()[0]
            if tries >= CODE_TRIES_PER_HOUR:
                raise HTTPException(429, "Too many tries. Try again in an hour.")
            found = conn.execute(
                "SELECT id, supporter_pseudonym_id FROM vault.share_code"
                " WHERE code_hash = %s AND used_at IS NULL AND expires_at > now() FOR UPDATE",
                (code_hash(req.code),),
            ).fetchone()
            if found is None:
                audit(conn, request, "share", "denied", youth, youth.pseudonym_id, doc, {"reason": "bad code"})
                conn.commit()
                raise HTTPException(400, BAD_CODE)
            code_id, supporter = str(found[0]), str(found[1])
        else:
            supporter = str(req.supporter)
            if supporter not in known_supporters(conn, youth):
                raise HTTPException(404, "Person not found.")

        # Sharing the same thing with the same person again changes nothing.
        existing = conn.execute(
            "SELECT g.id FROM identity.share_grant g JOIN identity.share_grant_scope s ON s.share_grant_id = g.id"
            " WHERE g.youth_pseudonym_id = %s AND g.grantee_pseudonym_id = %s AND g.purpose_code = %s"
            "   AND g.revoked_at IS NULL AND s.field_group_key = %s AND s.object_id IS NOT DISTINCT FROM %s",
            (youth.pseudonym_id, supporter, SHARE_PURPOSE, scope, object_id),
        ).fetchone()
        if existing:
            grant = str(existing[0])
        else:
            grant = str(
                conn.execute(
                    "INSERT INTO identity.share_grant (youth_pseudonym_id, grantee_pseudonym_id, purpose_code)"
                    " VALUES (%s, %s, %s) RETURNING id",
                    (youth.pseudonym_id, supporter, SHARE_PURPOSE),
                ).fetchone()[0]
            )
            conn.execute(
                "INSERT INTO identity.share_grant_scope (share_grant_id, field_group_key, object_id) VALUES (%s, %s, %s)",
                (grant, scope, object_id),
            )
        if code_id:
            conn.execute(
                "UPDATE vault.share_code SET used_at = now(), share_grant_id = %s WHERE id = %s", (grant, code_id)
            )
        audit(conn, request, "share", "allowed", youth, youth.pseudonym_id, doc, {"scope": scope}, grant)
    return {"share_id": grant, "supporter": supporter}


@router.get("/shares")
def list_shares(youth: Youth = Depends(current_youth)):
    """The youth's current shares, and the people they can share with again without a code."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT g.id, g.grantee_pseudonym_id, a.display_name, s.field_group_key, s.object_id, d.title, g.created_at"
            "  FROM identity.share_grant g"
            "  JOIN identity.share_grant_scope s ON s.share_grant_id = g.id"
            "  JOIN identity.subject_pseudonym p ON p.id = g.grantee_pseudonym_id"
            "  JOIN identity.account_subject a ON a.id = p.subject_id"
            "  LEFT JOIN vault.document d ON d.id = s.object_id"
            " WHERE g.youth_pseudonym_id = %s AND g.purpose_code = %s AND g.revoked_at IS NULL"
            "   AND (d.id IS NULL OR d.status <> 'deleted')"
            " ORDER BY g.created_at DESC",
            (youth.pseudonym_id, SHARE_PURPOSE),
        ).fetchall()
        people = known_supporters(conn, youth)
        my_name = display_name(conn, youth.subject_id)
    return {
        "my_name": my_name,
        "shares": [
            {
                "share_id": str(r[0]),
                "supporter": str(r[1]),
                "supporter_name": r[2],
                "document_id": str(r[4]) if r[4] else None,
                "document_title": r[5],
                "category": r[3].removeprefix("vault.category:") if r[3].startswith("vault.category:") else None,
                "created_at": r[6].isoformat(),
            }
            for r in rows
        ],
        "people": [{"supporter": k, "name": v} for k, v in people.items()],
    }


@router.delete("/shares/{share_id}", status_code=204)
def stop_sharing(share_id: uuid.UUID, request: Request, youth: Youth = Depends(current_youth)):
    """Takes effect on the supporter's next request: the policy check reads it live."""
    with connect() as conn:
        row = conn.execute(
            "SELECT revoked_at FROM identity.share_grant"
            " WHERE id = %s AND youth_pseudonym_id = %s AND purpose_code = %s FOR UPDATE",
            (share_id, youth.pseudonym_id, SHARE_PURPOSE),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Share not found.")
        if row[0] is None:
            conn.execute("UPDATE identity.share_grant SET revoked_at = now() WHERE id = %s", (share_id,))
            audit(conn, request, "revoke", "allowed", youth, youth.pseudonym_id, share_grant_id=str(share_id))
    return Response(status_code=204)


@router.get("/shared")
def shared_with_me(request: Request, supporter: Supporter = Depends(current_supporter)):
    """Ready documents currently shared with this supporter, grouped by youth.
    Opening this list is recorded as a view for each youth (§1, §7)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (d.id) d.owner_pseudonym_id, a.display_name, d.id, d.title,"
            "       d.document_category_key, d.content_type, d.size_bytes, d.created_at"
            "  FROM identity.share_grant g"
            "  JOIN identity.share_grant_scope s ON s.share_grant_id = g.id"
            "  JOIN vault.document d ON d.owner_pseudonym_id = g.youth_pseudonym_id AND d.status = 'ready'"
            "   AND ((s.field_group_key = 'vault.document' AND s.object_id = d.id)"
            "     OR s.field_group_key = 'vault.category:' || d.document_category_key)"
            "  JOIN identity.subject_pseudonym p ON p.id = g.youth_pseudonym_id"
            "  JOIN identity.account_subject a ON a.id = p.subject_id"
            " WHERE g.grantee_pseudonym_id = %s AND g.purpose_code = %s AND g.revoked_at IS NULL"
            "   AND a.account_state = 'active'"
            " ORDER BY d.id",
            (supporter.pseudonym_id, SHARE_PURPOSE),
        ).fetchall()
        groups: dict[str, dict] = {}
        for owner, name, doc, title, category, content_type, size, created in rows:
            group = groups.setdefault(str(owner), {"from": name, "documents": []})
            group["documents"].append(
                {
                    "document_id": str(doc),
                    "title": title,
                    "category": category,
                    "content_type": content_type,
                    "size_bytes": size,
                    "created_at": created.isoformat(),
                }
            )
        for owner, group in groups.items():
            # One view per youth per few minutes: page refreshes don't flood the log.
            recent = conn.execute(
                "SELECT 1 FROM vault.audit_event WHERE action = 'view' AND actor_pseudonym_id = %s"
                "   AND youth_pseudonym_id = %s AND occurred_at > now() - make_interval(secs => %s)",
                (supporter.pseudonym_id, owner, VIEW_LOG_SECONDS),
            ).fetchone()
            if recent is None:
                audit(conn, request, "view", "allowed", supporter, owner, detail={"documents": len(group["documents"])})
            group["documents"].sort(key=lambda d: d["created_at"], reverse=True)
        my_name = display_name(conn, supporter.subject_id)
    return {"my_name": my_name, "from": sorted(groups.values(), key=lambda g: (g["from"] or "").lower())}
