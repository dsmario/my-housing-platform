"""Doc Vault worker (Doc Vault §2, §3): from a scan result to a document in the vault.

Reads malware scan results from the queue. In AWS they come from GuardDuty
Malware Protection through EventBridge; locally from local_scanner.py, in the
same shape.

  NO_THREATS_FOUND        read the file (only if its scan tag says clean), check
                          its size and type, encrypt it with the youth's keys,
                          write it to the vault, mark it ready, then delete the
                          quarantine copy.
  THREATS_FOUND,          mark it rejected. The file stays in quarantine for
  UNSUPPORTED             support review until the 7-day lifecycle rule removes it.
  ACCESS_DENIED, FAILED   leave the message; the queue retries it, then moves it
                          to the dead-letter queue.

Every PURGE_EVERY_SECONDS it also purges deleted documents: every S3 version of
the file is removed for good (§10), and uploads that never finished are expired.
Once a day it seals the audit log (§7).

Settings: VAULT_SCAN_QUEUE_URL, VAULT_QUARANTINE_BUCKET, VAULT_BUCKET, VAULT_AUDIT_BUCKET, and
locally VAULT_S3_ENDPOINT and VAULT_SQS_ENDPOINT. Run: python vault_worker.py
"""

import json
import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone

import psycopg

import sanitize
import vault_keys
from db import connect

log = logging.getLogger("vault_worker")

SCAN_TAG = "GuardDutyMalwareScanStatus"
CLEAN = "NO_THREATS_FOUND"
REJECT = {"THREATS_FOUND", "UNSUPPORTED"}


class RetryLater(Exception):
    """Not handled now; leave the message for the queue to deliver again."""


def looks_like(content_type: str, head: bytes) -> bool:
    """Does the start of the file match the type the youth declared?"""
    if content_type == "application/pdf":
        return head.startswith(b"%PDF-")
    if content_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/heic":
        return head[4:8] == b"ftyp" and head[8:12] in {b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1"}
    return False


def version_args(version_id: str | None) -> dict:
    return {"VersionId": version_id} if version_id else {}


def handle(event: dict, s3, conn: psycopg.Connection) -> str:
    """Processes one scan result. Returns what happened, for the log."""
    detail = event.get("detail", {})
    obj = detail.get("s3ObjectDetails", {})
    bucket, key, version = obj.get("bucketName"), obj.get("objectKey"), obj.get("versionId")
    result = detail.get("scanResultDetails", {}).get("scanResultStatus")
    if bucket != os.environ["VAULT_QUARANTINE_BUCKET"] or not key:
        return "skipped: not a quarantine upload"

    row = conn.execute(
        "SELECT id, owner_pseudonym_id, content_type, size_bytes FROM vault.document"
        " WHERE quarantine_object_key = %s AND status IN ('uploading', 'scanning') FOR UPDATE",
        (key,),
    ).fetchone()
    if row is None:
        return "skipped: no document waiting for this file"
    document_id, owner, content_type, size_bytes = str(row[0]), str(row[1]), row[2], row[3]

    def reject(reason: str) -> str:
        conn.execute(
            "UPDATE vault.document SET status = 'rejected', status_changed_at = now() WHERE id = %s",
            (document_id,),
        )
        return f"rejected {document_id}: {reason}"

    if result in REJECT:
        return reject(result)
    if result != CLEAN:
        raise RetryLater(f"scan result {result!r} for {document_id}")

    # Read only what the scanner itself marked clean (§3).
    tags = s3.get_object_tagging(Bucket=bucket, Key=key, **version_args(version))["TagSet"]
    if {t["Key"]: t["Value"] for t in tags}.get(SCAN_TAG) != CLEAN:
        raise RetryLater(f"{document_id} is not tagged clean yet")
    data = s3.get_object(Bucket=bucket, Key=key, **version_args(version))["Body"].read(size_bytes + 1)
    if len(data) != size_bytes:
        return reject(f"size {len(data)}, expected {size_bytes}")
    if not looks_like(content_type, data[:16]):
        return reject(f"content is not {content_type}")

    # Rebuild it (§2, §3): only the cleaned copy is ever stored.
    try:
        data, content_type = sanitize.sanitize(content_type, data)
    except sanitize.Rejected as e:
        return reject(f"could not be cleaned: {e}")

    youth_key = vault_keys.youth_data_key(conn, owner)
    document_key, wrapped = vault_keys.new_document_key(youth_key, document_id)
    vault_key = f"documents/{document_id}"
    s3.put_object(
        Bucket=os.environ["VAULT_BUCKET"],
        Key=vault_key,
        Body=vault_keys.encrypt_file(document_key, data, document_id),
        ContentType="application/octet-stream",
    )
    conn.execute(
        "UPDATE vault.document SET status = 'ready', status_changed_at = now(), vault_object_key = %s,"
        " wrapped_document_key = %s, quarantine_object_key = NULL, content_type = %s, size_bytes = %s"
        " WHERE id = %s",
        (vault_key, wrapped, content_type, len(data), document_id),
    )
    conn.commit()
    try:
        s3.delete_object(Bucket=bucket, Key=key, **version_args(version))
    except Exception:  # noqa: BLE001 - the lifecycle rule removes it within 7 days anyway
        log.exception("could not delete quarantine copy of %s", document_id)
    return f"ready {document_id}"


def s3_client():
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("VAULT_S3_ENDPOINT") or None
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path" if endpoint else "auto"}),
    )


