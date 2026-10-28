"""Admin API: browse every table, edit the configuration tables.

Only tables listed in TABLES are reachable, and each has a mode:

  edit     Configuration (rule releases, schemas). Add, change, delete.
  read     People and planning records. View only, so staff can't quietly
           change what a youth has recorded.
  summary  Row count and integrity check only. Used for the Doc Vault audit
           log: who viewed which document is not shown to engineers
           (Doc Vault section 7, PRIV-1).

HIDDEN columns never leave the API (key material and contact details). Their
cells come back as "[hidden]", or null when empty, so staff can still tell a
destroyed key from a live one.

Column names and types are read from the database, so a new migration shows up
here without code changes. Table and column names are only ever used after
being matched against that list.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from ipaddress import IPv4Address, IPv6Address
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from psycopg import sql

from db import connect
from identity import staff_admin

router = APIRouter(prefix="/api/admin", dependencies=[Depends(staff_admin)])

HIDDEN = "[hidden]"


@dataclass(frozen=True)
class TableRule:
    mode: str  # edit, read or summary
    hidden: frozenset[str] = field(default_factory=frozenset)


TABLES: dict[str, TableRule] = {
    # identity
    "identity.account_subject": TableRule("read"),
    "identity.subject_pseudonym": TableRule("read"),
    "identity.verified_channel": TableRule("read", frozenset({"destination_ciphertext"})),
    "identity.youth_profile": TableRule("read"),
    "identity.adult_profile": TableRule("read"),
    "identity.adult_block": TableRule("read", frozenset({"blocked_channel_hashes"})),
    "identity.share_grant": TableRule("read"),
    "identity.share_grant_scope": TableRule("read"),
    # planning: configuration
    "planning.rules_input_release": TableRule("edit"),
    "planning.rules_corpus_release": TableRule("edit"),
    "planning.task_result_schema": TableRule("edit"),
    "planning.readiness_assessment_schema": TableRule("edit"),
    # planning: youth records and rule history
    "planning.housing_readiness_plan": TableRule("read"),
    "planning.plan_answer": TableRule("read"),
    "planning.youth_document_status": TableRule("read"),
    "planning.plan_input_schema": TableRule("read"),
    "planning.plan_fact_schema": TableRule("read"),
    "planning.plan_input": TableRule("read"),
    "planning.rule_input_evaluation": TableRule("read"),
    "planning.fact_snapshot": TableRule("read"),
    "planning.rule_evaluation": TableRule("read"),
    "planning.action_result": TableRule("read"),
    "planning.pathway_task": TableRule("read"),
    "planning.pathway_task_result": TableRule("read"),
    "planning.readiness_assessment": TableRule("read"),
    # vault
    "vault.youth_data_key": TableRule("read", frozenset({"wrapped_key"})),
    "vault.document": TableRule("read", frozenset({"title", "wrapped_document_key"})),
    "vault.audit_event": TableRule("summary"),
    "vault.audit_seal": TableRule("read"),
}

# Newest first, by the first of these the table has.
ORDER_COLUMNS = ("created_at", "recorded_at", "occurred_at", "sealed_at")
PAGE_SIZE_MAX = 200


def get_rule(table: str) -> TableRule:
    rule = TABLES.get(table)
    if rule is None:
        raise HTTPException(404, f"Unknown table: {table}")
    return rule


def ident(table: str) -> sql.Identifier:
    schema, name = table.split(".")
    return sql.Identifier(schema, name)


def columns(conn, table: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.attname,
               format_type(a.atttypid, a.atttypmod),
               a.attnotnull,
               a.atthasdef,
               (SELECT cn.nspname || '.' || cc.relname
                  FROM pg_constraint c
                  JOIN pg_class cc ON cc.oid = c.confrelid
                  JOIN pg_namespace cn ON cn.oid = cc.relnamespace
                 WHERE c.conrelid = a.attrelid AND c.contype = 'f'
                   AND c.conkey = ARRAY[a.attnum]
                 LIMIT 1)
          FROM pg_attribute a
         WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY a.attnum
        """,
        (table,),
    ).fetchall()
    rule = get_rule(table)
    return [
        {
            "name": name,
            "type": type_,
            "required": not_null and not has_default,
            "has_default": has_default,
            "references": references,
            "hidden": name in rule.hidden,
        }
        for name, type_, not_null, has_default, references in rows
    ]


