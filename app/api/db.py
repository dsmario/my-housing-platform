"""Database connection settings, shared by the API, migrations and the seed script.

Local: one DATABASE_URL. AWS: separate DB_* values, with the user and password
injected from the RDS-managed secret in Secrets Manager.
"""

import os

import psycopg
from sqlalchemy.engine import URL, make_url


def connect():
    if "DATABASE_URL" in os.environ:
        return psycopg.connect(os.environ["DATABASE_URL"])
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode="require",
    )


def sqlalchemy_url() -> URL:
    if "DATABASE_URL" in os.environ:
        return make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql+psycopg")
    # URL.create escapes the generated RDS password, which can contain any character.
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ["DB_NAME"],
        query={"sslmode": "require"},
    )