def clients():
    import boto3

    return s3_client(), boto3.client("sqs", endpoint_url=os.environ.get("VAULT_SQS_ENDPOINT") or None)


# Purging deleted documents (§10) ----------------------------------------------------

PURGE_EVERY_SECONDS = 300


def delete_all_versions(s3, bucket: str, key: str) -> int:
    """Permanently removes every version and delete marker of one object. Returns how many."""
    removed = 0
    while True:
        page = s3.list_object_versions(Bucket=bucket, Prefix=key)
        entries = [v for v in page.get("Versions", []) + page.get("DeleteMarkers", []) if v["Key"] == key]
        if not entries:
            return removed
        for v in entries:
            version = v.get("VersionId")
            s3.delete_object(Bucket=bucket, Key=key, **version_args(None if version in (None, "null") else version))
            removed += 1


def purge_deleted(s3, conn: psycopg.Connection) -> list[str]:
    """Removes every stored version of deleted documents (vault and quarantine), then
    records purged_at. Until this runs, an old S3 version plus a database backup (which
    still holds the key) could be read; afterwards there is nothing left to open.
    Safe to repeat: a failure leaves the rest for the next sweep."""
    rows = conn.execute(
        "SELECT id, vault_object_key, quarantine_object_key FROM vault.document"
        " WHERE status = 'deleted' AND purged_at IS NULL ORDER BY deleted_at LIMIT 50"
    ).fetchall()
    purged = []
    for doc, vault_key, quarantine_key in rows:
        buckets = os.environ["VAULT_BUCKET"], os.environ["VAULT_QUARANTINE_BUCKET"]
        for bucket, key in zip(buckets, (vault_key, quarantine_key)):
            if key:
                delete_all_versions(s3, bucket, key)
        conn.execute("UPDATE vault.document SET purged_at = now() WHERE id = %s", (doc,))
        conn.commit()
        purged.append(str(doc))
    return purged


# Unfinished uploads -----------------------------------------------------------------
#
# An upload whose file never reached quarantine (the youth closed the app, the
# connection dropped) would show as "checking" for ever and count against the
# youth's storage limit. The upload link lasts minutes, so after an hour no file
# can still come. If a file is there, it's left alone: its scan may be on the way.

EXPIRE_UPLOADS_AFTER = timedelta(hours=1)


def expire_unfinished_uploads(s3, conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT id, quarantine_object_key FROM vault.document"
        " WHERE status IN ('uploading', 'scanning') AND status_changed_at < now() - %s"
        " ORDER BY status_changed_at LIMIT 50",
        (EXPIRE_UPLOADS_AFTER,),
    ).fetchall()
    expired = []
    for doc, key in rows:
        if key and s3.list_object_versions(Bucket=os.environ["VAULT_QUARANTINE_BUCKET"], Prefix=key).get("Versions"):
            continue
        updated = conn.execute(
            "UPDATE vault.document SET status = 'expired', status_changed_at = now()"
            " WHERE id = %s AND status IN ('uploading', 'scanning')",
            (doc,),
        ).rowcount
        conn.commit()
        if updated:
            expired.append(str(doc))
    return expired


