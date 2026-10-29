"""Linking a sign-in to a youth: created once, nothing personal stored, others refused."""

import threading
import uuid

import pytest
from fastapi import HTTPException

from db import connect
from identity import Principal
from people import current_youth


def principal(groups=("youth",), pool="users", subject=None) -> Principal:
    return Principal(
        subject=subject or f"test-sub-{uuid.uuid4()}",
        username="someone@example.org",
        groups=tuple(groups),
        pool=pool,
    )


def records(sub: str) -> dict:
    with connect() as conn:
        subject = conn.execute(
            "SELECT id, subject_type, display_name FROM identity.account_subject"
            " WHERE identity_provider_subject = %s",
            (sub,),
        ).fetchone()
        if subject is None:
            return {"subject": None, "pseudonyms": 0, "profiles": 0}
        return {
            "subject": subject,
            "pseudonyms": conn.execute(
                "SELECT count(*) FROM identity.subject_pseudonym WHERE subject_id = %s", (subject[0],)
            ).fetchone()[0],
            "profiles": conn.execute(
                "SELECT count(*) FROM identity.youth_profile WHERE subject_id = %s", (subject[0],)
            ).fetchone()[0],
        }


def test_first_visit_creates_the_youth_once():
    p = principal()
    first = current_youth(p)
    again = current_youth(p)
    assert first == again
    r = records(p.subject)
    assert r["subject"][1] == "youth"
    assert (r["pseudonyms"], r["profiles"]) == (1, 1)


def test_nothing_personal_is_stored():
    p = principal()
    youth = current_youth(p)
    with connect() as conn:
        row = conn.execute(
            "SELECT display_name, identity_provider_subject FROM identity.account_subject WHERE id = %s",
            (youth.subject_id,),
        ).fetchone()
    assert row == (None, p.subject)


@pytest.mark.parametrize(
    "groups, pool",
    [
        (("supporter",), "users"),
        ((), "users"),
        (("youth",), "staff"),  # a staff account is never a youth, whatever its groups
        (("admin",), "staff"),
    ],
)
def test_other_accounts_are_refused_and_nothing_is_created(groups, pool):
    p = principal(groups, pool)
    with pytest.raises(HTTPException) as e:
        current_youth(p)
    assert e.value.status_code == 403
    assert records(p.subject)["subject"] is None


def test_a_sign_in_linked_to_someone_else_is_refused():
    sub = f"test-sub-{uuid.uuid4()}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO identity.account_subject (subject_type, identity_provider_subject)"
            " VALUES ('supportive_adult', %s)",
            (sub,),
        )
    with pytest.raises(HTTPException) as e:
        current_youth(principal(subject=sub))
    assert e.value.status_code == 403
    assert records(sub)["profiles"] == 0


def test_closed_account_is_refused():
    p = principal()
    current_youth(p)
    with connect() as conn:
        conn.execute(
            "UPDATE identity.account_subject SET account_state = 'closed' WHERE identity_provider_subject = %s",
            (p.subject,),
        )
    with pytest.raises(HTTPException) as e:
        current_youth(p)
    assert e.value.status_code == 403


def test_simultaneous_first_visits_make_one_record():
    p = principal()
    results, errors = [], []

    def visit():
        try:
            results.append(current_youth(p))
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=visit) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(set(results)) == 1
    r = records(p.subject)
    assert (r["pseudonyms"], r["profiles"]) == (1, 1)
