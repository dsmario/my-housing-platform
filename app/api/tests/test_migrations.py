"""Migrations run up, down and up again on their own database."""

import uuid

import psycopg

from conftest import create_database, drop_database, migrate

SCHEMAS = ("identity", "planning", "vault")


def schema_table_counts(url: str) -> dict[str, int]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT table_schema, count(*) FROM information_schema.tables"
            " WHERE table_schema = ANY(%s) GROUP BY 1",
            (list(SCHEMAS),),
        ).fetchall()
    return dict(rows)


def test_upgrade_downgrade_upgrade():
    name = f"mhp_test_mig_{uuid.uuid4().hex[:8]}"
    url = create_database(name)
    try:
        migrate(url)
        assert schema_table_counts(url) == {"identity": 8, "planning": 17, "vault": 4}

        migrate(url, "0001", down=True)
        assert schema_table_counts(url) == {}
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001"
            conn.execute("SELECT 1 FROM messages")  # the baseline table is kept

        migrate(url)
        assert schema_table_counts(url) == {"identity": 8, "planning": 17, "vault": 4}
    finally:
        drop_database(name)


def test_seed_runs_once(database):
    import seed

    with psycopg.connect(database) as conn:
        before = conn.execute("SELECT count(*) FROM identity.account_subject").fetchone()[0]
    seed.main()
    with psycopg.connect(database) as conn:
        after = conn.execute("SELECT count(*) FROM identity.account_subject").fetchone()[0]
    assert before == after == 4