# Checks for the alarms (§11) ---------------------------------------------------------
#
# Log lines starting VAULT_ are counted by CloudWatch alarms (terraform/app/alarms.tf):
# a heartbeat every sweep (no heartbeat = worker down), purge failures, and the
# hourly audit chain check.

AUDIT_CHECK_EVERY_SECONDS = 3600
# After a failed check (say the worker started just before the API ran a
# migration), try again soon rather than in an hour.
AUDIT_CHECK_RETRY_SECONDS = 300


def check_audit_chain(conn: psycopg.Connection) -> int | None:
    """The id of the first audit row whose hash doesn't match, or None if the chain is intact."""
    broken = conn.execute("SELECT vault.verify_audit_chain()").fetchone()[0]
    # Kept for the admin Vault health page; the caller commits.
    conn.execute(
        "INSERT INTO vault.audit_chain_check (checked_at, last_event_id, chain_broken_at)"
        " SELECT now(), max(id), %s FROM vault.audit_event"
        " ON CONFLICT (id) DO UPDATE SET checked_at = EXCLUDED.checked_at,"
        "   last_event_id = EXCLUDED.last_event_id, chain_broken_at = EXCLUDED.chain_broken_at",
        (broken,),
    )
    if broken is None:
        log.info("VAULT_AUDIT_CHAIN_OK")
    else:
        log.error("VAULT_AUDIT_CHAIN_BROKEN at=%s", broken)
    return broken


def run_audit_check() -> int:
    """Runs the hourly check. Returns the seconds until the next one."""
    try:
        with connect() as conn:
            check_audit_chain(conn)
    except Exception:  # noqa: BLE001 - tried again soon
        log.exception("audit chain check failed; trying again in %d s", AUDIT_CHECK_RETRY_SECONDS)
        return AUDIT_CHECK_RETRY_SECONDS
    return AUDIT_CHECK_EVERY_SECONDS


# Daily audit seal (§7) ----------------------------------------------------------------
#
# Once a day after SEAL_HOUR_UTC, every audit row since the last seal is written to
# the Object Lock audit bucket (VAULT_AUDIT_BUCKET), where no one can change or
# delete it for the lock period, then read back and recorded in vault.audit_seal.
# A missed day is caught up on the next run. If the chain is broken, nothing is
# sealed (§11: halt seals until investigated).
#
# The file is JSON lines: a header (first and last id, the hash the batch starts
# from, the final hash), then one line per row with the exact text its hash covers,
# so anyone can recheck the chain from the file alone: row_hash = sha256(prev_hash + payload).

SEAL_HOUR_UTC = 2
SEAL_CHECK_EVERY_SECONDS = 900


class SealRefused(Exception):
    """The audit chain is broken, or the stored seal didn't read back the same."""


def seal_due(conn: psycopg.Connection, now: datetime) -> bool:
    """Due if there's no seal since the most recent SEAL_HOUR_UTC."""
    boundary = now.astimezone(timezone.utc).replace(hour=SEAL_HOUR_UTC, minute=0, second=0, microsecond=0)
    if boundary > now:
        boundary -= timedelta(days=1)
    last = conn.execute("SELECT max(sealed_at) FROM vault.audit_seal").fetchone()[0]
    return last is None or last < boundary


