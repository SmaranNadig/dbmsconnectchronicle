"""Dashboard endpoints' back end that show the DBMS at work: the live system
catalog, the integrity rules rejecting real bad writes, the project source
(SQL and NoSQL), the MongoDB store, and one routed query on the shards
compared with the coordinator.
"""
from __future__ import annotations

import threading
import time

import psycopg
from psycopg.rows import dict_row

from backend.config import PROJECT_ROOT, get_settings
from backend.metadata import COLLECTION, mongo_db


def _conn(**kw) -> psycopg.Connection:
    return psycopg.connect(get_settings().pg_dsn(), options="-c search_path=cc,public", row_factory=dict_row, **kw)


# --------------------------------------------------------------- integrity
# Each statement must be rejected by the database itself (constraint or
# trigger), not by application code. They run one per transaction and every
# transaction is rolled back, so nothing is ever written. The statements pick
# their rows from whatever is loaded, so they work on any database with two
# datasets (the real one and the test fixtures).
INTEGRITY_CASES = [
    {"rule": "CHECK", "what": "dataset codes must be lower-case identifiers",
     "sql": "INSERT INTO dataset (code, name) VALUES ('Bad Code!', 'not a valid code')"},
    {"rule": "UNIQUE", "what": "a dataset code can be registered only once",
     "sql": "INSERT INTO dataset (code, name)\n"
            "SELECT code, 'second copy of ' || code FROM dataset ORDER BY dataset_id LIMIT 1"},
    {"rule": "NOT NULL", "what": "every release needs its position in the release order",
     "sql": "INSERT INTO release (dataset_id, version_label)\n"
            "SELECT dataset_id, 'v9.9' FROM dataset ORDER BY dataset_id LIMIT 1"},
    {"rule": "FOREIGN KEY (composite)", "what": "an edge can never join neurons of two different datasets",
     "sql": "INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count, valid_rel,\n"
            "                        provenance_id, source_lines)\n"
            "SELECT a.dataset_id, a.dataset_neuron_id, b.dataset_neuron_id, 5, int4range(1, NULL), a.provenance_id, '{1}'\n"
            "FROM (SELECT * FROM dataset_neuron ORDER BY dataset_neuron_id LIMIT 1) a      -- neuron of one dataset\n"
            "JOIN LATERAL (SELECT * FROM dataset_neuron b WHERE b.dataset_id <> a.dataset_id\n"
            "              LIMIT 1) b ON true                                             -- neuron of another"},
    {"rule": "CHECK", "what": "synapse counts cannot be negative",
     "sql": "INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count, valid_rel,\n"
            "                        provenance_id, source_lines)\n"
            "SELECT dataset_id, pre_neuron_id, post_neuron_id, -5, int4range(1, NULL), provenance_id, '{1}'\n"
            "FROM connection LIMIT 1"},
    {"rule": "EXCLUDE (GiST, temporal)", "what": "a neuron cannot have two current values of one annotation "
                                                 "in overlapping releases",
     "sql": "INSERT INTO annotation (dataset_neuron_id, annotation_key, annotation_value, valid_rel, provenance_id)\n"
            "SELECT dataset_neuron_id, annotation_key, annotation_value || ' (conflicting)', valid_rel, provenance_id\n"
            "FROM annotation WHERE upper_inf(tx) LIMIT 1"},
    {"rule": "TRIGGER guard_history", "what": "history is append-only: an old fact cannot be edited in place",
     "sql": "UPDATE connection SET synapse_count = synapse_count + 1\n"
            "WHERE connection_id = (SELECT min(connection_id) FROM connection)"},
    {"rule": "TRIGGER guard_history", "what": "history is append-only: nothing can be deleted",
     "sql": "DELETE FROM annotation WHERE annotation_id = (SELECT min(annotation_id) FROM annotation)"},
    {"rule": "CHECK (cross-column)", "what": "a temporal diff must compare two releases of the same dataset",
     "sql": "INSERT INTO diff_run (mode, dataset_a, release_a, dataset_b, release_b, as_of)\n"
            "SELECT 'temporal', a.dataset_id, a.release_id, b.dataset_id, b.release_id, now()\n"
            "FROM release a JOIN release b ON b.dataset_id <> a.dataset_id   -- releases of two datasets\n"
            "LIMIT 1"},
]


