"""Doc Vault sharing (§6): codes, shares by document or category, immediate revoke, audit."""

import uuid

import pytest
from test_vault_download import add_document, get_file, s3  # noqa: F401 - s3 is a fixture

from db import connect


def youth() -> dict:
    return {"Authorization": f"Bearer stub:youth-{uuid.uuid4().hex[:10]}:youth"}


def supporter() -> dict:
    return {"Authorization": f"Bearer stub:sup-{uuid.uuid4().hex[:10]}:supporter"}


def new_code(client, headers, name="Ms. Lopez") -> str:
    r = client.post("/api/vault/share-codes", json={"my_name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["code"]


def share(client, headers, **body):
    body.setdefault("my_name", "Maria")
    for k in ("document_id", "supporter"):
        if k in body:
            body[k] = str(body[k])
    return client.post("/api/vault/shares", json=body, headers=headers)


def shared_titles(client, headers) -> dict[str, list[str]]:
    groups = client.get("/api/vault/shared", headers=headers).json()["from"]
    return {g["from"]: sorted(d["title"] for d in g["documents"]) for g in groups}


def audit_rows(action: str, share_id: str | None = None, document_id: str | None = None) -> list[tuple]:
    with connect() as conn:
        return conn.execute(
            "SELECT outcome, detail->>'reason', share_grant_id::text FROM vault.audit_event"
            " WHERE action = %s AND (%s::uuid IS NULL OR share_grant_id = %s::uuid)"
            "   AND (%s::uuid IS NULL OR document_id = %s::uuid) ORDER BY id",
            (action, share_id, share_id, document_id, document_id),
        ).fetchall()


# Codes -------------------------------------------------------------------------------


def test_code_looks_right_and_is_stored_only_as_a_hash(client):
    sup = supporter()
    code = new_code(client, sup)
    assert len(code) == 9 and code[4] == "-"
    with connect() as conn:
        stored = conn.execute("SELECT code_hash FROM vault.share_code ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    assert code.replace("-", "").encode() not in bytes(stored)


def test_supporter_needs_a_name_first(client):
    r = client.post("/api/vault/share-codes", json={}, headers=supporter())
    assert r.status_code == 422 and r.json()["detail"] == "Add your name first."


def test_open_codes_are_limited(client):
    sup = supporter()
    for _ in range(5):
        new_code(client, sup)
    assert client.post("/api/vault/share-codes", json={}, headers=sup).status_code == 429


# Sharing and seeing ------------------------------------------------------------------


def test_share_one_document_then_supporter_downloads_it(client, s3):
    me, sup = youth(), supporter()
    lease = add_document(client, s3, me, title="Lease")
    add_document(client, s3, me, title="Not shared")
    r = share(client, me, code=new_code(client, sup), document_id=lease)
    assert r.status_code == 201
    share_id = r.json()["share_id"]

    assert shared_titles(client, sup) == {"Maria": ["Lease"]}
    assert get_file(client, lease, sup).status_code == 200
    assert audit_rows("download", share_id) == [("allowed", None, share_id)]
    assert audit_rows("share", share_id) == [("allowed", None, share_id)]


def test_code_works_with_any_case_spacing_or_dash(client, s3):
    me, sup = youth(), supporter()
    doc = add_document(client, s3, me)
    code = new_code(client, sup).lower().replace("-", " ")
    assert share(client, me, code=code, document_id=doc).status_code == 201


def test_stop_sharing_takes_effect_on_the_next_request(client, s3):
    me, sup = youth(), supporter()
    doc = add_document(client, s3, me)
    share_id = share(client, me, code=new_code(client, sup), document_id=doc).json()["share_id"]
    assert get_file(client, doc, sup).status_code == 200

    assert client.delete(f"/api/vault/shares/{share_id}", headers=me).status_code == 204
    assert get_file(client, doc, sup).status_code == 403
    assert shared_titles(client, sup) == {}
    assert audit_rows("revoke", share_id) == [("allowed", None, share_id)]
    assert ("denied", "not shared with requester", None) in audit_rows("download", document_id=doc)
    assert client.delete(f"/api/vault/shares/{share_id}", headers=me).status_code == 204  # again: no change
    assert len(audit_rows("revoke", share_id)) == 1


def test_category_share_includes_documents_added_later_and_nothing_else(client, s3):
    me, other_youth, sup = youth(), youth(), supporter()
    share(client, me, code=new_code(client, sup), category="housing")
    later = add_document(client, s3, me, title="New lease")  # add_document uses the housing category
    with connect() as conn:  # same youth, another category
        conn.execute(
            "UPDATE vault.document SET document_category_key = 'income' WHERE id = %s",
            (add_document(client, s3, me, title="Pay stub"),),
        )
    someone_else = add_document(client, s3, other_youth, title="Someone else's lease")

    assert shared_titles(client, sup) == {"Maria": ["New lease"]}
    assert get_file(client, later, sup).status_code == 200
    assert get_file(client, someone_else, sup).status_code == 403


def test_share_again_with_someone_known_without_a_code(client, s3):
    me, sup = youth(), supporter()
    first, second = add_document(client, s3, me, title="A"), add_document(client, s3, me, title="B")
    share(client, me, code=new_code(client, sup), document_id=first)
    people = client.get("/api/vault/shares", headers=me).json()["people"]
    assert [p["name"] for p in people] == ["Ms. Lopez"]
    assert share(client, me, supporter=people[0]["supporter"], document_id=second).status_code == 201
    assert shared_titles(client, sup) == {"Maria": ["A", "B"]}


def test_sharing_the_same_thing_twice_changes_nothing(client, s3):
    me, sup = youth(), supporter()
    doc = add_document(client, s3, me)
    first = share(client, me, code=new_code(client, sup), document_id=doc).json()
    again = share(client, me, supporter=first["supporter"], document_id=doc).json()
    assert again["share_id"] == first["share_id"]
    assert len(client.get("/api/vault/shares", headers=me).json()["shares"]) == 1


def test_youth_sees_current_shares_with_names(client, s3):
    me, sup = youth(), supporter()
    doc = add_document(client, s3, me, title="Lease")
    share(client, me, code=new_code(client, sup), document_id=doc)
    share(client, me, code=new_code(client, sup), category="id_documents")
    body = client.get("/api/vault/shares", headers=me).json()
    assert body["my_name"] == "Maria"
    assert sorted((s["supporter_name"], s["document_title"] or "", s["category"] or "") for s in body["shares"]) == [
        ("Ms. Lopez", "", "id_documents"),
        ("Ms. Lopez", "Lease", ""),
    ]


# Codes that don't work -----------------------------------------------------------------


def test_code_works_once(client, s3):
    sup = supporter()
    code = new_code(client, sup)
    first, second = youth(), youth()
    assert share(client, first, code=code, document_id=add_document(client, s3, first)).status_code == 201
    r = share(client, second, code=code, document_id=add_document(client, s3, second))
    assert r.status_code == 400 and r.json()["detail"] == "That code didn't work. Check it and try again."


def test_expired_code_does_not_work(client, s3):
    me, sup = youth(), supporter()
    code = new_code(client, sup)
    with connect() as conn:
        conn.execute("UPDATE vault.share_code SET expires_at = now() - interval '1 minute' WHERE used_at IS NULL")
    assert share(client, me, code=code, document_id=add_document(client, s3, me)).status_code == 400


def test_guessing_codes_is_limited_and_audited(client, s3):
    me = youth()
    doc = add_document(client, s3, me)
    statuses = [share(client, me, code="AAAA-AAAA", document_id=doc).status_code for _ in range(11)]
    assert statuses == [400] * 10 + [429]
    assert sum(1 for o, reason, _ in audit_rows("share", document_id=doc) if reason == "bad code") == 10


# What can't be shared, or seen ---------------------------------------------------------------


def test_cannot_share_someone_elses_document(client, s3):
    owner, me, sup = youth(), youth(), supporter()
    doc = add_document(client, s3, owner)
    assert share(client, me, code=new_code(client, sup), document_id=doc).status_code == 404


def test_cannot_pick_a_stranger_without_a_code(client, s3):
    me, stranger = youth(), supporter()
    new_code(client, stranger)  # makes the stranger's records
    with connect() as conn:
        stranger_id = conn.execute(
            "SELECT p.subject_pseudonym_id FROM identity.adult_profile p"
            " JOIN identity.account_subject a ON a.id = p.subject_id ORDER BY a.created_at DESC LIMIT 1"
        ).fetchone()[0]
    assert share(client, me, supporter=stranger_id, document_id=add_document(client, s3, me)).status_code == 404


def test_another_supporter_sees_nothing(client, s3):
    me, sup, other = youth(), supporter(), supporter()
    doc = add_document(client, s3, me)
    share(client, me, code=new_code(client, sup), document_id=doc)
    new_code(client, other, name="Mr. Kim")
    assert shared_titles(client, other) == {}
    assert get_file(client, doc, other).status_code == 403


def test_closed_youth_account_stops_sharing(client, s3):
    me, sup = youth(), supporter()
    doc = add_document(client, s3, me)
    share(client, me, code=new_code(client, sup), document_id=doc)
    with connect() as conn:
        conn.execute(
            "UPDATE identity.account_subject SET account_state = 'closed' WHERE identity_provider_subject = %s",
            ("stub-" + me["Authorization"].split(":")[1],),
        )
    assert get_file(client, doc, sup).status_code == 403
    assert shared_titles(client, sup) == {}


def test_supporter_opening_the_list_is_recorded_as_a_view(client, s3):
    me, sup = youth(), supporter()
    share(client, me, code=new_code(client, sup), document_id=add_document(client, s3, me))
    with connect() as conn:
        before = conn.execute("SELECT count(*) FROM vault.audit_event WHERE action = 'view'").fetchone()[0]
        client.get("/api/vault/shared", headers=sup)
        after = conn.execute("SELECT count(*) FROM vault.audit_event WHERE action = 'view'").fetchone()[0]
    assert after == before + 1


@pytest.mark.parametrize(
    "who, method, path, status",
    [
        ("supporter", "post", "/api/vault/shares", 403),  # no re-sharing
        ("supporter", "post", "/api/vault/uploads", 403),
        ("supporter", "get", "/api/vault/documents", 403),
        ("supporter", "get", "/api/vault/shares", 403),
        ("youth", "get", "/api/vault/shared", 403),
        ("youth", "post", "/api/vault/share-codes", 403),
        ("staff", "get", "/api/vault/shared", 403),
        ("nobody", "post", "/api/vault/share-codes", 401),
    ],
)
def test_who_can_call_what(client, who, method, path, status):
    headers = {
        "youth": youth(),
        "supporter": supporter(),
        "staff": {"Authorization": "Bearer stub:alex:supporter:staff"},
        "nobody": {},
    }[who]
    assert getattr(client, method)(path, headers=headers, **({"json": {}} if method == "post" else {})).status_code == status


def test_profile(client):
    sup = supporter()
    assert client.get("/api/vault/profile", headers=sup).json() == {"role": "supporter", "display_name": None}
    new_code(client, sup, name="  Ms.   Lopez ")
    assert client.get("/api/vault/profile", headers=sup).json() == {"role": "supporter", "display_name": "Ms. Lopez"}


def test_refreshing_the_list_records_one_view_per_few_minutes(client, s3):
    me, sup = youth(), supporter()
    share(client, me, code=new_code(client, sup), document_id=add_document(client, s3, me))
    with connect() as conn:
        before = conn.execute("SELECT count(*) FROM vault.audit_event WHERE action = 'view'").fetchone()[0]
        for _ in range(4):
            client.get("/api/vault/shared", headers=sup)
        after = conn.execute("SELECT count(*) FROM vault.audit_event WHERE action = 'view'").fetchone()[0]
    assert after == before + 1


def test_many_refusals_for_one_person_raise_the_alarm_once(client, s3, caplog):
    doc = add_document(client, s3, youth())
    guesser = supporter()
    new_code(client, guesser)  # makes the supporter's records
    with caplog.at_level("WARNING", logger="vault"):
        for _ in range(4):
            assert get_file(client, doc, guesser).status_code == 403
        assert "VAULT_REFUSALS_ONE_PERSON" not in caplog.text
        get_file(client, doc, guesser)  # the 5th
        get_file(client, doc, guesser)  # the 6th doesn't repeat it
    assert caplog.text.count("VAULT_REFUSALS_ONE_PERSON") == 1
