"""The writable side of the dashboard (DATA_EDITOR_PLAN.md).

SQL
  * "lab": schema `lab`, a small editable copy of real data (lab_schema.sql),
    reached through the login role cc_lab. cc_lab owns `lab`, may only read
    `cc` and `er`, and is a separate login, so a script cannot switch back to
    the administrator role. Scripts may contain several statements, DDL and
    their own BEGIN / COMMIT; they are committed unless `rollback` is set.
  * the table editor builds INSERT / UPDATE / DELETE from the catalog for lab
    tables and returns the exact statement it ran.

MongoDB
  * database `<mongo_db>_lab` holds the source documents of the lab neurons,
    the same unique indexes as the real store and a $jsonSchema validator.
    Every write operation here targets that database only; the real store
    stays read-only (backend/console.py).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import psycopg
from bson import json_util
from psycopg import sql
from psycopg.rows import dict_row

from backend.config import PROJECT_ROOT, get_settings
from backend.console import MAX_ROWS, TIMEOUT_MS, QueryError, _cell, _check, _error
from backend.metadata import COLLECTION, _client, ensure_indexes, mongo_db

LAB_SQL = PROJECT_ROOT / "backend" / "db" / "lab_schema.sql"
EDGES_PER_DATASET = 150
MAX_STATEMENT_ROWS = 200
MAX_DOCS = 50
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_build_lock = threading.RLock()


# ------------------------------------------------------------- connections
def lab_password() -> str:
    """cc_lab's password, derived from the administrator's, so no extra setting
    is needed and every process (API, tests, migrations) agrees on it."""
    s = get_settings()
    return hashlib.sha256(f"cc_lab:{s.postgres_user}:{s.postgres_password}".encode()).hexdigest()[:40]


def _admin():
    return psycopg.connect(get_settings().pg_dsn(), autocommit=True, row_factory=dict_row)


def lab_connect() -> psycopg.Connection:
    s = get_settings()
    kw = dict(host=s.postgres_host, port=s.coordinator_port, dbname=s.postgres_db, user="cc_lab",
              password=lab_password(), autocommit=True, row_factory=dict_row,
              options=f"-c search_path=lab,cc,public -c statement_timeout={TIMEOUT_MS}")
    try:
        return psycopg.connect(**kw)
    except psycopg.OperationalError as e:
        if "password" not in str(e):
            raise
        # the administrator password changed since migration 0012: re-derive
        with _admin() as conn:
            conn.execute(f"ALTER ROLE cc_lab PASSWORD '{lab_password()}'")
        return psycopg.connect(**kw)


def _watchdog(conn: psycopg.Connection) -> threading.Timer:
    """Cancels the running statement even if a script raised its own
    statement_timeout."""
    t = threading.Timer(TIMEOUT_MS / 1000 * 1.5, conn.cancel)
    t.daemon = True
    t.start()
    return t


def mongo_lab_name() -> str:
    return f"{get_settings().mongo_db}_lab"


def mongo_lab():
    return _client(get_settings().mongo_uri)[mongo_lab_name()]


# ------------------------------------------------------------- build / reset
VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["dataset", "source_neuron_id", "dataset_neuron_id"],
    "properties": {
        "dataset": {"bsonType": "string", "description": "dataset code, e.g. hemibrain"},
        "source_neuron_id": {"bsonType": "string", "description": "the id used by the source, as text"},
        "dataset_neuron_id": {"bsonType": ["int", "long"], "description": "key of the neuron in PostgreSQL"},
        "releases": {"bsonType": "object", "description": "original source rows per release id"},
    },
}}


def exists() -> bool:
    with _admin() as conn:
        return conn.execute("SELECT to_regclass('lab.change_log') IS NOT NULL AS ok").fetchone()["ok"]


def reset() -> dict:
    """Drop and rebuild the SQL and MongoDB labs from the real data."""
    t0 = time.perf_counter()
    script = LAB_SQL.read_text(encoding="utf-8").replace("{edges}", str(EDGES_PER_DATASET))
    with _build_lock, _admin() as conn:
        with conn.transaction():
            conn.execute("DROP SCHEMA IF EXISTS lab CASCADE")
            conn.execute("CREATE SCHEMA lab AUTHORIZATION cc_lab")
            conn.execute("SET LOCAL ROLE cc_lab")
            conn.execute("SET LOCAL statement_timeout = '180s'")
            conn.execute(script)
        ids = [r["neuron_id"] for r in conn.execute("SELECT neuron_id FROM lab.neuron")]
    mdb = mongo_lab()
    mdb.client.drop_database(mdb.name)
    mdb.create_collection(COLLECTION, validator=VALIDATOR, validationAction="error")
    ensure_indexes(mdb)
    docs = list(mongo_db()[COLLECTION].find({"dataset_neuron_id": {"$in": ids}})) if ids else []
    if docs:
        mdb[COLLECTION].insert_many(docs, ordered=False)
    out = status()
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


def ensure() -> None:
    if not exists():
        with _build_lock:
            if not exists():
                reset()


def status() -> dict:
    with _admin() as conn:
        if not conn.execute("SELECT to_regclass('lab.change_log') IS NOT NULL AS ok").fetchone()["ok"]:
            return {"built": False}
        counts = conn.execute("""SELECT (SELECT count(*) FROM lab.dataset) AS datasets,
                                        (SELECT count(*) FROM lab.release) AS releases,
                                        (SELECT count(*) FROM lab.neuron) AS neurons,
                                        (SELECT count(*) FROM lab.connection) AS connections,
                                        (SELECT count(*) FROM lab.change_log) AS changes""").fetchone()
    mdb = mongo_lab()
    return {"built": True, "schema": "lab", "role": "cc_lab", "tables": counts,
            "mongo": {"database": mdb.name,
                      "collections": {c: mdb[c].estimated_document_count() for c in sorted(mdb.list_collection_names())}},
            "review_token_required": bool(get_settings().cc_review_token)}


# ------------------------------------------------------------- SQL scripts
def run_script(text: str, rollback: bool = False) -> dict:
    """Run a script as cc_lab: one result per statement. PostgreSQL runs a
    multi-statement script as one transaction unless it has its own
    BEGIN / COMMIT, so a failing statement undoes the earlier ones."""
    if not text.strip():
        raise QueryError("empty script")
    ensure()
    results = []
    t0 = time.perf_counter()
    with lab_connect() as conn:
        dog = _watchdog(conn)
        try:
            if rollback:
                conn.execute("BEGIN")
            cur = conn.cursor()
            cur.execute(text)
            while True:
                item = {"status": cur.statusmessage, "rowcount": cur.rowcount}
                if cur.description is not None:
                    rows = cur.fetchmany(MAX_STATEMENT_ROWS + 1)
                    item.update(columns=[d.name for d in cur.description],
                                rows=[[_cell(v) for v in r.values()] for r in rows[:MAX_STATEMENT_ROWS]],
                                truncated=len(rows) > MAX_STATEMENT_ROWS)
                results.append(item)
                if not cur.nextset():
                    break
        except psycopg.Error as e:
            raise QueryError(_error(e) + "\nNothing from this run was saved, unless the script ran COMMIT "
                             "before the failing statement.") from e
        finally:
            dog.cancel()
            # a preview, or a BEGIN without COMMIT: undo, as psql does on disconnect
            open_tx = conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE
            if open_tx:
                conn.execute("ROLLBACK")
    if open_tx and not rollback:
        results.append({"status": "ROLLBACK (the script left its transaction open, so it was rolled back)",
                        "rowcount": -1})
    return {"results": results, "statements": len(results), "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            "executed_as": "cc_lab in schema lab (" + ("rolled back: preview only" if rollback else "committed") + ")"}


# ------------------------------------------------------------- table catalog
SCHEMAS = {"lab": "editable copy", "cc": "real data, read-only here", "er": "ER-diagram views, read-only"}

_TABLES_SQL = """
SELECT c.relname AS name,
       CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'table' WHEN 'v' THEN 'view'
                      WHEN 'm' THEN 'materialized view' END AS kind,
       greatest(c.reltuples, 0)::bigint AS estimated_rows,
       obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
