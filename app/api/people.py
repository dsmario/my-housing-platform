"""The person behind a sign-in.

A Cognito account maps to one identity.account_subject through its "sub"
(identity_provider_subject). Youth get their records the first time a route
needs them. Only the sub is stored: no name, username or email.
"""

from dataclasses import dataclass

import psycopg
from fastapi import Depends, HTTPException

from db import connect
from identity import Principal, current_principal


@dataclass(frozen=True)
class Youth:
    subject_id: str
    pseudonym_id: str


def youth_for(conn: psycopg.Connection, principal: Principal) -> Youth:
    """Finds or creates the youth records for this sign-in. The caller commits."""
    conn.execute(
        "INSERT INTO identity.account_subject (subject_type, identity_provider_subject)"
        " VALUES ('youth', %s) ON CONFLICT (identity_provider_subject) DO NOTHING",
        (principal.subject,),
    )
    # The row lock makes a second first-request wait, so it can't add a second profile.
    subject_id, subject_type, state = conn.execute(
        "SELECT id, subject_type, account_state FROM identity.account_subject"
        " WHERE identity_provider_subject = %s FOR UPDATE",
        (principal.subject,),
    ).fetchone()
    if subject_type != "youth" or state != "active":
        raise HTTPException(403, "This account can't use this feature.")

    row = conn.execute(
        "SELECT subject_pseudonym_id FROM identity.youth_profile WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()
    if row is None:
        pseudonym_id = conn.execute(
            "INSERT INTO identity.subject_pseudonym (subject_id, pseudonym_purpose)"
            " VALUES (%s, 'platform') RETURNING id",
            (subject_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO identity.youth_profile (subject_id, subject_pseudonym_id) VALUES (%s, %s)",
            (subject_id, pseudonym_id),
        )
    else:
        pseudonym_id = row[0]
    return Youth(subject_id=str(subject_id), pseudonym_id=str(pseudonym_id))


def current_youth(principal: Principal = Depends(current_principal)) -> Youth:
    """Route dependency: a youth from the users pool, with their records."""
    if principal.pool != "users" or "youth" not in principal.groups:
        raise HTTPException(403, "Only youth accounts can use this feature.")
    with connect() as conn:
        return youth_for(conn, principal)
