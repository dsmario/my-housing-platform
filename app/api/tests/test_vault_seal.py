"""Daily audit seal (§7): rows go to the locked audit bucket, can be rechecked from the file alone."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fakes import FakeS3

from db import connect
from vault_worker import SealRefused, seal_audit, seal_due

AUDIT = os.environ["VAULT_AUDIT_BUCKET"]
# Far in the future, so the dates are under the tests' control.
DAY1 = datetime(2030, 1, 1, 3, 0, tzinfo=timezone.utc)


def add_audit_rows(n: int = 3) -> None:
    with connect() as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO vault.audit_event (action, outcome, user_agent, detail) VALUES ('view', 'allowed', %s, %s)",
                (f"seal-test-{i}", json.dumps({"n": i})),
            )


def seal(s3, now):
    with connect() as conn:
        return seal_audit(s3, conn, now)


def read_seal(s3, key) -> tuple[dict, list[dict]]:
    header, *rows = [json.loads(line) for line in s3.objects[(AUDIT, key)].decode().splitlines()]
    return header, rows


def recheck_chain(header, rows) -> bool:
    """What an auditor can do with the file alone: rebuild every hash."""
    prev = header["start_hash"]
    for r in rows:
        if r["prev_hash"] != prev:
            return False
        if hashlib.sha256(bytes.fromhex(prev) + r["payload"].encode()).hexdigest() != r["row_hash"]:
            return False
        prev = r["row_hash"]
    return prev == header["head_hash"]


def test_daily_seals_follow_on_and_can_be_rechecked_from_the_file():
    s3 = FakeS3()
    add_audit_rows()
    first = seal(s3, DAY1)
    assert first.startswith("seals/2030/01/01/") and first.endswith(".jsonl")
    header, rows = read_seal(s3, first)
    assert header["rows"] == len(rows) >= 3
    assert recheck_chain(header, rows)
    assert {"seal-test-0", "seal-test-1", "seal-test-2"} <= {r["user_agent"] for r in rows}

    with connect() as conn:
        recorded = conn.execute(
            "SELECT first_event_id, last_event_id, encode(head_hash, 'hex'), object_key"
            "  FROM vault.audit_seal ORDER BY sealed_at DESC LIMIT 1"
        ).fetchone()
    assert recorded == (header["first_event_id"], header["last_event_id"], header["head_hash"], first)

    # Not due again the same day.
    add_audit_rows(1)
    assert seal(s3, DAY1 + timedelta(hours=10)) is None

    # The next day carries on exactly where the last seal ended.
    second = seal(s3, DAY1 + timedelta(days=1))
    header2, rows2 = read_seal(s3, second)
    assert header2["first_event_id"] == header["last_event_id"] + 1
    assert header2["start_hash"] == header["head_hash"]
    assert recheck_chain(header2, rows2)


def test_nothing_new_writes_nothing():
    s3 = FakeS3()
    with connect() as conn:
        last = conn.execute("SELECT max(sealed_at) FROM vault.audit_seal").fetchone()[0]
        newest_row = conn.execute("SELECT max(id) FROM vault.audit_event").fetchone()[0]
        sealed_to = conn.execute("SELECT max(last_event_id) FROM vault.audit_seal").fetchone()[0]
    if last is None or newest_row != sealed_to:
        seal(s3, DAY1 + timedelta(days=5))  # catch up first
        s3 = FakeS3()
    assert seal(s3, DAY1 + timedelta(days=6)) is None
    assert s3.objects == {}


def test_a_missed_day_is_caught_up():
    with connect() as conn:
        last = conn.execute("SELECT max(sealed_at) FROM vault.audit_seal").fetchone()[0]
        assert seal_due(conn, last + timedelta(days=3))
        assert not seal_due(conn, last + timedelta(minutes=1))


def test_broken_chain_is_not_sealed():
    s3 = FakeS3()
    add_audit_rows(1)
    with connect() as conn:
        last = conn.execute("SELECT max(id) FROM vault.audit_event").fetchone()[0]
        conn.execute("ALTER TABLE vault.audit_event DISABLE TRIGGER audit_event_append_only")
        conn.execute("UPDATE vault.audit_event SET outcome = 'denied' WHERE id = %s", (last,))
        with pytest.raises(SealRefused, match="chain broken"):
            seal_audit(s3, conn, DAY1 + timedelta(days=30))
        conn.rollback()  # the row and the trigger come back
    assert s3.objects == {}


def test_seal_that_does_not_read_back_the_same_is_not_recorded():
    class Corrupting(FakeS3):
        def get_object(self, Bucket, Key, VersionId=None):
            body = super().get_object(Bucket, Key)["Body"].read()
            import io

            return {"Body": io.BytesIO(body[:-2] + b"!\n")}

    add_audit_rows(1)
    with connect() as conn:
        before = conn.execute("SELECT count(*) FROM vault.audit_seal").fetchone()[0]
    with pytest.raises(SealRefused, match="read back"):
        seal(Corrupting(), DAY1 + timedelta(days=60))
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM vault.audit_seal").fetchone()[0] == before
