"""Doc Vault audit log: hash chain, append-only rules, and table constraints."""

import psycopg
import pytest


def add_events(conn, n: int = 3) -> None:
    for i in range(n):
        conn.execute(
            "INSERT INTO vault.audit_event (action, outcome, user_agent) VALUES ('view', %s, %s)",
            ("allowed" if i % 2 == 0 else "denied", f"test-{i}" if i != 1 else None),
        )


def test_chain_is_intact_after_inserts(conn):
    add_events(conn)
    assert conn.execute("SELECT vault.verify_audit_chain()").fetchone()[0] is None
    ids = [r[0] for r in conn.execute("SELECT id FROM vault.audit_event ORDER BY id")]
    assert ids == sorted(ids) and len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE vault.audit_event SET outcome = 'allowed'",
        "DELETE FROM vault.audit_event",
        "TRUNCATE vault.audit_event",
    ],
)
def test_audit_log_is_append_only(conn, statement):
    add_events(conn, 1)
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute(statement)


def test_tampering_is_detected(conn):
    add_events(conn)
    last_id = conn.execute("SELECT max(id) FROM vault.audit_event").fetchone()[0]
    conn.execute("ALTER TABLE vault.audit_event DISABLE TRIGGER audit_event_append_only")
    conn.execute("UPDATE vault.audit_event SET user_agent = 'tampered' WHERE id = %s", (last_id,))
    assert conn.execute("SELECT vault.verify_audit_chain()").fetchone()[0] == last_id


def test_audit_seal_is_append_only(conn):
    conn.execute(
        "INSERT INTO vault.audit_seal (first_event_id, last_event_id, head_hash, object_key)"
        " VALUES (1, 1, '\\x00', 'test')"
    )
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("DELETE FROM vault.audit_seal")


def pseudonym(conn) -> str:
    return conn.execute("SELECT id FROM identity.subject_pseudonym LIMIT 1").fetchone()[0]


def test_ready_document_needs_a_key(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO vault.document"
            " (owner_pseudonym_id, document_category_key, title, content_type, size_bytes, status)"
            " VALUES (%s, 'id_documents', 'x', 'application/pdf', 10, 'ready')",
            (pseudonym(conn),),
        )


def test_deleted_document_has_no_key(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO vault.document (owner_pseudonym_id, document_category_key, title, content_type,"
            " size_bytes, status, wrapped_document_key, deleted_at)"
            " VALUES (%s, 'id_documents', 'x', 'application/pdf', 10, 'deleted', '\\x01', now())",
            (pseudonym(conn),),
        )


def test_destroyed_data_key_needs_a_date(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO vault.youth_data_key (youth_pseudonym_id, wrapped_key, key_provider, wrapping_key_id)"
            " VALUES (%s, NULL, 'local', 'dev')",
            (pseudonym(conn),),
        )


def test_youth_cannot_share_with_themselves(conn):
    p = pseudonym(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO identity.share_grant (youth_pseudonym_id, grantee_pseudonym_id, purpose_code)"
            " VALUES (%s, %s, 'x')",
            (p, p),
        )
