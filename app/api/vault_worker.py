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

Settings: VAULT_SCAN_QUEUE_URL, VAULT_QUARANTINE_BUCKET, VAULT_BUCKET, and
locally VAULT_S3_ENDPOINT and VAULT_SQS_ENDPOINT. Run: python vault_worker.py
"""

import json
import logging
import os
import signal

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


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    queue_url = os.environ["VAULT_SCAN_QUEUE_URL"]
    s3, sqs = clients()
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    log.info("waiting for scan results")
    while not stopping:
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