def integrity_demo() -> list[dict]:
    out = []
    with _conn() as conn:
        for case in INTEGRITY_CASES:
            t0 = time.perf_counter()
            try:
                conn.execute("SET LOCAL statement_timeout = 10000")
                cur = conn.execute(case["sql"])
                result = {"rejected": False, "message": f"accepted ({cur.rowcount} rows) - rolled back"}
            except psycopg.Error as e:
                d = e.diag
                result = {"rejected": True, "sqlstate": d.sqlstate, "message": d.message_primary,
                          "detail": d.message_detail, "constraint": d.constraint_name, "table": d.table_name}
            finally:
                conn.rollback()
            out.append({**case, **result, "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return out


# ----------------------------------------------------------------- catalog
CATALOG_SQL = {
    "relations": """
        SELECT n.nspname AS schema, c.relname AS name,
               CASE c.relkind WHEN 'r' THEN 'table' WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized view' END AS kind,
               CASE WHEN c.relkind IN ('r', 'm') THEN greatest(c.reltuples, 0)::bigint END AS approx_rows,
               CASE WHEN c.relkind IN ('r', 'm') THEN pg_total_relation_size(c.oid) END AS bytes,
               (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0
                                                     AND NOT a.attisdropped) AS columns,
               obj_description(c.oid) AS comment
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('cc', 'er') AND c.relkind IN ('r', 'v', 'm')
        ORDER BY n.nspname, c.relkind, c.relname""",
    "constraints": """
        SELECT t.relname AS "table", c.conname AS name,
               CASE c.contype WHEN 'p' THEN 'PRIMARY KEY' WHEN 'f' THEN 'FOREIGN KEY' WHEN 'u' THEN 'UNIQUE'
                              WHEN 'c' THEN 'CHECK' WHEN 'x' THEN 'EXCLUDE' END AS type,
               pg_get_constraintdef(c.oid) AS definition
        FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'cc' ORDER BY t.relname, c.contype, c.conname""",
    "indexes": """
        SELECT i.tablename AS "table", i.indexname AS name, am.amname AS method,
               pg_relation_size(format('%I.%I', i.schemaname, i.indexname)::regclass) AS bytes, i.indexdef AS definition
        FROM pg_indexes i JOIN pg_class ic ON ic.relname = i.indexname
        JOIN pg_namespace n ON n.oid = ic.relnamespace AND n.nspname = i.schemaname
        JOIN pg_am am ON am.oid = ic.relam
        WHERE i.schemaname = 'cc' ORDER BY i.tablename, i.indexname""",
    "triggers": """
        SELECT c.relname AS "table", t.tgname AS name, p.proname AS function, pg_get_triggerdef(t.oid) AS definition
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = 'cc' AND NOT t.tgisinternal ORDER BY c.relname, t.tgname""",
    "functions": """
        SELECT n.nspname AS schema, p.proname AS name, pg_get_function_identity_arguments(p.oid) AS arguments,
               pg_get_function_result(p.oid) AS returns, l.lanname AS language
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang
        WHERE n.nspname IN ('cc', 'er')
          AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')  -- skip btree_gist
        ORDER BY n.nspname, p.proname""",
}


def catalog() -> dict:
    with _conn() as conn:
        return {k: conn.execute(sql).fetchall() for k, sql in CATALOG_SQL.items()}


# ------------------------------------------------------------------ source
SOURCES = {
    **{f"backend/db/sql/{p.name}": "SQL migration" for p in sorted((PROJECT_ROOT / "backend/db/sql").glob("*.sql"))},
    "backend/metadata.py": "MongoDB: document model, indexes, sync and cross-store consistency check",
    "config/schema_mappings/hemibrain.yaml": "schema mapping: source columns to the canonical model",
    "backend/etl/loader.py": "ETL: one transaction per release, staging tables, validation, delta merge",
    "backend/distributed/partition.py": "sharding: hash, anatomical and community partitioning",
    "backend/distributed/deploy.py": "sharding: copy each strategy to the 3 shards and verify",
    "backend/distributed/router.py": "sharding: query router (scatter-gather) and coordinator baseline",
    "backend/console.py": "this dashboard's read-only SQL and MongoDB consoles",
}


def source_list() -> list[dict]:
    return [{"path": p, "about": a, "lines": (PROJECT_ROOT / p).read_text(encoding="utf-8").count("\n")}
            for p, a in SOURCES.items()]


def source_text(path: str) -> str | None:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8") if path in SOURCES else None


# ----------------------------------------------------------------- MongoDB
SAMPLE_DOC = "hemibrain:1688868386"


def mongo_overview() -> dict:
    mdb = mongo_db()
    coll = mdb[COLLECTION]
    stats = mdb.command("collStats", COLLECTION)
    with _conn() as conn:
        syncs = conn.execute("""
            SELECT d.code AS dataset, r.version_label AS release, s.documents_written, s.records_written,
                   round(extract(epoch FROM s.finished_at - s.started_at)::numeric, 1)::float8 AS seconds
            FROM metadata_sync s JOIN release r USING (release_id) JOIN dataset d USING (dataset_id)
            WHERE s.status = 'completed'
              AND s.metadata_sync_id IN (SELECT max(metadata_sync_id) FROM metadata_sync
                                         WHERE status = 'completed' GROUP BY release_id)
            ORDER BY d.dataset_id, r.release_order""").fetchall()
        pg_row = conn.execute("""SELECT dataset_neuron_id, source_neuron_id, metadata_doc_id FROM dataset_neuron
                                 WHERE metadata_doc_id = %s""", (SAMPLE_DOC,)).fetchone()
    return {
        "server_version": mdb.client.server_info()["version"], "database": mdb.name, "collection": COLLECTION,
        "documents": stats["count"], "data_bytes": stats["size"], "storage_bytes": stats["storageSize"],
        "avg_document_bytes": stats.get("avgObjSize"), "index_bytes": stats["totalIndexSize"],
        "indexes": [{"name": k, "keys": v["key"], "unique": bool(v.get("unique"))}
                    for k, v in coll.index_information().items()],
        "syncs": syncs, "sample_id": SAMPLE_DOC, "sample_postgres_row": pg_row,
        "sample": coll.find_one({"_id": SAMPLE_DOC}),
    }


# ------------------------------------------------------------ distribution
_routers: dict = {}
_lock = threading.Lock()


def _targets(strategy: str):
    # building a router loads the placement map; keep one per strategy
    from backend.distributed.router import Coordinator, Router
    if strategy not in _routers:
        _routers[strategy] = Router(strategy)
    if "coordinator" not in _routers:
        _routers["coordinator"] = Coordinator()
    return _routers[strategy], _routers["coordinator"]


def warm_routers() -> None:
    """Build the routers ahead of the first request (loading the placement maps
    and the coordinator baseline takes several seconds). Started in the
    background by the API when CC_WARM_ROUTERS=1; errors only mean the first
    query will build them instead."""
    for strategy in ("anatomical", "hash", "community"):
        try:
            with _lock:
                _targets(strategy)
        except Exception:  # noqa: BLE001 - e.g. no verified deployment in this database
            return


def routed_query(strategy: str, kind: str, release: int, neuron: int | None = None,
                 region: str | None = None) -> dict:
    from backend.distributed.router import Router
    with _lock:
        router, coord = _targets(strategy)
        if kind in ("neighbours_out", "neighbours_in", "two_hop") and neuron not in router.owner:
            raise ValueError("that neuron is not in the deployed partition")
        call = {"neighbours_out": lambda t: t.neighbours(neuron, release, "out"),
                "neighbours_in": lambda t: t.neighbours(neuron, release, "in"),
                "two_hop": lambda t: t.two_hop(neuron, release),
                "region": lambda t: t.region(region, release),
                "summary": lambda t: t.summary(release)}[kind]
        rows = call(router)
        shard_info = dict(router.last)
        base = call(coord)
        base_info = dict(coord.last)
    template = {"neighbours_out": Router.NEIGHBOURS.format(me="pre_neuron_id", other="post_neuron_id"),
                "neighbours_in": Router.NEIGHBOURS.format(me="post_neuron_id", other="pre_neuron_id"),
                "two_hop": Router.TWO_HOP, "region": Router.REGION, "summary": Router.SUMMARY}[kind]
    return {
        "strategy": strategy, "kind": kind, "shard_ids": shard_info["shard_ids"],
        "shards_touched": shard_info["shards"], "rows": shard_info["rows"],
        "shard_ms": round(shard_info["latency_ms"], 2), "coordinator_ms": round(base_info["latency_ms"], 2),
        "matches_coordinator": sorted(map(tuple, rows)) == sorted(map(tuple, base)),
        "shard_sql": "\n".join(l[8:] if l.startswith(" " * 8) else l
                               for l in template.format(s=router.schema).strip("\n").splitlines()),
        "sample": [list(r) for r in rows[:15]],
    }
