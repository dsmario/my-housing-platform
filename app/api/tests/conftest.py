"""Test setup: a fresh database per run, built by the real migrations and seed.

Needs TEST_ADMIN_DATABASE_URL: a connection that may create databases (the
"postgres" database on the local server). docker compose sets it for the
api-test service:

    docker compose run --rm api-test

The database is called mhp_test_<random> and is dropped at the end, so the
local mhp database is never touched.
"""

import os
import uuid

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

ADMIN_URL = os.environ["TEST_ADMIN_DATABASE_URL"]


def create_database(name: str) -> str:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def drop_database(name: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def migrate(url: str, target: str = "head", down: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    # migrations/env.py reads DATABASE_URL; put back the previous value after.
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
        (command.downgrade if down else command.upgrade)(cfg, target)
    finally:
        if previous is not None:
            os.environ["DATABASE_URL"] = previous


# Set before the app is imported: stub tokens, local vault keys, and the test database.
os.environ["IDENTITY_PROVIDER"] = "stub"
os.environ["VAULT_KEY_PROVIDER"] = "local"
os.environ["VAULT_LOCAL_KEY"] = "dGVzdC1vbmx5LWxvY2FsLXZhdWx0LWtleS0zMmJ5dGU="
# Presigned POSTs are signed locally; tests never contact S3.
os.environ["VAULT_QUARANTINE_BUCKET"] = "test-quarantine"
os.environ["VAULT_BUCKET"] = "test-vault"
os.environ["VAULT_AUDIT_BUCKET"] = "test-audit"
os.environ["API_FORWARD_SECRET"] = "test-forward-secret"
os.environ["VAULT_S3_PUBLIC_ENDPOINT"] = "http://storage.test:8333"
os.environ["AWS_ACCESS_KEY_ID"] = "test"
os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
_DB_NAME = f"mhp_test_{uuid.uuid4().hex[:8]}"
os.environ["DATABASE_URL"] = create_database(_DB_NAME)


@pytest.fixture(scope="session", autouse=True)
def database():
    url = os.environ["DATABASE_URL"]
    try:
        migrate(url)
        import seed

        seed.main()
        yield url
    finally:
        drop_database(_DB_NAME)


@pytest.fixture
def conn(database):
    """A connection whose changes are rolled back after the test."""
    with psycopg.connect(database) as c:
        yield c
        c.rollback()


@pytest.fixture(scope="session")
def client(database):
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:
        yield c