ORDER BY c.relkind, c.relname"""

_COLUMNS_SQL = """
SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS default, a.attidentity AS identity, a.attgenerated AS generated,
       coalesce(a.attnum = ANY (pk.conkey), false) AS primary_key
FROM pg_attribute a
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
LEFT JOIN pg_constraint pk ON pk.conrelid = a.attrelid AND pk.contype = 'p'
WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum"""

_CONSTRAINTS_SQL = """
SELECT conname AS name, CASE contype WHEN 'p' THEN 'PRIMARY KEY' WHEN 'f' THEN 'FOREIGN KEY' WHEN 'u' THEN 'UNIQUE'
       WHEN 'c' THEN 'CHECK' WHEN 'x' THEN 'EXCLUDE' ELSE contype::text END AS type,
       pg_get_constraintdef(oid) AS definition
FROM pg_constraint WHERE conrelid = to_regclass(%s) ORDER BY contype, conname"""


def _schema(schema: str) -> str:
    if schema not in SCHEMAS:
        raise QueryError(f"schema must be one of {', '.join(SCHEMAS)}")
    return schema


def tables(schema: str) -> dict:
    _schema(schema)
    if schema == "lab":
        ensure()
    with _admin() as conn:
        rows = conn.execute(_TABLES_SQL, (schema,)).fetchall()
        if schema == "lab":       # small: exact counts
            for r in rows:
                r["rows"] = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(r["name"]))).fetchone()["n"]
    return {"schema": schema, "about": SCHEMAS[schema], "editable": schema == "lab", "tables": rows}


def describe(schema: str, table: str, conn) -> dict:
    qualified = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table)).as_string(conn)
    cols = conn.execute(_COLUMNS_SQL, (qualified,)).fetchall()
    if not cols:
        raise QueryError(f"no table or view {schema}.{table}")
    return {"columns": cols, "constraints": conn.execute(_CONSTRAINTS_SQL, (qualified,)).fetchall(),
            "primary_key": [c["name"] for c in cols if c["primary_key"]]}


def rows(schema: str, table: str, limit: int = 50, offset: int = 0, sort: str | None = None,
         desc: bool = False) -> dict:
    _schema(schema)
    if schema == "lab":
        ensure()
    with _admin() as conn:
        meta = describe(schema, table, conn)
        names = [c["name"] for c in meta["columns"]]
        order = [sort] if sort else (meta["primary_key"] or names[:1])
        if any(o not in names for o in order):
            raise QueryError(f"unknown column {sort!r}")
        q = sql.SQL("SELECT * FROM {}.{} ORDER BY {} LIMIT %s OFFSET %s").format(
            sql.Identifier(schema), sql.Identifier(table),
            sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(o), sql.SQL("DESC" if desc else "ASC"))
                               for o in order))
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = {TIMEOUT_MS}")
            data = conn.execute(q, (limit, offset)).fetchall()
            shown = psycopg.ClientCursor(conn).mogrify(q, (limit, offset))
            total = None
            if schema == "lab":
                total = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table))).fetchone()["n"]
    return {"schema": schema, "table": table, "editable": schema == "lab" and meta["primary_key"] != [],
            **meta, "rows": [{k: _cell(v) for k, v in r.items()} for r in data], "total": total,
            "limit": limit, "offset": offset, "sql": shown}


# ------------------------------------------------------------- row editing
def _values(meta: dict, values: dict, insert: bool) -> dict:
    cols = {c["name"]: c for c in meta["columns"]}
    out = {}
    for k, v in values.items():
        if k not in cols:
            raise QueryError(f"unknown column {k!r}")
        c = cols[k]
        if c["generated"]:
            raise QueryError(f"column {k} is generated and cannot be set")
        if c["identity"] == "a" and (insert or v is not None):
            raise QueryError(f"column {k} is GENERATED ALWAYS AS IDENTITY and cannot be set")
        if insert and v in ("", None) and (c["default"] is not None or c["identity"]):
            continue                # let the default / identity fill it
        out[k] = None if v == "" else (json.dumps(v) if isinstance(v, (dict, list)) else v)
    return out


def _key(meta: dict, key: dict) -> dict:
    pk = meta["primary_key"]
    if not pk:
        raise QueryError("this table has no primary key; change it with SQL in the query console")
    if set(key) != set(pk):
        raise QueryError(f"key must give exactly the primary key columns: {', '.join(pk)}")
    return key


def _edit(table: str, build) -> dict:
    ensure()
    t0 = time.perf_counter()
    with lab_connect() as conn:
        meta = describe("lab", table, conn)
        q, params = build(meta)
        shown = psycopg.ClientCursor(conn).mogrify(q, params)
        try:
            with conn.transaction():
                cur = conn.execute(q, params)
                out = cur.fetchall()
        except psycopg.Error as e:
            raise QueryError(_error(e)) from e
    return {"sql": shown, "rowcount": len(out), "rows": [{k: _cell(v) for k, v in r.items()} for r in out],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}


def _ident_table(table: str) -> sql.Composable:
    if not NAME.match(table):
        raise QueryError("invalid table name")
    return sql.SQL("lab.{}").format(sql.Identifier(table))


def insert_row(table: str, values: dict) -> dict:
    def build(meta):
        v = _values(meta, values, insert=True)
        if not v:
            return sql.SQL("INSERT INTO {} DEFAULT VALUES RETURNING *").format(_ident_table(table)), []
        return (sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            _ident_table(table), sql.SQL(", ").join(map(sql.Identifier, v)),
            sql.SQL(", ").join(sql.Placeholder() * len(v))), list(v.values()))
    return _edit(table, build)


def _where(key: dict) -> sql.Composable:
    return sql.SQL(" AND ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in key)


def update_row(table: str, key: dict, values: dict) -> dict:
    def build(meta):
        k = _key(meta, key)
        v = _values(meta, values, insert=False)
        if not v:
            raise QueryError("nothing to change")
        q = sql.SQL("UPDATE {} SET {} WHERE {} RETURNING *").format(
            _ident_table(table), sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in v),
            _where(k))
        return q, [*v.values(), *k.values()]
    out = _edit(table, build)
    if not out["rowcount"]:
        raise QueryError("no row with that key (it may have been deleted)")
    return out


def delete_row(table: str, key: dict) -> dict:
    def build(meta):
        k = _key(meta, key)
        return sql.SQL("DELETE FROM {} WHERE {} RETURNING *").format(_ident_table(table), _where(k)), list(k.values())
    out = _edit(table, build)
    if not out["rowcount"]:
        raise QueryError("no row with that key (it may have been deleted)")
    return out


def changes(limit: int = 50) -> list[dict]:
    ensure()
    with _admin() as conn:
        return [{k: _cell(v) for k, v in r.items()} for r in conn.execute(
            "SELECT * FROM lab.change_log ORDER BY change_id DESC LIMIT %s", (limit,))]


# ------------------------------------------------------------- MongoDB lab
READ_OPS = {"find", "count", "aggregate", "listIndexes", "listCollections"}
WRITE_OPS = {"insertOne", "insertMany", "updateOne", "updateMany", "replaceOne", "deleteOne", "deleteMany",
             "createIndex", "dropIndex", "createCollection", "dropCollection"}


def _json(v):
    return json.loads(json_util.dumps(v, json_options=json_util.RELAXED_JSON_OPTIONS))


def _bson(v):
    # accept extended JSON such as {"$oid": ...} or {"$date": ...} from the browser
    return json_util.loads(json.dumps(v)) if v is not None else None


def run_mongo(op: str, collection: str | None = None, filter: dict | None = None, projection: dict | None = None,
              sort: dict | None = None, pipeline: list | None = None, limit: int = 10, skip: int = 0,
              document: dict | None = None, documents: list | None = None, update=None,
              keys: dict | None = None, unique: bool = False, name: str | None = None,
              validator: dict | None = None) -> dict:
    if op not in READ_OPS | WRITE_OPS:
        raise QueryError(f"unknown operation {op}")
    for part in (filter, projection, sort, pipeline, document, documents, update, validator):
        _check(part)
    ensure()
    mdb = mongo_lab()
    collection = collection or COLLECTION
    if not NAME.match(collection) or collection.startswith("system"):
        raise QueryError("invalid collection name")
    coll = mdb[collection]
    limit = max(1, min(limit, MAX_DOCS))
    f = _bson(filter) or {}
    t0 = time.perf_counter()
    try:
        if op == "find":
            cur = coll.find(f, projection or None, max_time_ms=TIMEOUT_MS).skip(max(0, skip)).limit(limit)
            if sort:
                cur = cur.sort(list(sort.items()))
            result = {"documents": list(cur)}
        elif op == "count":
            result = {"count": coll.count_documents(f, maxTimeMS=TIMEOUT_MS)}
        elif op == "aggregate":
            if not isinstance(pipeline, list):
                raise QueryError("aggregate needs a pipeline: a JSON array of stages")
            result = {"documents": list(coll.aggregate([*_bson(pipeline), {"$limit": limit}], maxTimeMS=TIMEOUT_MS))}
        elif op == "listIndexes":
            result = {"indexes": list(coll.list_indexes())}
        elif op == "listCollections":
            result = {"collections": [{"name": c["name"], "options": c.get("options", {})}
                                      for c in mdb.list_collections()]}
        elif op == "insertOne":
            if not isinstance(document, dict):
                raise QueryError("insertOne needs a document (a JSON object)")
            result = {"inserted_id": coll.insert_one(_bson(document)).inserted_id}
        elif op == "insertMany":
            if not isinstance(documents, list) or not documents or len(documents) > 1000:
                raise QueryError("insertMany needs documents: a JSON array of 1 to 1000 objects")
            result = {"inserted_ids": coll.insert_many(_bson(documents)).inserted_ids}
        elif op in ("updateOne", "updateMany"):
            if not update:
                raise QueryError(f"{op} needs an update, e.g. {{\"$set\": {{\"field\": 1}}}}")
            r = (coll.update_one if op == "updateOne" else coll.update_many)(f, _bson(update))
            result = {"matched": r.matched_count, "modified": r.modified_count}
        elif op == "replaceOne":
            if not isinstance(document, dict):
                raise QueryError("replaceOne needs the replacement document")
            r = coll.replace_one(f, _bson(document))
            result = {"matched": r.matched_count, "modified": r.modified_count}
        elif op in ("deleteOne", "deleteMany"):
            r = (coll.delete_one if op == "deleteOne" else coll.delete_many)(f)
            result = {"deleted": r.deleted_count}
        elif op == "createIndex":
            if not keys:
                raise QueryError('createIndex needs keys, e.g. {"dataset": 1}')
            result = {"index": coll.create_index(list(keys.items()), unique=unique, **({"name": name} if name else {}))}
        elif op == "dropIndex":
            if not name or name == "_id_":
                raise QueryError("dropIndex needs the index name (not _id_)")
            coll.drop_index(name)
            result = {"dropped": name}
        elif op == "createCollection":
            mdb.create_collection(collection, **({"validator": validator} if validator else {}))
            result = {"created": collection}
        else:  # dropCollection
            coll.drop()
            result = {"dropped": collection}
    except QueryError:
        raise
    except Exception as e:     # pymongo: WriteError (validator), DuplicateKeyError, OperationFailure, ...
        raise QueryError(str(e)) from e
    return {"database": mdb.name, "collection": collection, "op": op,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2), **_json(result)}


def mongo_collections() -> dict:
    ensure()
    mdb = mongo_lab()
    out = []
    for c in mdb.list_collections():
        coll = mdb[c["name"]]
        out.append({"name": c["name"], "documents": coll.count_documents({}),
                    "validator": _json(c.get("options", {}).get("validator")),
                    "indexes": _json(list(coll.list_indexes()))})
    return {"database": mdb.name, "collections": sorted(out, key=lambda x: x["name"])}
