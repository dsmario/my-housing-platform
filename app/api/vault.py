"""Doc Vault API (Doc Vault §3, §5, §9).

  GET  /api/vault/documents                the youth's own documents
  GET  /api/vault/documents/{id}/file      decrypted and streamed by the API (§5)

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

import hmac
import ipaddress
import logging
import os
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
from people import Youth, current_youth

log = logging.getLogger("vault")
router = APIRouter(prefix="/api/vault")

UPLOAD_LINK_SECONDS = 300

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
    actor: Youth,
    owner_pseudonym_id: str | None,
    document_id: str | None = None,
    detail: dict | None = None,
) -> None:
    """One row in the append-only audit log (§7): who acted, whose document. The caller commits."""
    ip, user_agent = client_details(request)
    conn.execute(
        "INSERT INTO vault.audit_event"
        " (action, outcome, actor_pseudonym_id, youth_pseudonym_id, document_id, ip_address, user_agent, detail)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            action,
            outcome,
            actor.pseudonym_id,
            owner_pseudonym_id,
            document_id,
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


def download_name(title: str, content_type: str) -> str:
    ext = EXTENSIONS.get(content_type, "")
    name = title if title.lower().endswith(ext) else title + ext
    return quote(name, safe="")


@router.get("/documents/{document_id}/file")
def download(document_id: uuid.UUID, request: Request, youth: Youth = Depends(current_youth)):
    """Policy check, then the API decrypts and streams the file (§5). No storage
    URL ever reaches the browser, so a revoked share takes effect on the next request."""
    doc = str(document_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT owner_pseudonym_id, status, title, content_type, vault_object_key, wrapped_document_key"
            "  FROM vault.document WHERE id = %s",
            (doc,),
        ).fetchone()
        owner = str(row[0]) if row else None

        def deny(status: int, message: str, reason: str) -> HTTPException:
            audit(conn, request, "download", "denied", youth, owner, doc, {"reason": reason})
            conn.commit()
            return HTTPException(status, message)

        # Policy decision: owner only, until sharing exists. Someone else's
        # document and a missing one get the same answer.
        if row is None:
            raise deny(403, CANNOT_OPEN, "no such document")
        if owner != youth.pseudonym_id:
            raise deny(403, CANNOT_OPEN, "not shared with requester")
        _, status, title, content_type, vault_key, wrapped = row
        if status != "ready":
            raise deny(409, "This document isn't ready yet.", f"status {status}")

        try:
            youth_key = vault_keys.youth_data_key(conn, owner)
            document_key = vault_keys.unwrap_document_key(youth_key, wrapped, doc)
            blob = storage().get_object(Bucket=os.environ["VAULT_BUCKET"], Key=vault_key)["Body"].read()
            data = vault_keys.decrypt_file(document_key, blob, doc)
        except vault_keys.KeyDestroyed:
            raise deny(410, "This document can no longer be opened.", "key destroyed") from None
        except vault_keys.DecryptFailed:
            log.error("decrypt failed for document %s", doc)  # §11: alarm on decrypt failures
            raise deny(500, "Something went wrong. Try again later.", "decrypt failed") from None

        audit(conn, request, "download", "allowed", youth, owner, doc)

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
