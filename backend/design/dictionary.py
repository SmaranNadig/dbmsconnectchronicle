"""Data dictionary, read from the database catalog (never written by hand).

For every table of schemas cc and lab: description (COMMENT ON), columns with
type, nullability, default and description, constraints with their
definitions, indexes, and triggers. Also the views, the SQL functions and the
MongoDB collections (indexes and JSON-schema validator). Because it is read
from pg_catalog, the dictionary cannot drift from the schema.
"""
from __future__ import annotations

from psycopg.rows import dict_row

SCHEMAS = ("cc", "lab")
KIND = {"p": "primary key", "f": "foreign key", "u": "unique", "c": "check", "x": "exclusion", "t": "trigger"}

TABLES_SQL = """
SELECT n.nspname AS schema, c.relname AS name, obj_description(c.oid, 'pg_class') AS description,
       c.reltuples::bigint AS approx_rows, pg_total_relation_size(c.oid) AS total_bytes
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(s)s) AND c.relkind IN ('r', 'p') AND NOT c.relispartition
ORDER BY n.nspname, c.relname"""

COLUMNS_SQL = """
SELECT n.nspname AS schema, c.relname AS table, a.attnum AS position, a.attname AS name,
       format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS not_null,
       pg_get_expr(d.adbin, d.adrelid) AS default_expr, a.attgenerated <> '' AS generated,
       a.attidentity <> '' AS identity, col_description(c.oid, a.attnum) AS description
FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = ANY(%(s)s) AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY 1, 2, 3"""

CONSTRAINTS_SQL = """
SELECT n.nspname AS schema, c.relname AS table, k.conname AS name, k.contype AS kind,
       pg_get_constraintdef(k.oid) AS definition, k.condeferrable AS deferrable
FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(s)s) AND NOT c.relispartition
ORDER BY 1, 2, position(k.contype IN 'pufxct'), 3"""

INDEXES_SQL = """
SELECT n.nspname AS schema, t.relname AS table, i.relname AS name, pg_get_indexdef(x.indexrelid) AS definition,
       x.indisunique AS is_unique, x.indisprimary AS is_primary, pg_relation_size(i.oid) AS bytes,
       am.amname AS method
FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace JOIN pg_am am ON am.oid = i.relam
WHERE n.nspname = ANY(%(s)s) AND NOT t.relispartition
ORDER BY 1, 2, 3"""

TRIGGERS_SQL = """
SELECT n.nspname AS schema, c.relname AS table, g.tgname AS name, pg_get_triggerdef(g.oid) AS definition,
       g.tgconstraint <> 0 AS constraint_trigger
FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(s)s) AND NOT g.tgisinternal
ORDER BY 1, 2, 3"""

VIEWS_SQL = """
SELECT n.nspname AS schema, c.relname AS name, c.relkind = 'm' AS materialized,
       obj_description(c.oid, 'pg_class') AS description, pg_get_viewdef(c.oid, true) AS definition
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(s)s) AND c.relkind IN ('v', 'm')
ORDER BY 1, 2"""

FUNCTIONS_SQL = """
SELECT n.nspname AS schema, p.proname AS name, pg_get_function_identity_arguments(p.oid) AS arguments,
       pg_get_function_result(p.oid) AS returns, l.lanname AS language,
       CASE p.provolatile WHEN 'i' THEN 'immutable' WHEN 's' THEN 'stable' ELSE 'volatile' END AS volatility,
       p.prosecdef AS security_definer, obj_description(p.oid, 'pg_proc') AS description
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang
WHERE n.nspname = ANY(%(s)s) AND p.prokind IN ('f', 'p')
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
ORDER BY 1, 2"""


def postgres(conn) -> dict:
    cur = conn.cursor(row_factory=dict_row)
    p = {"s": list(SCHEMAS)}
    tables = {(t["schema"], t["name"]): {**t, "columns": [], "constraints": [], "indexes": [], "triggers": []}
              for t in cur.execute(TABLES_SQL, p).fetchall()}

    def attach(sql: str, key: str):
        for row in cur.execute(sql, p).fetchall():
            t = tables.get((row.pop("schema"), row.pop("table")))
            if t is not None:
                t[key].append(row)

    attach(COLUMNS_SQL, "columns")
    attach(CONSTRAINTS_SQL, "constraints")
    attach(INDEXES_SQL, "indexes")
    attach(TRIGGERS_SQL, "triggers")
    for t in tables.values():
        if t["approx_rows"] < 100_000:      # estimate missing or rough for small tables: count exactly
            t["approx_rows"] = cur.execute(f'SELECT count(*) AS n FROM "{t["schema"]}"."{t["name"]}"').fetchone()["n"]
        for k in t["constraints"]:
            k["kind"] = KIND.get(k["kind"], k["kind"])
        pk = next((k for k in t["constraints"] if k["kind"] == "primary key"), None)
        fks = [k["definition"] for k in t["constraints"] if k["kind"] == "foreign key"]
        for c in t["columns"]:
            c["key"] = ("PK" if pk and _in_key(c["name"], pk["definition"]) else "") + \
                       (" FK" if any(_in_key(c["name"], d.split("REFERENCES")[0]) for d in fks) else "")
            c["key"] = c["key"].strip()
    return {"tables": list(tables.values()),
            "views": cur.execute(VIEWS_SQL, p).fetchall(),
            "functions": cur.execute(FUNCTIONS_SQL, p).fetchall()}


def _in_key(col: str, definition: str) -> bool:
    inside = definition[definition.find("(") + 1: definition.find(")")]
    return col in [x.strip().strip('"') for x in inside.split(",")]


def mongo() -> list[dict]:
    from backend.lab import mongo_lab
    from backend.metadata import mongo_db

    out = []
    for db in (mongo_db(), mongo_lab()):
        out += _collections(db)
    return out


def _collections(db) -> list[dict]:
    out = []
    for info in db.list_collections():
        name = info["name"]
        if name.startswith("system."):
            continue
        coll = db[name]
        out.append({
            "database": db.name, "name": name,
            "documents": coll.estimated_document_count(),
            "validator": (info.get("options") or {}).get("validator"),
            "validation_level": (info.get("options") or {}).get("validationLevel"),
            "indexes": [{"name": i["name"], "keys": dict(i["key"]), "unique": bool(i.get("unique")),
                         "partial": i.get("partialFilterExpression")} for i in coll.list_indexes()],
        })
    return sorted(out, key=lambda c: c["name"])


def build(conn, with_mongo: bool = True) -> dict:
    pg = postgres(conn)
    data = {"postgres": pg, "mongo": mongo() if with_mongo else []}
    t = pg["tables"]
    data["summary"] = {
        "tables": len(t), "columns": sum(len(x["columns"]) for x in t),
        "described_tables": sum(bool(x["description"]) for x in t),
        "constraints": sum(len(x["constraints"]) for x in t),
        "foreign_keys": sum(k["kind"] == "foreign key" for x in t for k in x["constraints"]),
        "checks": sum(k["kind"] == "check" for x in t for k in x["constraints"]),
        "exclusions": sum(k["kind"] == "exclusion" for x in t for k in x["constraints"]),
        "indexes": sum(len(x["indexes"]) for x in t),
        "triggers": sum(len(x["triggers"]) for x in t),
        "views": len(pg["views"]), "functions": len(pg["functions"]),
        "mongo_collections": len(data["mongo"]),
    }
    return data
