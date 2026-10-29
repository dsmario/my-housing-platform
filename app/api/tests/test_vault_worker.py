"""Vault worker and local scanner: scan result to vault, rejections, retries."""

import os
import uuid

import io
from pathlib import Path

import pytest
from fakes import FakeS3, FakeSQS
from PIL import Image
from samples import jpeg_with_secrets, page_count, pdf_pages, pdf_with_script

import vault_keys
from db import connect
from local_scanner import EICAR, scan_once
from vault_worker import SCAN_TAG, RetryLater, handle, looks_like

QUARANTINE = os.environ["VAULT_QUARANTINE_BUCKET"]
VAULT = os.environ["VAULT_BUCKET"]
PDF_BYTES = pdf_pages(1)


def uploaded(client, s3: FakeS3, data: bytes = PDF_BYTES, content_type="application/pdf", tag="NO_THREATS_FOUND"):
    """A youth starts an upload, the file lands in quarantine, the scanner tags it."""
    headers = {"Authorization": f"Bearer stub:youth-{uuid.uuid4().hex[:10]}:youth"}
    body = client.post(
        "/api/vault/uploads",
        json={"title": "Lease", "category": "housing", "content_type": content_type, "size_bytes": len(data)},
        headers=headers,
    ).json()
    doc = body["document_id"]
    key = body["upload"]["fields"]["key"]
    client.post(f"/api/vault/uploads/{doc}/done", headers=headers)
    s3.put_object(Bucket=QUARANTINE, Key=key, Body=data)
    if tag:
        s3.put_object_tagging(Bucket=QUARANTINE, Key=key, Tagging={"TagSet": [{"Key": SCAN_TAG, "Value": tag}]})
    return doc, key


def event(key: str, result: str, bucket: str = QUARANTINE) -> dict:
    return {"detail": {"s3ObjectDetails": {"bucketName": bucket, "objectKey": key},
                       "scanResultDetails": {"scanResultStatus": result}}}


def process(e: dict, s3) -> str:
    with connect() as conn:
        return handle(e, s3, conn)


def document(doc: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT status, owner_pseudonym_id::text, quarantine_object_key, vault_object_key, wrapped_document_key,"
            "       content_type, size_bytes"
            " FROM vault.document WHERE id = %s",
            (doc,),
        ).fetchone()
    return dict(zip(["status", "owner", "quarantine_key", "vault_key", "wrapped", "content_type", "size"], row))


def stored_file(s3, doc: str) -> bytes:
    """What the vault holds for the document, decrypted the way the API does it."""
    d = document(doc)
    with connect() as conn:
        youth_key = vault_keys.youth_data_key(conn, d["owner"])
    document_key = vault_keys.unwrap_document_key(youth_key, d["wrapped"], doc)
    return vault_keys.decrypt_file(document_key, s3.objects[(VAULT, d["vault_key"])], doc)


# Clean files -----------------------------------------------------------------------


def test_clean_file_is_encrypted_into_the_vault(client):
    s3 = FakeS3()
    doc, key = uploaded(client, s3)
    assert process(event(key, "NO_THREATS_FOUND"), s3) == f"ready {doc}"

    d = document(doc)
    assert d["status"] == "ready" and d["quarantine_key"] is None
    assert d["vault_key"] == f"documents/{doc}"
    assert (QUARANTINE, key) not in s3.objects  # quarantine copy deleted

    clean = stored_file(s3, doc)
    assert clean.startswith(b"%PDF") and page_count(clean) == 1
    assert clean not in s3.objects[(VAULT, d["vault_key"])]  # encrypted at rest
    assert (d["content_type"], d["size"]) == ("application/pdf", len(clean))


# Cleaning (§2, §3) -----------------------------------------------------------------


def test_photo_is_stored_without_gps_or_hidden_data(client):
    s3 = FakeS3()
    doc, key = uploaded(client, s3, data=jpeg_with_secrets(), content_type="image/jpeg")
    process(event(key, "NO_THREATS_FOUND"), s3)
    clean = stored_file(s3, doc)
    assert dict(Image.open(io.BytesIO(clean)).getexif()) == {}
    assert b"Secret Artist" not in clean and b"<script>" not in clean


def test_pdf_is_stored_without_its_script(client):
    s3 = FakeS3()
    doc, key = uploaded(client, s3, data=pdf_with_script())
    process(event(key, "NO_THREATS_FOUND"), s3)
    clean = stored_file(s3, doc)
    assert b"JavaScript" not in clean and b"x.exe" not in clean and page_count(clean) == 1


def test_heic_is_stored_as_jpeg(client):
    s3 = FakeS3()
    heic = (Path(__file__).parent / "fixtures" / "tiny.heic").read_bytes()
    doc, key = uploaded(client, s3, data=heic, content_type="image/heic")
    process(event(key, "NO_THREATS_FOUND"), s3)
    assert document(doc)["content_type"] == "image/jpeg"
    assert Image.open(io.BytesIO(stored_file(s3, doc))).format == "JPEG"