def to_json(value: Any) -> Any:
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal, IPv4Address, IPv6Address)):
        return str(value)
    if isinstance(value, list):
        return [to_json(v) for v in value]
    return value


def select_list(cols: list[dict]) -> sql.Composable:
    parts = []
    for c in cols:
        name = sql.Identifier(c["name"])
        if c["hidden"]:
            parts.append(
                sql.SQL("CASE WHEN {n} IS NULL THEN NULL ELSE {h} END AS {n}").format(
                    n=name, h=sql.Literal(HIDDEN)
                )
            )
        else:
            parts.append(name)
    return sql.SQL(", ").join(parts)


def array_literal(items: list) -> str:
    def quote(v: Any) -> str:
        if v is None:
            return "NULL"
        s = str(v).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'

    return "{" + ",".join(quote(v) for v in items) + "}"


def to_db_text(value: Any, type_: str) -> str | None:
    """Values travel as text and Postgres casts them, so every column type
    works the same way and bad input gets Postgres's own error message."""
    if value is None:
        return None
    if type_.endswith("[]"):
        if not isinstance(value, list):
            raise HTTPException(422, f"Expected a list for a {type_} column")
        return array_literal(value)
    if type_ in ("json", "jsonb"):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def assignments(cols: list[dict], values: dict) -> list[tuple[dict, str | None]]:
    by_name = {c["name"]: c for c in cols}
    out = []
    for name, value in values.items():
        col = by_name.get(name)
        if col is None:
            raise HTTPException(422, f"Unknown column: {name}")
        if col["hidden"]:
            raise HTTPException(422, f"Column {name} can't be set here")
        out.append((col, to_db_text(value, col["type"])))
    return out


def placeholder(col: dict) -> sql.Composable:
    # The type comes from the catalog, not from the request.
    return sql.SQL("%s::") + sql.SQL(col["type"])


def db_error(e: psycopg.Error) -> HTTPException:
    message = e.diag.message_primary or str(e)
    if isinstance(e, psycopg.errors.ForeignKeyViolation) and "still referenced" in (e.diag.message_detail or ""):
        return HTTPException(409, "Other records use this row, so it can't be deleted.")
    if isinstance(e, psycopg.errors.IntegrityError):
        return HTTPException(409, message)
    return HTTPException(422, message)


@router.get("/tables")
def list_tables():
    with connect() as conn:
        out = []
        for table, rule in TABLES.items():
            count = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(ident(table))).fetchone()[0]
            schema, name = table.split(".")
            out.append({"table": table, "schema": schema, "name": name, "mode": rule.mode, "rows": count})
    return out


@router.get("/tables/{table}")
def table_rows(
    table: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=PAGE_SIZE_MAX),
):
    """Rows, newest first. Any other query parameter that names a column is an
    exact-match filter, e.g. ?plan_id=... (used to follow links)."""
    rule = get_rule(table)
    with connect() as conn:
        cols = columns(conn, table)
        result = {"table": table, "mode": rule.mode, "columns": cols}
        if rule.mode == "summary":
            result["rows"] = []
            result["total"] = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(ident(table))).fetchone()[0]
            if table == "vault.audit_event":
                result["chain_broken_at"] = conn.execute("SELECT vault.verify_audit_chain()").fetchone()[0]
            return result

        names = {c["name"]: c for c in cols}
        filters, params = [], []
        for key, value in request.query_params.items():
            if key in ("offset", "limit"):
                continue
            if key not in names or names[key]["hidden"]:
                raise HTTPException(422, f"Can't filter on: {key}")
            filters.append(sql.SQL("{}::text = %s").format(sql.Identifier(key)))
            params.append(value)
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(filters) if filters else sql.SQL("")
        order_col = next((c for c in ORDER_COLUMNS if c in names), "id")

        total = conn.execute(
            sql.SQL("SELECT count(*) FROM {t}{w}").format(t=ident(table), w=where), params
        ).fetchone()[0]
        cur = conn.execute(
            sql.SQL("SELECT {cols} FROM {t}{w} ORDER BY {o} DESC LIMIT %s OFFSET %s").format(
                cols=select_list(cols), t=ident(table), w=where, o=sql.Identifier(order_col)
            ),
            [*params, limit, offset],
        )
        keys = [d.name for d in cur.description]
        result["rows"] = [{k: to_json(v) for k, v in zip(keys, row)} for row in cur.fetchall()]
        result["total"] = total
    return result


