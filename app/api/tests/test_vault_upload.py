"""Doc Vault upload: limits, the presigned POST, ownership, and audit rows."""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from db import connect

PDF = {"title": "Passport", "category": "id_documents", "content_type": "application/pdf", "size_bytes": 2048}


def youth() -> dict:
    """Headers for a new youth (stub token), so each test has its own quota."""
    return {"Authorization": f"Bearer stub:youth-{uuid.uuid4().hex[:10]}:youth", "User-Agent": "pytest-browser"}


def pseudonym(headers: dict) -> str | None:
    username = headers["Authorization"].split(":")[1]
    with connect() as conn:
        row = conn.execute(
            "SELECT p.subject_pseudonym_id FROM identity.youth_profile p"
            " JOIN identity.account_subject s ON s.id = p.subject_id"
            " WHERE s.identity_provider_subject = %s",
            (f"stub-{username}",),
        ).fetchone()
    return str(row[0]) if row else None


def audit_rows(headers: dict) -> list[tuple]:
    with connect() as conn:
        return conn.execute(
            "SELECT action, outcome, document_id, user_agent, detail FROM vault.audit_event"
            " WHERE youth_pseudonym_id = %s ORDER BY id",
            (pseudonym(headers),),
        ).fetchall()


def start(client, headers, **changes):
    return client.post("/api/vault/uploads", json={**PDF, **changes}, headers=headers)


# The upload link ---------------------------------------------------------------


def test_upload_link_is_locked_to_one_file(client):
    h = youth()
    r = start(client, h)
    assert r.status_code == 201
    body = r.json()
    doc = body["document_id"]
    upload = body["upload"]

    assert upload["url"] == "http://storage.test:8333/test-quarantine"
    assert upload["fields"]["key"] == f"uploads/{doc}"
    assert upload["fields"]["Content-Type"] == "application/pdf"
    assert body["expires_in"] == 300

    policy = json.loads(base64.b64decode(upload["fields"]["policy"]))
    conditions = policy["conditions"]
    assert {"bucket": "test-quarantine"} in conditions
    assert {"key": f"uploads/{doc}"} in conditions
    assert {"Content-Type": "application/pdf"} in conditions
    assert ["content-length-range", 2048, 2048] in conditions
    expires = datetime.fromisoformat(policy["expiration"].replace("Z", "+00:00"))
    assert expires <= datetime.now(timezone.utc) + timedelta(seconds=305)


def test_upload_creates_the_document_and_an_audit_row(client):
    h = youth()
    doc = start(client, h).json()["document_id"]
    with connect() as conn:
        row = conn.execute(
            "SELECT owner_pseudonym_id::text, status, title, document_category_key, size_bytes, quarantine_object_key,"
            "       vault_object_key, wrapped_document_key FROM vault.document WHERE id = %s",
            (doc,),
        ).fetchone()
    assert row == (pseudonym(h), "uploading", "Passport", "id_documents", 2048, f"uploads/{doc}", None, None)

    [(action, outcome, doc_id, agent, detail)] = audit_rows(h)
    assert (action, outcome, str(doc_id), agent) == ("upload", "allowed", doc, "pytest-browser")
    assert "Passport" not in json.dumps(detail)  # titles stay out of the audit log


# Refusals ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes, status",
    [
        ({"category": "medical"}, 422),
        ({"content_type": "text/html"}, 415),
        ({"content_type": "application/x-msdownload"}, 415),
        ({"size_bytes": 15 * 1024 * 1024 + 1}, 413),
    ],
)
def test_refused_uploads_are_audited(client, changes, status):
    h = youth()
    assert start(client, h, **changes).status_code == status
    [(action, outcome, doc_id, _, detail)] = audit_rows(h)
    assert (action, outcome, doc_id, detail["reason"]) == ("upload", "denied", None, status)


@pytest.mark.parametrize("changes", [{"size_bytes": 0}, {"title": ""}, {"title": "x" * 201}])
def test_malformed_requests_are_refused(client, changes):
    assert start(client, youth(), **changes).status_code == 422


