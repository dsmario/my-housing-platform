"""Doc Vault deletion (§10, §12): the key goes at once, shares stop, the worker purges every version."""

import os
import uuid

from test_vault_download import add_document, get_file, s3  # noqa: F401 - s3 is a fixture
from test_vault_sharing import new_code, share, shared_titles, supporter, youth

from db import connect
from vault_worker import purge_deleted

QUARANTINE = os.environ["VAULT_QUARANTINE_BUCKET"]
VAULT = os.environ["VAULT_BUCKET"]


def delete(client, doc, headers):
    return client.delete(f"/api/vault/documents/{doc}", headers=headers)


def row(doc) -> dict:
    with connect() as conn:
        r = conn.execute(
            "SELECT status, wrapped_document_key, deleted_at, purged_at, vault_object_key, quarantine_object_key"
            "  FROM vault.document WHERE id = %s",
            (doc,),
        ).fetchone()
    return dict(zip(["status", "wrapped", "deleted_at", "purged_at", "vault_key", "quarantine_key"], r))


def purge(s3) -> list[str]:
    with connect() as conn:
        return purge_deleted(s3, conn)


def versions(s3, bucket, key) -> list:
    return s3.list_object_versions(Bucket=bucket, Prefix=key)["Versions"]


def test_delete_removes_the_key_at_once_and_hides_the_document(client, s3):
    me = youth()
    doc = add_document(client, s3, me)
    assert delete(client, doc, me).status_code == 204
    r = row(doc)
    assert r["status"] == "deleted" and r["wrapped"] is None and r["deleted_at"] is not None
    assert r["purged_at"] is None  # files go at the next sweep
    assert client.get("/api/vault/documents", headers=me).json() == []
    assert get_file(client, doc, me).status_code == 410  # never decrypted again
    with connect() as conn:
        assert conn.execute(
            "SELECT outcome FROM vault.audit_event WHERE action = 'delete' AND document_id = %s", (doc,)
        ).fetchall() == [("allowed",)]


def test_worker_purges_every_version(client, s3):
    me = youth()
    doc = add_document(client, s3, me)
    vault_key = row(doc)["vault_key"]
    s3.put_object(Bucket=VAULT, Key=vault_key, Body=b"an older copy")  # a second version
    s3.delete_object(Bucket=VAULT, Key=vault_key)  # and a delete marker
    assert len(versions(s3, VAULT, vault_key)) == 2

    delete(client, doc, me)
    assert doc in purge(s3)
    assert versions(s3, VAULT, vault_key) == []
    assert row(doc)["purged_at"] is not None
    assert doc not in purge(s3)  # nothing left to do


def test_restoring_a_database_backup_after_the_purge_opens_nothing(client, s3):
    """§12 deletion test: the backup still has the key, but every S3 version is gone."""
    me = youth()
    doc = add_document(client, s3, me)
    before = row(doc)  # what a database backup taken before the delete would hold
    delete(client, doc, me)
    purge(s3)

    with connect() as conn:  # restore that backup's row
        conn.execute(
            "UPDATE vault.document SET status = 'ready', wrapped_document_key = %s, deleted_at = NULL,"
            " purged_at = NULL WHERE id = %s",
            (before["wrapped"], doc),
        )
    r = get_file(client, doc, me)
    assert r.status_code == 410 and not r.content.startswith(b"%PDF")
    assert versions(s3, VAULT, before["vault_key"]) == []


def test_rejected_or_unscanned_upload_can_be_deleted_and_its_quarantine_copy_goes(client, s3):
    me = youth()
    doc = add_document(client, s3, me, ready=False)  # still being checked
    quarantine_key = row(doc)["quarantine_key"]
    assert versions(s3, QUARANTINE, quarantine_key)
    assert delete(client, doc, me).status_code == 204
    purge(s3)
    assert versions(s3, QUARANTINE, quarantine_key) == []


def test_delete_stops_shares_of_that_document_but_not_category_shares(client, s3):
    me, sup = youth(), supporter()
    doc = add_document(client, s3, me, title="Lease")
    other = add_document(client, s3, me, title="Old lease")
    share(client, me, code=new_code(client, sup), document_id=doc)
    share(client, me, code=new_code(client, sup), category="housing")
    assert get_file(client, doc, sup).status_code == 200

    delete(client, doc, me)
    assert get_file(client, doc, sup).status_code == 403
    assert shared_titles(client, sup) == {"Maria": ["Old lease"]}  # the category share still works
    shares = client.get("/api/vault/shares", headers=me).json()["shares"]
    assert [s["category"] for s in shares] == ["housing"]
    assert get_file(client, other, sup).status_code == 200


def test_cannot_delete_someone_elses_document(client, s3):
    doc = add_document(client, s3, youth())
    assert delete(client, doc, youth()).status_code == 404
    assert delete(client, uuid.uuid4(), youth()).status_code == 404
    assert row(doc)["status"] == "ready"


def test_deleting_twice_is_harmless(client, s3):
    me = youth()
    doc = add_document(client, s3, me)
    assert delete(client, doc, me).status_code == 204
    assert delete(client, doc, me).status_code == 204
    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM vault.audit_event WHERE action = 'delete' AND document_id = %s", (doc,)
        ).fetchone()[0] == 1


def test_supporters_and_staff_cannot_delete(client, s3):
    doc = add_document(client, s3, youth())
    assert delete(client, doc, supporter()).status_code == 403
    assert delete(client, doc, {"Authorization": "Bearer stub:alex:admin:staff"}).status_code == 403
    assert row(doc)["status"] == "ready"
