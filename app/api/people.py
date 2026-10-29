"""The person behind a sign-in.

A Cognito account maps to one identity.account_subject through its "sub"
(identity_provider_subject). Youth and supporters get their records the first
time a route needs them. Only the sub is stored, plus a display name the person
chooses to show the people they share with: no username or email.
"""

from dataclasses import dataclass

import psycopg
from fastapi import Depends, HTTPException

from db import connect
from identity import Principal, current_principal

# Users-pool group -> (account_subject.subject_type, profile table, adult_role)
ROLES = {
    "youth": ("youth", "youth_profile", None),
    "supporter": ("supportive_adult", "adult_profile", "supportive_adult"),
}


@dataclass(frozen=True)
class Youth:
    subject_id: str
    pseudonym_id: str


@dataclass(frozen=True)
class Supporter:
    subject_id: str
    pseudonym_id: str


def person_for(conn: psycopg.Connection, principal: Principal, group: str) -> tuple[str, str]:
    """Finds or creates (subject_id, pseudonym_id) for this sign-in in this role. The caller commits."""
    subject_type, profile, adult_role = ROLES[group]
    conn.execute(
        "INSERT INTO identity.account_subject (subject_type, identity_provider_subject)"
        " VALUES (%s, %s) ON CONFLICT (identity_provider_subject) DO NOTHING",
        (subject_type, principal.subject),
    )
    # The row lock makes a second first-request wait, so it can't add a second profile.
    subject_id, existing_type, state = conn.execute(
        "SELECT id, subject_type, account_state FROM identity.account_subject"
        " WHERE identity_provider_subject = %s FOR UPDATE",
        (principal.subject,),
    ).fetchone()
    if existing_type != subject_type or state != "active":
        raise HTTPException(403, "This account can't use this feature.")

    row = conn.execute(
        f"SELECT subject_pseudonym_id FROM identity.{profile} WHERE subject_id = %s", (subject_id,)
    ).fetchone()
    if row is not None:
        return str(subject_id), str(row[0])
    pseudonym_id = conn.execute(
        "INSERT INTO identity.subject_pseudonym (subject_id, pseudonym_purpose)"
        " VALUES (%s, 'platform') RETURNING id",
        (subject_id,),
    ).fetchone()[0]
    if adult_role:
        conn.execute(
            "INSERT INTO identity.adult_profile (subject_id, subject_pseudonym_id, adult_role) VALUES (%s, %s, %s)",
            (subject_id, pseudonym_id, adult_role),
        )
    else:
        conn.execute(
            "INSERT INTO identity.youth_profile (subject_id, subject_pseudonym_id) VALUES (%s, %s)",
            (subject_id, pseudonym_id),
        )
    return str(subject_id), str(pseudonym_id)


def youth_for(conn: psycopg.Connection, principal: Principal) -> Youth:
    """Finds or creates the youth records for this sign-in. The caller commits."""
    return Youth(*person_for(conn, principal, "youth"))


def in_group(principal: Principal, group: str) -> bool:
    return principal.pool == "users" and group in principal.groups


def current_youth(principal: Principal = Depends(current_principal)) -> Youth:
    """Route dependency: a youth from the users pool, with their records."""
    if not in_group(principal, "youth"):
        raise HTTPException(403, "Only youth accounts can use this feature.")
    with connect() as conn:
        return youth_for(conn, principal)


def current_supporter(principal: Principal = Depends(current_principal)) -> Supporter:
    """Route dependency: a supporter from the users pool, with their records."""
    if not in_group(principal, "supporter"):
        raise HTTPException(403, "Only supporter accounts can use this feature.")
    with connect() as conn:
        return Supporter(*person_for(conn, principal, "supporter"))


def current_reader(principal: Principal = Depends(current_principal)) -> Youth | Supporter:
    """Route dependency for reading documents: a youth (their own) or a supporter (shared with them)."""
    if in_group(principal, "youth"):
        return current_youth(principal)
    if in_group(principal, "supporter"):
        return current_supporter(principal)
    raise HTTPException(403, "This account can't use this feature.")


def display_name(conn: psycopg.Connection, subject_id: str) -> str | None:
    return conn.execute("SELECT display_name FROM identity.account_subject WHERE id = %s", (subject_id,)).fetchone()[0]


def set_display_name(conn: psycopg.Connection, subject_id: str, name: str) -> None:
    conn.execute("UPDATE identity.account_subject SET display_name = %s WHERE id = %s", (name, subject_id))
