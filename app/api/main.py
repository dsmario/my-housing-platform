"""MHP API, walking skeleton: one route that reads a message from Postgres."""

import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException

DATABASE_URL = os.environ["DATABASE_URL"]

SEED_TEXT = "Hello from the database. Web, API and Postgres are connected."


def connect():
    return psycopg.connect(DATABASE_URL)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
