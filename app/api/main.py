"""MHP API, walking skeleton: one route that reads a message from Postgres.

The schema comes from Alembic migrations (migrations/) and test data from seed.py;
start.sh runs both before the API starts.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

import admin
import vault
from db import connect
from identity import Principal, current_principal, get_verifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_verifier()  # Fail at startup if IDENTITY_PROVIDER is missing or wrong.
    yield


app = FastAPI(title="MHP API", lifespan=lifespan)
app.include_router(admin.router)
app.include_router(vault.router)


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
        "pool": principal.pool,
    }