def test_file_that_cannot_be_cleaned_is_rejected(client):
    s3 = FakeS3()
    doc, key = uploaded(client, s3, data=b"%PDF-1.4\nnot really a pdf, just the right first bytes")
    assert "could not be cleaned" in process(event(key, "NO_THREATS_FOUND"), s3)
    assert document(doc)["status"] == "rejected"
    assert not any(b == VAULT for b, _ in s3.objects)


def test_repeated_message_is_harmless(client):
    s3 = FakeS3()
    doc, key = uploaded(client, s3)
    process(event(key, "NO_THREATS_FOUND"), s3)
    stored = dict(s3.objects)
    assert process(event(key, "NO_THREATS_FOUND"), s3).startswith("skipped")
    assert s3.objects == stored and document(doc)["status"] == "ready"


# Rejections ------------------------------------------------------------------------


@pytest.mark.parametrize("result", ["THREATS_FOUND", "UNSUPPORTED"])
def test_infected_or_unscannable_file_is_rejected_and_kept(client, result):
    s3 = FakeS3()
    doc, key = uploaded(client, s3, tag=result)
    assert process(event(key, result), s3).startswith(f"rejected {doc}")
    assert document(doc)["status"] == "rejected"
    assert (QUARANTINE, key) in s3.objects  # kept 7 days for support review
    assert not any(b == VAULT for b, _ in s3.objects)


def test_file_that_is_not_what_it_claims_is_rejected(client):
    s3 = FakeS3()
    doc, key = uploaded(client, s3, data=b"MZ\x90\x00" + b"x" * 100)  # a Windows program, sent as a PDF
    assert "content is not application/pdf" in process(event(key, "NO_THREATS_FOUND"), s3)
    assert document(doc)["status"] == "rejected"


def test_size_mismatch_is_rejected(client):
    s3 = FakeS3()
    doc, key = uploaded(client, s3)
    s3.put_object(Bucket=QUARANTINE, Key=key, Body=PDF_BYTES + b"more")
    assert "size" in process(event(key, "NO_THREATS_FOUND"), s3)
    assert document(doc)["status"] == "rejected"


# Retries and things to ignore ---------------------------------------------------------


@pytest.mark.parametrize("result", ["FAILED", "ACCESS_DENIED", None])
def test_failed_scan_is_retried(client, result):
    s3 = FakeS3()
    doc, key = uploaded(client, s3)
    with pytest.raises(RetryLater):
        process(event(key, result), s3)
    assert document(doc)["status"] == "scanning"


def test_file_not_tagged_clean_is_not_read(client):
    s3 = FakeS3()
    doc, key = uploaded(client, s3, tag=None)
    with pytest.raises(RetryLater, match="not tagged clean"):
        process(event(key, "NO_THREATS_FOUND"), s3)
    assert document(doc)["status"] == "scanning"


def test_other_buckets_and_unknown_files_are_ignored(client):
    s3 = FakeS3()
    assert process(event("uploads/x", "NO_THREATS_FOUND", bucket="someone-elses"), s3).startswith("skipped")
    assert process(event(f"uploads/{uuid.uuid4()}", "NO_THREATS_FOUND"), s3).startswith("skipped")


@pytest.mark.parametrize(
    "content_type, head, ok",
    [
        ("application/pdf", b"%PDF-1.7", True),
        ("image/jpeg", b"\xff\xd8\xff\xe0", True),
        ("image/png", b"\x89PNG\r\n\x1a\n", True),
        ("image/heic", b"\x00\x00\x00\x18ftypheic", True),
        ("image/png", b"%PDF-1.7", False),
        ("image/heic", b"\x00\x00\x00\x18ftypisom", False),  # an MP4 video
        ("text/html", b"<html>", False),
    ],
)
def test_type_check(content_type, head, ok):
    assert looks_like(content_type, head) is ok


# Local scanner ---------------------------------------------------------------------------


def test_local_scanner_flags_the_test_virus_and_sends_guardduty_shaped_results():
    s3, sqs = FakeS3(), FakeSQS()
    s3.put_object(Bucket=QUARANTINE, Key="uploads/clean", Body=PDF_BYTES)
    s3.put_object(Bucket=QUARANTINE, Key="uploads/virus", Body=EICAR)
    s3.put_object(Bucket=QUARANTINE, Key="other/ignored", Body=EICAR)

    assert sorted(scan_once(s3, sqs, QUARANTINE, "q")) == [
        ("uploads/clean", "NO_THREATS_FOUND"),
        ("uploads/virus", "THREATS_FOUND"),
    ]
    assert s3.tags[(QUARANTINE, "uploads/virus")] == [{"Key": SCAN_TAG, "Value": "THREATS_FOUND"}]
    sent = {e["detail"]["s3ObjectDetails"]["objectKey"]: e for e in sqs.sent}
    assert sent["uploads/virus"]["detail-type"] == "GuardDuty Malware Protection Object Scan Result"
    assert sent["uploads/virus"]["detail"]["scanResultDetails"]["scanResultStatus"] == "THREATS_FOUND"
    assert scan_once(s3, sqs, QUARANTINE, "q") == []  # each file is scanned once
