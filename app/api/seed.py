"""Test data for local and dev. Never runs in production (SEED_DATA unset).

Idempotent: safe to run on every start. All people and records are made up.
"""

from psycopg.types.json import Jsonb

from db import connect

MESSAGE = "Hello from the database. Web, API and Postgres are connected."


def seed_message(conn) -> None:
    if conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0:
        conn.execute("INSERT INTO messages (text) VALUES (%s)", (MESSAGE,))
        print("seed: inserted welcome message")
    else:
        print("seed: messages already present")


def add_person(conn, subject_type: str, display_name: str) -> tuple[str, str]:
    """Returns (subject_id, pseudonym_id)."""
    subject_id = conn.execute(
        "INSERT INTO identity.account_subject (subject_type, display_name)"
        " VALUES (%s, %s) RETURNING id",
        (subject_type, display_name),
    ).fetchone()[0]
    pseudonym_id = conn.execute(
        "INSERT INTO identity.subject_pseudonym (subject_id, pseudonym_purpose)"
        " VALUES (%s, 'platform') RETURNING id",
        (subject_id,),
    ).fetchone()[0]
    return subject_id, pseudonym_id


def seed_people_and_plans(conn) -> None:
    if conn.execute("SELECT count(*) FROM identity.account_subject").fetchone()[0] > 0:
        print("seed: people already present")
        return

    youth_a, youth_a_p = add_person(conn, "youth", "Test Youth A")
    youth_b, youth_b_p = add_person(conn, "youth", "Test Youth B")
    adult, adult_p = add_person(conn, "supportive_adult", "Test Supportive Adult")
    worker, worker_p = add_person(conn, "social_worker", "Test Social Worker")

    for subject_id, pseudonym_id, age_band in ((youth_a, youth_a_p, "17-18"), (youth_b, youth_b_p, "18-21")):
        conn.execute(
            "INSERT INTO identity.youth_profile"
            " (subject_id, subject_pseudonym_id, self_attested_age_band, care_status)"
            " VALUES (%s, %s, %s, 'in_care')",
            (subject_id, pseudonym_id, age_band),
        )
    for subject_id, pseudonym_id, role in ((adult, adult_p, "supportive_adult"), (worker, worker_p, "social_worker")):
        conn.execute(
            "INSERT INTO identity.adult_profile (subject_id, subject_pseudonym_id, adult_role)"
            " VALUES (%s, %s, %s)",
            (subject_id, pseudonym_id, role),
        )

    # Youth A shares their plan with the supportive adult.
    grant_id = conn.execute(
        "INSERT INTO identity.share_grant (youth_pseudonym_id, grantee_pseudonym_id, purpose_code)"
        " VALUES (%s, %s, 'supportive_adult') RETURNING id",
        (youth_a_p, adult_p),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO identity.share_grant_scope (share_grant_id, field_group_key)"
        " VALUES (%s, 'planning.plan')",
        (grant_id,),
    )

    conn.execute(
        "INSERT INTO planning.rules_input_release (release_key) VALUES ('dev-2026-10')"
    )
    conn.execute(
        "INSERT INTO planning.rules_corpus_release (release_key, jurisdiction_keys)"
        " VALUES ('dev-2026-10', '{CA}')"
    )
    conn.execute(
        "INSERT INTO planning.task_result_schema (release_key, schema_template) VALUES ('dev-2026-10', %s)",
        (Jsonb({"type": "object", "properties": {"done": {"type": "boolean"}}}),),
    )
    conn.execute(
        "INSERT INTO planning.readiness_assessment_schema (release_key, schema_template)"
        " VALUES ('dev-2026-10', %s)",
        (Jsonb({"type": "object", "properties": {"notes": {"type": "string"}}}),),
    )

    plan_id = conn.execute(
        "INSERT INTO planning.housing_readiness_plan (subject_pseudonym_id) VALUES (%s) RETURNING id",
        (youth_a_p,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO planning.plan_answer (plan_id, question_version_key, answer_state)"
        " VALUES (%s, 'stay_in_care.v1', 'answered')",
        (plan_id,),
    )
    conn.execute(
        "INSERT INTO planning.youth_document_status"
        " (subject_pseudonym_id, document_type_key, status, held_by)"
        " VALUES (%s, 'birth_certificate', 'missing', NULL),"
        "        (%s, 'state_id', 'have', 'youth')",
        (youth_a_p, youth_a_p),
    )
    print("seed: inserted test people, sharing and a plan")


def main() -> None:
    with connect() as conn:
        seed_message(conn)
        seed_people_and_plans(conn)


if __name__ == "__main__":
    main()
