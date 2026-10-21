"""MHP API, walking skeleton: one route that reads a message from Postgres."""

import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, HTTPException

from identity import Principal, current_principal, get_verifier

SEED_TEXT = "Hello from the database. Web, API and Postgres are connected."


def connect():
    # Local: one DATABASE_URL. AWS: separate DB_* values, with the user and
    # password injected from the RDS-managed secret in Secrets Manager.
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_verifier()  # Fail at startup if IDENTITY_PROVIDER is missing or wrong.
    # Skeleton only: create and seed the table on startup. Real schema changes
    # will go through migrations.
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id         serial PRIMARY KEY,
                text       text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        if conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0:
            conn.execute("INSERT INTO messages (text) VALUES (%s)", (SEED_TEXT,))
    yield


app = FastAPI(title="MHP API", lifespan=lifespan)


@app.get("/api/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/message")
def message():
    with connect() as conn:
        row = conn.execute(
            "SELECT text, now(), version() FROM messages ORDER BY id LIMIT 1"
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No message in the database")
    text, db_time, db_version = row
    return {
        "text": text,
        "db_time": db_time.isoformat(),
        "db_version": db_version.split(",")[0],
    }


@app.get("/api/me")
def me(principal: Principal = Depends(current_principal)):
    return {
        "username": principal.username,
        "groups": list(principal.groups),
    }