def require_edit(table: str) -> TableRule:
    rule = get_rule(table)
    if rule.mode != "edit":
        raise HTTPException(403, f"{table} is view-only")
    return rule


def fetch_one(conn, table: str, cols: list[dict], row_id: str) -> dict:
    cur = conn.execute(
        sql.SQL("SELECT {cols} FROM {t} WHERE id::text = %s").format(cols=select_list(cols), t=ident(table)),
        (row_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "Row not found")
    return {d.name: to_json(v) for d, v in zip(cur.description, row)}


@router.get("/tables/{table}/{row_id}")
def get_row(table: str, row_id: str):
    rule = get_rule(table)
    if rule.mode == "summary":
        raise HTTPException(403, f"{table} rows aren't shown")
    with connect() as conn:
        cols = columns(conn, table)
        return {"table": table, "mode": rule.mode, "columns": cols, "row": fetch_one(conn, table, cols, row_id)}


@router.post("/tables/{table}", status_code=201)
def create_row(table: str, values: dict = Body(...)):
    require_edit(table)
    try:
        with connect() as conn:
            cols = columns(conn, table)
            pairs = assignments(cols, values)
            if not pairs:
                raise HTTPException(422, "No values given")
            row_id = conn.execute(
                sql.SQL("INSERT INTO {t} ({names}) VALUES ({ph}) RETURNING id").format(
                    t=ident(table),
                    names=sql.SQL(", ").join(sql.Identifier(c["name"]) for c, _ in pairs),
                    ph=sql.SQL(", ").join(placeholder(c) for c, _ in pairs),
                ),
                [v for _, v in pairs],
            ).fetchone()[0]
            return fetch_one(conn, table, cols, str(row_id))
    except psycopg.Error as e:
        raise db_error(e) from None


@router.patch("/tables/{table}/{row_id}")
def update_row(table: str, row_id: str, values: dict = Body(...)):
    require_edit(table)
    if "id" in values:
        raise HTTPException(422, "id can't be changed")
    try:
        with connect() as conn:
            cols = columns(conn, table)
            pairs = assignments(cols, values)
            if not pairs:
                raise HTTPException(422, "No values given")
            updated = conn.execute(
                sql.SQL("UPDATE {t} SET {sets} WHERE id::text = %s").format(
                    t=ident(table),
                    sets=sql.SQL(", ").join(
                        sql.SQL("{} = ").format(sql.Identifier(c["name"])) + placeholder(c) for c, _ in pairs
                    ),
                ),
                [*(v for _, v in pairs), row_id],
            ).rowcount
            if updated == 0:
                raise HTTPException(404, "Row not found")
            return fetch_one(conn, table, cols, row_id)
    except psycopg.Error as e:
        raise db_error(e) from None


@router.delete("/tables/{table}/{row_id}", status_code=204)
def delete_row(table: str, row_id: str):
    require_edit(table)
    try:
        with connect() as conn:
            deleted = conn.execute(
                sql.SQL("DELETE FROM {t} WHERE id::text = %s").format(t=ident(table)), (row_id,)
            ).rowcount
    except psycopg.Error as e:
        raise db_error(e) from None
    if deleted == 0:
        raise HTTPException(404, "Row not found")
