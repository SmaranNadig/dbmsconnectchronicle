"""Read-only query consoles for the dashboard: SQL on the coordinator and
find/aggregate on the MongoDB metadata store.

SQL safety, in layers (any one of them is enough to stop a write):
  * only statements starting with SELECT / WITH / TABLE / VALUES / EXPLAIN / SHOW;
  * the text is sent as a server-side prepared statement, which PostgreSQL
    refuses to build from more than one command, so "SELECT 1; COMMIT; ..."
    cannot end the read-only transaction;
  * a fresh connection per request, `SET ROLE cc_reader` (SELECT-only grants,
    see migration 0010) and a READ ONLY transaction that is always rolled back;
  * `statement_timeout` and a row cap.

MongoDB safety: one whitelisted collection, find / aggregate / count only,
server-side operators that run code or write ($where, $function,
$accumulator, $out, $merge) are refused, maxTimeMS and a document cap.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from decimal import Decimal

import psycopg
from bson import json_util

from backend.config import get_settings
from backend.metadata import COLLECTION, mongo_db

MAX_ROWS = 500
TIMEOUT_MS = 10_000
ALLOWED_START = re.compile(r"^\s*(\(\s*)*(select|with|table|values|explain|show)\b", re.I)


class QueryError(ValueError):
    """A query the console will not run, or one the database rejected."""


def _cell(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return v
    return str(v)          # ranges, arrays of ranges, bytes, ...


def _strip(sql: str) -> str:
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    # drop leading comments so "-- note\nSELECT ..." is accepted
    while True:
        if sql.startswith("--"):
            sql = sql.split("\n", 1)[1].lstrip() if "\n" in sql else ""
        elif sql.startswith("/*") and "*/" in sql:
            sql = sql.split("*/", 1)[1].lstrip()
        else:
            return sql


def _error(e: psycopg.Error) -> str:
    diag = e.diag
    return (f"{diag.sqlstate or ''} {diag.message_primary or e}".strip()
            + (f"\nDETAIL: {diag.message_detail}" if diag.message_detail else "")
            + (f"\nHINT: {diag.message_hint}" if diag.message_hint else "")
            + (f"\nCONSTRAINT: {diag.constraint_name}" if diag.constraint_name else ""))


# "trial" target: one data-changing statement on the real tables, run as
# cc_tryout (migration 0012) and always rolled back, so constraints and
# triggers can be tried on the loaded data without changing it.
TRIAL_START = re.compile(r"^\s*(\(\s*)*(select|with|table|values|explain|insert|update|delete|merge)\b", re.I)


def run_sql(sql: str, explain: str | None = None, target: str = "read") -> dict:
    """Run one statement on the real database, always rolled back.
    target "read": SELECT-only role in a READ ONLY transaction; "trial":
    INSERT / UPDATE / DELETE allowed, with every constraint and trigger.
    explain: None, "plan" (EXPLAIN) or "analyze" (EXPLAIN ANALYZE, BUFFERS:
    the statement really runs)."""
    sql = _strip(sql)
    if not sql:
        raise QueryError("empty query")
    trial = target == "trial"
    if trial and not TRIAL_START.match(sql):
        raise QueryError("a trial runs one SELECT, INSERT, UPDATE, DELETE or MERGE statement; for DDL "
                         "(CREATE, ALTER, DROP) use the Lab target")
    if not trial and not ALLOWED_START.match(sql):
        raise QueryError("the console is read-only: start the query with SELECT, WITH, TABLE, VALUES, "
                         "EXPLAIN or SHOW, or choose the Trial or Lab target to change data")
    if explain == "plan":
        sql = f"EXPLAIN (COSTS, VERBOSE OFF) {sql}"
    elif explain == "analyze":
        sql = f"EXPLAIN (ANALYZE, BUFFERS, TIMING) {sql}"
    with psycopg.connect(get_settings().pg_dsn(), options="-c search_path=cc,public",
                         autocommit=True) as conn:
        try:
            conn.execute("BEGIN" if trial else "BEGIN READ ONLY")
            conn.execute(f"SET LOCAL ROLE {'cc_tryout' if trial else 'cc_reader'}")
            conn.execute(f"SET LOCAL statement_timeout = {TIMEOUT_MS}")
            t0 = time.perf_counter()
            cur = conn.execute(sql, prepare=True)
            status, affected = cur.statusmessage, cur.rowcount
            if cur.description is None and not trial:
                raise QueryError("statement returned no result set")
            columns = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchmany(MAX_ROWS + 1) if cur.description else []
            ms = (time.perf_counter() - t0) * 1000
        except psycopg.Error as e:
            raise QueryError(_error(e)) from e
        finally:
            conn.execute("ROLLBACK")
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    return {"columns": columns, "rows": [[_cell(v) for v in r] for r in rows], "row_count": len(rows),
            "truncated": truncated, "elapsed_ms": round(ms, 2), "explain": explain, "status": status,
            "affected": affected, "target": target,
            "executed_as": ("cc_tryout on the real tables (transaction rolled back: nothing was saved)" if trial
                            else "cc_reader (READ ONLY transaction, rolled back)")}


FORBIDDEN = {"$where", "$function", "$accumulator", "$out", "$merge"}
MAX_DOCS = 50


def _check(node) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in FORBIDDEN:
                raise QueryError(f"operator {k} is not allowed in the console")
            _check(v)
    elif isinstance(node, list):
        for v in node:
            _check(v)


def run_mongo(op: str, filter: dict | None = None, projection: dict | None = None, sort: dict | None = None,
              pipeline: list | None = None, limit: int = 10) -> dict:
    limit = max(1, min(limit, MAX_DOCS))
    for part in (filter, projection, sort, pipeline):
        _check(part)
    coll = mongo_db()[COLLECTION]
    t0 = time.perf_counter()
    try:
        if op == "find":
            cur = coll.find(filter or {}, projection or None, max_time_ms=TIMEOUT_MS).limit(limit)
            if sort:
                cur = cur.sort(list(sort.items()))
            docs = list(cur)
        elif op == "count":
            docs = [{"count": coll.count_documents(filter or {}, maxTimeMS=TIMEOUT_MS)}]
        elif op == "aggregate":
            if not isinstance(pipeline, list):
                raise QueryError("aggregate needs a pipeline: a JSON array of stages")
            docs = list(coll.aggregate([*pipeline, {"$limit": limit}], maxTimeMS=TIMEOUT_MS))
        else:
            raise QueryError("op must be find, count or aggregate")
    except QueryError:
        raise
    except Exception as e:     # pymongo raises OperationFailure, ExecutionTimeout, ...
        raise QueryError(str(e)) from e
    ms = (time.perf_counter() - t0) * 1000
    return {"collection": COLLECTION, "op": op, "count": len(docs), "elapsed_ms": round(ms, 2),
            "documents": json.loads(json_util.dumps(docs, json_options=json_util.RELAXED_JSON_OPTIONS))}
