"""Doc Vault list and download: owner only, the API streams the file, every refusal audited."""

import os
import uuid

import pytest
from fakes import FakeS3
from samples import page_count, pdf_pages

import vault
import vault_keys
from db import connect
from vault_worker import SCAN_TAG, handle

QUARANTINE = os.environ["VAULT_QUARANTINE_BUCKET"]
VAULT = os.environ["VAULT_BUCKET"]


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(vault, "storage", lambda: fake)
    return fake


def youth() -> dict:
    return {"Authorization": f"Bearer stub:youth-{uuid.uuid4().hex[:10]}:youth"}


def add_document(client, s3, headers, data=None, title="My lease", ready=True) -> str:
    """Upload, land in quarantine, scan clean, and (if ready) let the worker move it to the vault."""
    data = data or pdf_pages(1)
    body = client.post(
        "/api/vault/uploads",
        json={"title": title, "category": "housing", "content_type": "application/pdf", "size_bytes": len(data)},
        headers=headers,
    ).json()
    doc, key = body["document_id"], body["upload"]["fields"]["key"]
    client.post(f"/api/vault/uploads/{doc}/done", headers=headers)
    s3.put_object(Bucket=QUARANTINE, Key=key, Body=data)
    s3.put_object_tagging(Bucket=QUARANTINE, Key=key, Tagging={"TagSet": [{"Key": SCAN_TAG, "Value": "NO_THREATS_FOUND"}]})
    if ready:
        result = {"detail": {"s3ObjectDetails": {"bucketName": QUARANTINE, "objectKey": key},
                             "scanResultDetails": {"scanResultStatus": "NO_THREATS_FOUND"}}}
        with connect() as conn:
            handle(result, s3, conn)
    return doc


def get_file(client, doc, headers):
    return client.get(f"/api/vault/documents/{doc}/file", headers=headers)


def downloads(doc) -> list[tuple]:
    with connect() as conn:
        return conn.execute(
            "SELECT outcome, actor_pseudonym_id::text, youth_pseudonym_id::text, detail->>'reason'"
            "  FROM vault.audit_event WHERE action = 'download' AND document_id = %s ORDER BY id",
            (doc,),
        ).fetchall()


def owner_of(doc) -> str:
    with connect() as conn:
        return str(conn.execute("SELECT owner_pseudonym_id FROM vault.document WHERE id = %s", (doc,)).fetchone()[0])


# Listing -------------------------------------------------------------------------


def test_list_shows_only_my_documents(client, s3):
    me, other = youth(), youth()
    ready = add_document(client, s3, me, title="Lease")
    waiting = add_document(client, s3, me, title="Pay stub", ready=False)
    add_document(client, s3, other, title="Someone else's")

    docs = client.get("/api/vault/documents", headers=me).json()
    assert {d["document_id"]: (d["title"], d["status"]) for d in docs} == {
        ready: ("Lease", "ready"),
        waiting: ("Pay stub", "scanning"),
    }


def test_deleted_documents_are_not_listed(client, s3):
    me = youth()
    doc = add_document(client, s3, me)
    with connect() as conn:
        conn.execute(
            "UPDATE vault.document SET status = 'deleted', wrapped_document_key = NULL, deleted_at = now() WHERE id = %s",
            (doc,),
        )
    assert client.get("/api/vault/documents", headers=me).json() == []


# Download ------------------------------------------------------------------------


def test_owner_downloads_the_cleaned_file(client, s3):
    me = youth()
    doc = add_document(client, s3, me, title="My lease")
    r = get_file(client, doc, me)
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF") and page_count(r.content) == 1
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "My%20lease.pdf" in r.headers["content-disposition"]
    assert downloads(doc) == [("allowed", owner_of(doc), owner_of(doc), None)]


def test_no_storage_address_reaches_the_browser(client, s3):
    me = youth()
    doc = add_document(client, s3, me)
    listed = client.get("/api/vault/documents", headers=me).text + str(get_file(client, doc, me).headers)
    assert "documents/" not in listed and VAULT not in listed


def test_another_youth_is_refused_and_audited(client, s3):
    doc = add_document(client, s3, youth())
    intruder = youth()
    r = get_file(client, doc, intruder)
    assert r.status_code == 403 and r.json() == {"detail": "You can't open this document."}
    [(outcome, actor, owner, reason)] = downloads(doc)
    assert (outcome, owner, reason) == ("denied", owner_of(doc), "not shared with requester")
    assert actor != owner


def test_missing_document_looks_the_same_as_someone_elses(client, s3):
    doc = str(uuid.uuid4())
    r = get_file(client, doc, youth())
    assert r.status_code == 403 and r.json() == {"detail": "You can't open this document."}
    [(outcome, _, owner, reason)] = downloads(doc)
    assert (outcome, owner, reason) == ("denied", None, "no such document")


def test_document_not_ready_yet(client, s3):
    me = youth()
    doc = add_document(client, s3, me, ready=False)
    assert get_file(client, doc, me).status_code == 409
    assert downloads(doc)[0][0] == "denied"


def test_destroyed_key_means_the_file_cannot_be_opened(client, s3):
    me = youth()
    doc = add_document(client, s3, me)
    with connect() as conn:
        vault_keys.destroy_youth_key(conn, owner_of(doc))
    r = get_file(client, doc, me)
    assert r.status_code == 410
    assert not r.content.startswith(b"%PDF")
    assert downloads(doc)[-1][3] == "key destroyed"


def test_changed_file_in_storage_is_refused(client, s3):
    me = youth()
    doc = add_document(client, s3, me)
    stored = bytearray(s3.objects[(VAULT, f"documents/{doc}")])
    stored[-1] ^= 1
    s3.objects[(VAULT, f"documents/{doc}")] = bytes(stored)
    r = get_file(client, doc, me)
    assert r.status_code == 500 and not r.content.startswith(b"%PDF")
    assert downloads(doc)[-1][:1] == ("denied",) and downloads(doc)[-1][3] == "decrypt failed"


@pytest.mark.parametrize(
    "headers, status",
    [
        ({}, 401),
        ({"Authorization": "Bearer stub:sam:supporter"}, 403),
        ({"Authorization": "Bearer stub:alex:admin:staff"}, 403),
    ],
)
def test_only_youth_can_list_or_download(client, s3, headers, status):
    doc = add_document(client, s3, youth())
    assert client.get("/api/vault/documents", headers=headers).status_code == status
    assert get_file(client, doc, headers).status_code == status


def test_download_name_is_safe():
    assert vault.download_name('a"b\r\nc', "application/pdf") == "a%22b%0D%0Ac.pdf"
    assert vault.download_name("scan.PDF", "application/pdf") == "scan.PDF"
    assert vault.download_name("scan", "image/png") == "scan.png"
    assert vault.download_name("photo.jpg", "image/jpeg") == "photo.jpg"
    assert vault.download_name("photo.jpeg", "image/jpeg") == "photo.jpeg"
    # Cleaning stored a HEIC as JPEG: the old ending is swapped, not stacked.
    assert vault.download_name("IMG_1234.HEIC", "image/jpeg") == "IMG_1234.jpg"
    assert vault.download_name("v1.2 lease", "application/pdf") == "v1.2%20lease.pdf"