@pytest.mark.parametrize("content_type", ["application/pdf", "image/jpeg", "image/png", "image/heic"])
def test_allowed_types(client, content_type):
    assert start(client, youth(), content_type=content_type).status_code == 201


def test_quota_counts_live_documents_only(client, monkeypatch):
    monkeypatch.setenv("VAULT_QUOTA_BYTES", "5000")
    h = youth()
    first = start(client, h).json()["document_id"]
    assert start(client, h).status_code == 201  # 4096 of 5000
    assert start(client, h).status_code == 413  # would be 6144
    with connect() as conn:
        conn.execute("UPDATE vault.document SET status = 'rejected' WHERE id = %s", (first,))
    assert start(client, h).status_code == 201  # rejected files don't count


def test_upload_rate_is_limited(client, monkeypatch):
    monkeypatch.setenv("VAULT_UPLOADS_PER_HOUR", "2")
    h = youth()
    assert [start(client, h).status_code for _ in range(3)] == [201, 201, 429]


def test_limits_are_settings(client, monkeypatch):
    monkeypatch.setenv("VAULT_ALLOWED_TYPES", "application/pdf")
    monkeypatch.setenv("VAULT_MAX_BYTES", "1000")
    h = youth()
    assert start(client, h, content_type="image/png").status_code == 415
    assert start(client, h, size_bytes=1001).status_code == 413
    assert start(client, h, size_bytes=1000).status_code == 201


@pytest.mark.parametrize(
    "headers, status",
    [
        ({}, 401),
        ({"Authorization": "Bearer stub:sam:supporter"}, 403),
        ({"Authorization": "Bearer stub:alex:youth:staff"}, 403),
    ],
)
def test_only_youth_can_upload(client, headers, status):
    assert start(client, headers).status_code == status


# Finishing an upload -------------------------------------------------------------


def test_finish_moves_to_scanning_once(client):
    h = youth()
    doc = start(client, h).json()["document_id"]
    for _ in range(2):
        r = client.post(f"/api/vault/uploads/{doc}/done", headers=h)
        assert r.status_code == 200 and r.json() == {"document_id": doc, "status": "scanning"}


def test_cannot_finish_someone_elses_upload(client):
    doc = start(client, youth()).json()["document_id"]
    other = youth()
    assert client.post(f"/api/vault/uploads/{doc}/done", headers=other).status_code == 404
    assert client.post(f"/api/vault/uploads/{uuid.uuid4()}/done", headers=other).status_code == 404
    assert client.post("/api/vault/uploads/not-an-id/done", headers=other).status_code == 422
    with connect() as conn:
        assert conn.execute("SELECT status FROM vault.document WHERE id = %s", (doc,)).fetchone()[0] == "uploading"


# Who the audit log records -------------------------------------------------------


@pytest.mark.parametrize(
    "secret, expected_ip, expected_agent",
    [
        ("test-forward-secret", "203.0.113.7", "Youth's phone browser"),  # from the web server
        ("wrong-secret", None, "pytest-browser"),  # made-up details are ignored
        (None, None, "pytest-browser"),
    ],
)
def test_audit_records_the_person_only_when_the_web_server_vouches(client, secret, expected_ip, expected_agent):
    h = {**youth(), "X-MHP-Client-IP": "203.0.113.7", "X-MHP-Client-UA": "Youth's phone browser"}
    if secret:
        h["X-MHP-Web-Secret"] = secret
    start(client, h)
    with connect() as conn:
        row = conn.execute(
            "SELECT host(ip_address), user_agent FROM vault.audit_event WHERE youth_pseudonym_id = %s",
            (pseudonym(h),),
        ).fetchone()
    assert row == (expected_ip, expected_agent)


def test_aws_upload_link_uses_the_regional_address(monkeypatch):
    import vault

    monkeypatch.setenv("VAULT_S3_PUBLIC_ENDPOINT", "")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    vault.presign_client.cache_clear()
    try:
        url = vault.presign_client().generate_presigned_post("test-quarantine", "uploads/x")["url"]
    finally:
        vault.presign_client.cache_clear()
    assert url == "https://test-quarantine.s3.us-west-2.amazonaws.com/"
