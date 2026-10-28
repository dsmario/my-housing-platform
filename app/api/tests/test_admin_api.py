"""Admin API: who gets in, what each table allows, and how values round-trip."""

import uuid

import psycopg
import pytest

ADMIN = {"Authorization": "Bearer stub:alex:admin:staff"}
RELEASES = "/api/admin/tables/planning.rules_corpus_release"


def key() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


# Access -------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers, status",
    [
        ({}, 401),
        ({"Authorization": "Bearer not-a-token"}, 401),
        ({"Authorization": "Bearer stub:yo:youth"}, 403),
        ({"Authorization": "Bearer stub:sam:supporter"}, 403),
        # An "admin" group from the users pool is still not staff.
        ({"Authorization": "Bearer stub:sneaky:admin:users"}, 403),
        ({"Authorization": "Bearer stub:pat:support:staff"}, 403),
        (ADMIN, 200),
    ],
)
def test_only_staff_admins_get_in(client, headers, status):
    assert client.get("/api/admin/tables", headers=headers).status_code == status


def test_me_reports_the_pool(client):
    assert client.get("/api/me", headers=ADMIN).json() == {"username": "alex", "groups": ["admin"], "pool": "staff"}


# Browsing -----------------------------------------------------------------


def test_lists_every_table_with_counts(client):
    tables = client.get("/api/admin/tables", headers=ADMIN).json()
    assert len(tables) == 29
    counts = {t["table"]: t["rows"] for t in tables}
    assert counts["identity.account_subject"] == 4


def test_unlisted_table_is_not_found(client):
    assert client.get("/api/admin/tables/public.messages", headers=ADMIN).status_code == 404
    assert client.get("/api/admin/tables/pg_catalog.pg_authid", headers=ADMIN).status_code == 404


def test_filter_follows_a_foreign_key(client):
    subjects = client.get("/api/admin/tables/identity.account_subject", headers=ADMIN).json()
    youth_id = next(r["id"] for r in subjects["rows"] if r["subject_type"] == "youth")
    r = client.get(f"/api/admin/tables/identity.subject_pseudonym?subject_id={youth_id}", headers=ADMIN).json()
    assert r["total"] == 1
    subject_col = next(c for c in r["columns"] if c["name"] == "subject_id")
    assert subject_col["references"] == "identity.account_subject"


def test_filter_on_unknown_column_is_refused(client):
    assert client.get("/api/admin/tables/identity.account_subject?bogus=1", headers=ADMIN).status_code == 422


def test_paging(client):
    r = client.get("/api/admin/tables/identity.account_subject?limit=1&offset=1", headers=ADMIN).json()
    assert r["total"] == 4 and len(r["rows"]) == 1


# View-only, hidden and totals-only -------------------------------------------


def test_view_only_tables_refuse_changes(client):
    table = "/api/admin/tables/identity.account_subject"
    row_id = client.get(table, headers=ADMIN).json()["rows"][0]["id"]
    assert client.post(table, json={"subject_type": "youth"}, headers=ADMIN).status_code == 403
    assert client.patch(f"{table}/{row_id}", json={"display_name": "x"}, headers=ADMIN).status_code == 403
    assert client.delete(f"{table}/{row_id}", headers=ADMIN).status_code == 403


def test_hidden_columns_never_leave_the_api(client, database):
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO vault.youth_data_key (youth_pseudonym_id, wrapped_key, key_provider, wrapping_key_id)"
            " SELECT id, '\\xdeadbeef', 'local', 'dev' FROM identity.subject_pseudonym"
            " WHERE NOT EXISTS (SELECT 1 FROM vault.youth_data_key k WHERE k.youth_pseudonym_id = subject_pseudonym.id)"
            " LIMIT 1"
        )
    r = client.get("/api/admin/tables/vault.youth_data_key", headers=ADMIN)
    assert r.json()["rows"][0]["wrapped_key"] == "[hidden]"
    assert "deadbeef" not in r.text


def test_hidden_columns_cannot_be_filtered(client):
    assert client.get("/api/admin/tables/vault.document?title=x", headers=ADMIN).status_code == 422


def test_audit_log_shows_totals_only(client):
    r = client.get("/api/admin/tables/vault.audit_event", headers=ADMIN).json()
    assert r["mode"] == "summary" and r["rows"] == [] and r["chain_broken_at"] is None
    assert client.get("/api/admin/tables/vault.audit_event/1", headers=ADMIN).status_code == 403


# Editing configuration --------------------------------------------------------


def test_create_update_delete_with_a_list(client):
    k = key()
    r = client.post(RELEASES, json={"release_key": k, "jurisdiction_keys": ["CA", 'San "Fran"', "a,b"]}, headers=ADMIN)
    assert r.status_code == 201
    row = r.json()
    assert row["jurisdiction_keys"] == ["CA", 'San "Fran"', "a,b"]

    r = client.patch(f"{RELEASES}/{row['id']}", json={"schema_release_id": "s-2"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["schema_release_id"] == "s-2"

    assert client.delete(f"{RELEASES}/{row['id']}", headers=ADMIN).status_code == 204
    assert client.get(f"{RELEASES}/{row['id']}", headers=ADMIN).status_code == 404


def test_json_round_trips(client):
    template = {"type": "object", "list": [1, 2], "nested": {"ok": True}}
    r = client.post(
        "/api/admin/tables/planning.task_result_schema",
        json={"release_key": key(), "schema_template": template},
        headers=ADMIN,
    )
    assert r.status_code == 201 and r.json()["schema_template"] == template


@pytest.mark.parametrize(
    "values, status, message",
    [
        ({"release_key": "dev-2026-10"}, 409, "duplicate key"),
        ({"release_key": "x", "rollback_release_id": "not-a-uuid"}, 422, "invalid input syntax for type uuid"),
        ({"release_key": "x", "no_such_column": 1}, 422, "Unknown column"),
        ({"release_key": "x", "jurisdiction_keys": "CA"}, 422, "Expected a list"),
        ({}, 422, "No values given"),
    ],
)
def test_bad_input_gets_a_clear_message(client, values, status, message):
    r = client.post(RELEASES, json=values, headers=ADMIN)
    assert r.status_code == status and message in r.json()["detail"]


def test_id_cannot_be_changed(client):
    row = client.post(RELEASES, json={"release_key": key()}, headers=ADMIN).json()
    assert client.patch(f"{RELEASES}/{row['id']}", json={"id": str(uuid.uuid4())}, headers=ADMIN).status_code == 422


def test_row_in_use_cannot_be_deleted(client):
    a = client.post(RELEASES, json={"release_key": key()}, headers=ADMIN).json()
    client.post(RELEASES, json={"release_key": key(), "rollback_release_id": a["id"]}, headers=ADMIN)
    r = client.delete(f"{RELEASES}/{a['id']}", headers=ADMIN)
    assert r.status_code == 409 and "Other records use this row" in r.json()["detail"]


def test_missing_row(client):
    missing = str(uuid.uuid4())
    assert client.patch(f"{RELEASES}/{missing}", json={"schema_release_id": "x"}, headers=ADMIN).status_code == 404
    assert client.delete(f"{RELEASES}/{missing}", headers=ADMIN).status_code == 404
