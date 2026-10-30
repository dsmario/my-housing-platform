"""Unfinished uploads expire; the worker's audit chain check shows on Vault health and is retried soon if it fails."""

import os

import psycopg

from fakes import FakeS3
from test_vault_upload import start, youth

from db import connect
from vault_worker import check_audit_chain, expire_unfinished_uploads

QUARANTINE = os.environ["VAULT_QUARANTINE_BUCKET"]
ADMIN = {"Authorization": "Bearer stub:alex:admin:staff"}
HEALTH = "/api/admin/vault-health"


def started_hours_ago(client, headers, hours: float, status: str = "uploading") -> str:
    doc = start(client, headers).json()["document_id"]
    with connect() as conn:
        conn.execute(
            "UPDATE vault.document SET status = %s, status_changed_at = now() - make_interval(secs => %s)"
            " WHERE id = %s",
            (status, hours * 3600, doc),
        )
    return doc


def status(doc: str) -> str:
    with connect() as conn:
        return conn.execute("SELECT status FROM vault.document WHERE id = %s", (doc,)).fetchone()[0]


def expire(s3) -> list[str]:
    with connect() as conn:
        return expire_unfinished_uploads(s3, conn)


def test_upload_with_no_file_expires_after_an_hour(client):
    me = youth()
    old = started_hours_ago(client, me, 2)
    done_but_no_file = started_hours_ago(client, me, 2, "scanning")
    recent = started_hours_ago(client, me, 0.5)
    expired = expire(FakeS3())
    assert old in expired and done_but_no_file in expired and recent not in expired
    assert [status(d) for d in (old, done_but_no_file, recent)] == ["expired", "expired", "uploading"]


def test_upload_whose_file_arrived_is_left_for_its_scan(client):
    s3 = FakeS3()
    before = client.get(HEALTH, headers=ADMIN).json()["documents"]["stuck_checking"]
    doc = started_hours_ago(client, youth(), 2, "scanning")
    s3.put_object(Bucket=QUARANTINE, Key=f"uploads/{doc}", Body=b"%PDF")
    assert doc not in expire(s3)
    assert status(doc) == "scanning"
    # Vault health counts it as stuck, since anything else would have expired.
    assert client.get(HEALTH, headers=ADMIN).json()["documents"]["stuck_checking"] == before + 1


def test_expired_uploads_show_to_the_youth_and_dont_count_against_the_limit(client, monkeypatch):
    monkeypatch.setenv("VAULT_QUOTA_BYTES", "5000")
    me = youth()
    doc = started_hours_ago(client, me, 2)  # 2048 bytes
    start(client, me)  # 4096 of 5000
    assert start(client, me).status_code == 413
    expire(FakeS3())
    assert start(client, me).status_code == 201  # the expired one no longer counts
    listed = {d["document_id"]: d["status"] for d in client.get("/api/vault/documents", headers=me).json()}
    assert listed[doc] == "expired"
    assert client.delete(f"/api/vault/documents/{doc}", headers=me).status_code == 204  # can still delete it


def test_vault_health_shows_the_workers_chain_check(client):
    with connect() as conn:
        assert check_audit_chain(conn) is None
    audit = client.get(HEALTH, headers=ADMIN).json()["audit"]
    assert audit["chain_checked_at"] is not None
    assert audit["chain_broken_at"] is None
    assert audit["chain_check_overdue"] is False
    assert audit["chain_checked_through"] == audit["last_event_id"]


def test_an_old_chain_check_is_flagged(client):
    with connect() as conn:
        check_audit_chain(conn)
        conn.execute("UPDATE vault.audit_chain_check SET checked_at = now() - interval '3 hours'")
    assert client.get(HEALTH, headers=ADMIN).json()["audit"]["chain_check_overdue"] is True
    with connect() as conn:
        check_audit_chain(conn)  # leave a fresh one for other tests


def test_a_failed_chain_check_is_retried_soon(monkeypatch):
    import vault_worker

    assert vault_worker.run_audit_check() == vault_worker.AUDIT_CHECK_EVERY_SECONDS

    def missing_table():
        raise psycopg.errors.UndefinedTable('relation "vault.audit_chain_check" does not exist')

    monkeypatch.setattr(vault_worker, "connect", missing_table)
    assert vault_worker.run_audit_check() == vault_worker.AUDIT_CHECK_RETRY_SECONDS