def seal_audit(s3, conn: psycopg.Connection, now: datetime | None = None) -> str | None:
    """Seals the audit rows since the last seal, if a seal is due. Returns the object
    key written, or None. The caller commits."""
    conn.execute("SELECT pg_advisory_xact_lock(727003)")  # one sealer at a time
    now = now or conn.execute("SELECT now()").fetchone()[0]
    if not seal_due(conn, now):
        return None
    broken = check_audit_chain(conn)
    if broken is not None:
        raise SealRefused(f"audit chain broken at row {broken}; not sealing")

    after = conn.execute("SELECT coalesce(max(last_event_id), 0) FROM vault.audit_seal").fetchone()[0]
    rows = conn.execute(
        "SELECT e.id, e.occurred_at, e.action, e.outcome, e.actor_pseudonym_id, e.youth_pseudonym_id,"
        "       e.document_id, e.share_grant_id, host(e.ip_address), e.user_agent, e.detail,"
        "       encode(e.prev_hash, 'hex'), encode(e.row_hash, 'hex'),"
        "       convert_from(vault.audit_event_payload(e), 'UTF8')"
        "  FROM vault.audit_event e WHERE e.id > %s ORDER BY e.id",
        (after,),
    ).fetchall()
    if not rows:
        log.info("VAULT_AUDIT_SEALED rows=0 (nothing new)")
        return None

    fields = (
        "id occurred_at action outcome actor_pseudonym_id youth_pseudonym_id document_id share_grant_id"
        " ip_address user_agent detail prev_hash row_hash payload"
    ).split()
    lines = [
        {
            "type": "doc-vault-audit-seal",
            "version": 1,
            "first_event_id": rows[0][0],
            "last_event_id": rows[-1][0],
            "rows": len(rows),
            "start_hash": rows[0][11],
            "head_hash": rows[-1][12],
            "sealed_at": now.astimezone(timezone.utc).isoformat(),
        }
    ] + [dict(zip(fields, r)) for r in rows]
    body = "".join(json.dumps(line, default=str, sort_keys=True) + "\n" for line in lines).encode()
    key = f"seals/{now.astimezone(timezone.utc):%Y/%m/%d}/{rows[0][0]:012d}-{rows[-1][0]:012d}.jsonl"

    bucket = os.environ["VAULT_AUDIT_BUCKET"]
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson")
    if s3.get_object(Bucket=bucket, Key=key)["Body"].read() != body:
        raise SealRefused(f"{key} didn't read back the same")
    conn.execute(
        "INSERT INTO vault.audit_seal (sealed_at, first_event_id, last_event_id, head_hash, object_key)"
        " VALUES (%s, %s, %s, decode(%s, 'hex'), %s)",
        (now, rows[0][0], rows[-1][0], rows[-1][12], key),
    )
    log.info("VAULT_AUDIT_SEALED rows=%d key=%s", len(rows), key)
    return key


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    queue_url = os.environ["VAULT_SCAN_QUEUE_URL"]
    s3, sqs = clients()
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    log.info("waiting for scan results")
    last_purge = last_seal_check = next_audit_check = float("-inf")  # all run at once on start
    while not stopping:
        if time.monotonic() - last_purge >= PURGE_EVERY_SECONDS:
            last_purge = time.monotonic()
            try:
                with connect() as conn:
                    for doc in purge_deleted(s3, conn):
                        log.info("purged %s", doc)
            except Exception:  # noqa: BLE001 - tried again at the next sweep
                log.exception("VAULT_PURGE_FAILED")
            try:
                with connect() as conn:
                    for doc in expire_unfinished_uploads(s3, conn):
                        log.info("upload never finished, expired %s", doc)
            except Exception:  # noqa: BLE001 - tried again at the next sweep
                log.exception("expiring unfinished uploads failed")
            log.info("VAULT_WORKER_HEARTBEAT")
        if time.monotonic() >= next_audit_check:
            next_audit_check = time.monotonic() + run_audit_check()
        if time.monotonic() - last_seal_check >= SEAL_CHECK_EVERY_SECONDS:
            last_seal_check = time.monotonic()
            try:
                with connect() as conn:
                    seal_audit(s3, conn)
            except Exception:  # noqa: BLE001 - tried again at the next check; the alarm counts these
                log.exception("VAULT_SEAL_FAILED")
        messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=5, WaitTimeSeconds=20).get(
            "Messages", []
        )
        for m in messages:
            try:
                with connect() as conn:
                    log.info(handle(json.loads(m["Body"]), s3, conn))
            except RetryLater as e:
                log.warning("retry later: %s", e)
                continue
            except Exception:  # noqa: BLE001 - leave it for the queue to retry
                log.exception("failed; the queue will retry this message")
                continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    log.info("stopped")


if __name__ == "__main__":
    run()
