"""REST API (FastAPI). Thin layer: every endpoint calls a parameterized SQL
query or a cc.* SQL function, so the database stays the system of record.

    uvicorn backend.main:app --reload      # docs at http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import json
import os
import threading
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from bson import json_util
from fastapi import Depends, FastAPI, HTTPException, Query, Header
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from backend import console, lab, showcase, workspace
from backend.config import PROJECT_ROOT, get_settings
from backend.metadata import mongo_db

pool: ConnectionPool | None = None
MAX_LIMIT = 500


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool
    pool = ConnectionPool(get_settings().pg_dsn(), min_size=1, max_size=8, open=True,
                          kwargs={"options": "-c search_path=cc,public", "row_factory": dict_row})
    if os.environ.get("CC_WARM_ROUTERS") == "1":
        threading.Thread(target=showcase.warm_routers, daemon=True).start()
    from backend.jobs import start_workers, stop_workers
    start_workers()
    yield
    stop_workers()
    pool.close()


app = FastAPI(title="ConnectomeChronicle API", version="0.1.0", lifespan=lifespan)
from backend import analysis  # registers model and matching routes on the workspace router
app.include_router(workspace.router)
from backend.jobs import router as jobs_router
app.include_router(jobs_router)


def db():
    assert pool is not None
    with pool.connection() as conn:
        yield conn


def page(limit: int = Query(50, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0)) -> dict:
    return {"limit": limit, "offset": offset}


def _neuron_or_404(conn, neuron_id: int) -> dict:
    row = conn.execute("""SELECT dn.dataset_neuron_id, d.code AS dataset, dn.source_neuron_id,
                                 r.version_label AS first_release, dn.metadata_doc_id
                          FROM dataset_neuron dn JOIN dataset d USING (dataset_id)
                          JOIN release r ON r.release_id = dn.first_release_id
                          WHERE dn.dataset_neuron_id = %s""", (neuron_id,)).fetchone()
    if not row:
        raise HTTPException(404, "neuron not found")
    return row


def _release_id(conn, dataset: str, label: str) -> int:
    row = conn.execute("""SELECT release_id FROM release JOIN dataset USING (dataset_id)
                          WHERE code = %s AND version_label = %s AND ingestion_status='loaded'""", (dataset, label)).fetchone()
    if not row:
        raise HTTPException(404, f"release {dataset} {label} not found")
    return row["release_id"]


@app.get("/api/v1/health")
def health(conn=Depends(db)):
    rev = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
    return {"status": "ok", "schema_revision": rev["version_num"] if rev else None}


@app.get("/api/v1/datasets")
def datasets(conn=Depends(db)):
    return conn.execute("""SELECT d.code, d.name, d.organism, d.source_url, d.license,
                                  count(r.*) AS releases
                           FROM dataset d LEFT JOIN release r USING (dataset_id)
                           GROUP BY d.dataset_id ORDER BY d.code""").fetchall()


RELEASES_SQL = """
    SELECT r.release_id, d.code AS dataset, r.version_label, r.release_order, r.release_date,
           r.ingestion_status, r.loaded_at, r.manifest_hash, s.neurons, s.connections
    FROM release r JOIN dataset d USING (dataset_id) LEFT JOIN release_stats s USING (release_id)
    WHERE %(d)s::text IS NULL OR d.code = %(d)s
    ORDER BY d.code, r.release_order"""


@app.get("/api/v1/releases")
def releases(dataset: str | None = None, conn=Depends(db)):
    # counts come from the release_stats materialized view, refreshed in each load transaction
    return conn.execute(RELEASES_SQL, {"d": dataset}).fetchall()


@app.get("/api/v1/overview")
def overview(conn=Depends(db)):
    """Everything the dashboard's first page shows, from the database itself."""
    out = {
        "datasets": conn.execute("""SELECT d.dataset_id, d.code, d.name, d.description, d.organism, d.organization,
                                           d.repository, d.source_url, d.license, d.data_formats
                                    FROM dataset d WHERE EXISTS (SELECT 1 FROM release r
                                        WHERE r.dataset_id = d.dataset_id AND r.ingestion_status = 'loaded')
                                    ORDER BY d.dataset_id""").fetchall(),
        "releases": [r for r in conn.execute(RELEASES_SQL, {"d": None}).fetchall()
                     if r["ingestion_status"] == "loaded"],
        "source_files": conn.execute("""SELECT d.code AS dataset, r.version_label, f.archive_path, f.member_path,
                                               f.size_bytes, f.row_count, f.sha256, f.source_url
                                        FROM source_file f JOIN release r USING (release_id)
                                        JOIN dataset d USING (dataset_id)
                                        WHERE r.ingestion_status = 'loaded'
                                        ORDER BY d.code, r.release_order, f.source_file_id""").fetchall(),
        "ingestion_runs": conn.execute("""SELECT i.run_id, d.code AS dataset, r.version_label, i.status,
                                                 extract(epoch FROM i.finished_at - i.started_at) AS seconds,
                                                 left(i.error_message, 160) AS error
                                          FROM ingestion_run i JOIN release r USING (release_id)
                                          JOIN dataset d USING (dataset_id) ORDER BY i.run_id""").fetchall(),
        "storage": conn.execute("""SELECT (SELECT count(*) FROM connection) AS connection_rows_stored,
                                          (SELECT sum(connections) FROM release_stats) AS rows_if_snapshots,
                                          (SELECT count(*) FROM annotation) AS annotation_rows,
                                          (SELECT count(*) FROM dataset_neuron) AS source_neurons,
                                          pg_database_size(current_database()) AS database_bytes,
                                          (SELECT count(*) FROM information_schema.tables
                                           WHERE table_schema = 'cc' AND table_type = 'BASE TABLE') AS tables,
                                          (SELECT count(*) FROM information_schema.views
                                           WHERE table_schema IN ('cc', 'er')) AS views""").fetchone(),
        "schema_revision": conn.execute("SELECT version_num FROM public.alembic_version").fetchone()["version_num"],
        "metadata_syncs": conn.execute("""SELECT d.code AS dataset, r.version_label, s.documents_written,
                                                 s.records_written, s.stats->'rows_for_unknown_neurons' AS unknown_rows
                                          FROM metadata_sync s JOIN release r USING (release_id)
                                          JOIN dataset d USING (dataset_id)
                                          WHERE s.metadata_sync_id IN (SELECT max(metadata_sync_id) FROM metadata_sync
                                                                       WHERE status = 'completed' GROUP BY release_id)
                                          ORDER BY d.code, r.release_order""").fetchall(),
    }
    try:
        coll = mongo_db()["neuron_source_record"]
        out["mongo_documents"] = {r["_id"]: r["n"] for r in coll.aggregate(
            [{"$group": {"_id": "$dataset", "n": {"$sum": 1}}}])}
    except Exception as exc:  # noqa: BLE001
        out["mongo_documents"] = {"unavailable": type(exc).__name__}
    synthetic = ["synthetic" in d["name"].lower() or "fixture" in d["name"].lower() for d in out["datasets"]]
    out["data_mode"] = "synthetic" if synthetic and all(synthetic) else "mixed" if any(synthetic) else "public" if synthetic else "empty"
    out["database"] = get_settings().postgres_db
    return out


@app.get("/api/v1/regions")
def regions(release: int, limit: int = Query(15, ge=1, le=100), conn=Depends(db)):
    """Brain regions of one release ranked by synapses (each synapse counted once, on its pre side)."""
    return conn.execute("""SELECT l.code, l.name, l.super_region, count(*) AS neurons,
                                  sum(s.pre_synapses) AS synapses
                           FROM neuron_region_synapses s JOIN brain_region_label l USING (brain_region_id)
                           WHERE s.release_id = %s GROUP BY l.code, l.name, l.super_region
                           ORDER BY synapses DESC LIMIT %s""", (release, limit)).fetchall()


@app.get("/api/v1/neurons")
def neurons(dataset: str, source_id: str | None = None, cell_type: str | None = None,
            release: str | None = None, as_of: datetime | None = None, region: str | None = None,
            p: dict = Depends(page), conn=Depends(db)):
    """Existence, filter and returned type share the same release and knowledge time."""
    return workspace.search_neurons(conn, dataset, source_id, cell_type, release, as_of, region, **p)


@app.get("/api/v1/neurons/{neuron_id}")
def neuron(neuron_id: int, release: str | None = None, as_of: datetime | None = None, conn=Depends(db)):
    as_of = workspace.instant(as_of)
    n = _neuron_or_404(conn, neuron_id)
    rel = _release_id(conn, n["dataset"], release) if release else None
    states = conn.execute("""
        SELECT s.release, s.present, s.annotations
        FROM release r, LATERAL neuron_at_release(%(n)s, r.release_id, coalesce(%(t)s, now())) s
        WHERE r.dataset_id = (SELECT dataset_id FROM dataset_neuron WHERE dataset_neuron_id = %(n)s)
          AND r.ingestion_status = 'loaded' AND (%(r)s::int IS NULL OR r.release_id = %(r)s)
        ORDER BY r.release_order""", {"n": neuron_id, "t": as_of, "r": rel}).fetchall()
    return {**n, "states": states, **_source_records(n["metadata_doc_id"])}


def _source_records(doc_id: str | None) -> dict:
    """Original source rows from MongoDB. PostgreSQL stays authoritative, so the
    neuron is still returned when MongoDB is unreachable; the status says so."""
    if doc_id is None:
        return {"source_records": None, "source_records_status": "no document reference"}
    try:
        doc = mongo_db()["neuron_source_record"].find_one({"_id": doc_id}, {"releases": 1})
    except Exception as exc:  # noqa: BLE001 - any MongoDB failure degrades to status
        return {"source_records": None, "source_records_status": f"unavailable: {type(exc).__name__}"}
    if doc is None:
        return {"source_records": None, "source_records_status": "not synced"}
    return {"source_records": sorted(doc["releases"].values(), key=lambda r: r["ingestion_run_id"] or 0),
            "source_records_status": "ok"}


@app.get("/api/v1/neurons/{neuron_id}/connections")
def neuron_connections(neuron_id: int, release: str, as_of: datetime | None = None,
                       min_weight: int = Query(1, ge=0), p: dict = Depends(page), conn=Depends(db)):
    as_of = workspace.instant(as_of)
    n = _neuron_or_404(conn, neuron_id)
    return conn.execute("""SELECT * FROM connections_at_release(%s, %s, coalesce(%s, now()), %s)
                           ORDER BY synapse_count DESC LIMIT %s OFFSET %s""",
                        (neuron_id, _release_id(conn, n["dataset"], release), as_of, min_weight,
                         p["limit"], p["offset"])).fetchall()


@app.get("/api/v1/neurons/{neuron_id}/regions")
def neuron_regions(neuron_id: int, release: str, conn=Depends(db)):
    n = _neuron_or_404(conn, neuron_id)
    return conn.execute("""SELECT l.code, l.name, s.pre_synapses, s.post_synapses
                           FROM neuron_region_synapses s JOIN brain_region_label l USING (brain_region_id)
                           WHERE s.dataset_neuron_id = %s AND s.release_id = %s
                           ORDER BY s.pre_synapses + s.post_synapses DESC""",
                        (neuron_id, _release_id(conn, n["dataset"], release))).fetchall()


@app.get("/api/v1/neurons/{neuron_id}/history")
def neuron_history(neuron_id: int, conn=Depends(db)):
    _neuron_or_404(conn, neuron_id)
    return conn.execute("SELECT * FROM annotation_history(%s)", (neuron_id,)).fetchall()


@app.get("/api/v1/neurons/{neuron_id}/provenance")
def neuron_provenance(neuron_id: int, conn=Depends(db)):
    _neuron_or_404(conn, neuron_id)
    return conn.execute("SELECT trace_provenance('dataset_neuron', %s) AS trace", (neuron_id,)).fetchone()["trace"]


@app.get("/api/v1/provenance/{table}/{record_id}")
def provenance(table: Literal["connection", "annotation", "neuron_version", "dataset_neuron"], record_id: int,
               conn=Depends(db)):
    t = conn.execute("SELECT trace_provenance(%s, %s) AS t", (table, record_id)).fetchone()["t"]
    if t is None:
        raise HTTPException(404, "record not found")
    return t


@app.get("/api/v1/mappings")
def mappings(dataset: str | None = None, method: str | None = None,
             status: Literal["proposed", "approved", "rejected", "unresolved"] | None = None,
             p: dict = Depends(page), conn=Depends(db)):
    return conn.execute("""
        SELECT m.mapping_id, d.code AS dataset, dn.source_neuron_id, m.canonical_neuron_id, c.label AS canonical_label,
               m.method, m.confidence, m.review_status, m.evidence, m.mapped_at, m.reviewed_by
        FROM neuron_mapping m JOIN dataset_neuron dn USING (dataset_neuron_id) JOIN dataset d USING (dataset_id)
        LEFT JOIN canonical_neuron c USING (canonical_neuron_id)
        WHERE (%(d)s::text IS NULL OR d.code = %(d)s) AND (%(m)s::text IS NULL OR m.method = %(m)s)
          AND (%(s)s::text IS NULL OR m.review_status = %(s)s)
        ORDER BY m.mapping_id LIMIT %(limit)s OFFSET %(offset)s""",
        {"d": dataset, "m": method, "s": status, **p}).fetchall()


class Review(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=100)


@app.post("/api/v1/mappings/{mapping_id}/review")
def review(mapping_id: int, body: Review, conn=Depends(db), x_review_token: str | None = Header(None)):
    workspace.require_review_token(x_review_token)
    try:
        conn.execute("SELECT review_mapping(%s, %s, %s)", (mapping_id, body.decision, body.reviewer))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise HTTPException(409, str(exc).splitlines()[0]) from exc
    return conn.execute("SELECT mapping_id, review_status, reviewed_at FROM neuron_mapping WHERE mapping_id = %s",
                        (mapping_id,)).fetchone()


class DiffRequest(BaseModel):
    dataset: str
    release_a: str
    release_b: str
    as_of: datetime | None = None
    refresh: bool = False


@app.post("/api/v1/diffs")
def create_diff(body: DiffRequest, conn=Depends(db)):
    # Without an explicit as_of the database's own transaction time is used: a
    # timestamp from this machine's clock can be a few ms ahead of the database
    # container's clock and would be rejected as "in the future".
    ts = workspace.instant(body.as_of) if body.as_of is not None else None
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    a, b = _release_id(conn, body.dataset, body.release_a), _release_id(conn, body.dataset, body.release_b)
    if a == b:
        raise HTTPException(422, "select two different releases")
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(f'diff:{a}:{b}',))
    # A stable knowledge revision makes default requests reusable without changing explicit as_of semantics.
    from backend.revisions import source_revision
    revision = source_revision(conn)
    if body.as_of is None and not body.refresh:
        existing = conn.execute('''SELECT diff_run_id FROM diff_run WHERE release_a=%s AND release_b=%s
            AND status='succeeded' AND stats->>'source_revision'=%s ORDER BY diff_run_id DESC LIMIT 1''', (a,b,revision)).fetchone()
        if existing:
            return get_diff(existing['diff_run_id'],conn)
    run = conn.execute("SELECT run_temporal_diff(%s, %s, coalesce(%s::timestamptz, now())) AS id",
                       (a, b, ts)).fetchone()["id"]
    if body.as_of is None:
        conn.execute("UPDATE diff_run SET stats=stats || jsonb_build_object('source_revision',%s::text) WHERE diff_run_id=%s",(revision,run))
    conn.commit()
    return get_diff(run, conn)


@app.get("/api/v1/diffs")
def list_diffs(conn=Depends(db)):
    return conn.execute("""SELECT r.diff_run_id, r.mode, d.code AS dataset, ra.version_label AS release_a,
                                  rb.version_label AS release_b, r.as_of, r.status, r.stats
                           FROM diff_run r JOIN dataset d ON d.dataset_id = r.dataset_a
                           JOIN release ra ON ra.release_id = r.release_a JOIN release rb ON rb.release_id = r.release_b
                           ORDER BY r.diff_run_id DESC""").fetchall()


@app.get("/api/v1/diffs/{diff_id}")
def get_diff(diff_id: int, conn=Depends(db)):
    row = conn.execute("""SELECT diff_run_id, mode, as_of, status, started_at, finished_at, stats
                          FROM diff_run WHERE diff_run_id = %s""", (diff_id,)).fetchone()
    if not row:
        raise HTTPException(404, "diff not found")
    return row


@app.get("/api/v1/diffs/{diff_id}/events")
def diff_events(diff_id: int, entity_type: Literal["neuron", "connection", "annotation"] | None = None,
                change_type: Literal["added", "removed", "modified"] | None = None,
                order: Literal["id", "magnitude"] = "id", p: dict = Depends(page), conn=Depends(db)):
    get_diff(diff_id, conn)
    order_sql = "abs(weight_delta) DESC, diff_event_id" if order == "magnitude" else "diff_event_id"
    return conn.execute(f"""
        SELECT * FROM diff_event_detail WHERE diff_run_id = %(r)s
          AND (%(e)s::text IS NULL OR entity_type = %(e)s) AND (%(c)s::text IS NULL OR change_type = %(c)s)
        ORDER BY {order_sql} LIMIT %(limit)s OFFSET %(offset)s""",
        {"r": diff_id, "e": entity_type, "c": change_type, **p}).fetchall()


@app.get("/api/v1/mappings/summary")
def mappings_summary(conn=Depends(db)):
    return {
        "by_status": conn.execute("""SELECT d.code AS dataset, m.method, m.review_status, count(*) AS n,
                                            round(avg(m.confidence), 3) AS avg_confidence
                                     FROM neuron_mapping m JOIN dataset_neuron dn USING (dataset_neuron_id)
                                     JOIN dataset d USING (dataset_id)
                                     GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall(),
        "unresolved_reasons": conn.execute("""SELECT evidence->>'reason' AS reason, count(*) AS n
                                              FROM neuron_mapping WHERE review_status = 'unresolved'
                                              GROUP BY 1 ORDER BY 2 DESC""").fetchall(),
        "confident_examples": conn.execute("""
            SELECT dn.source_neuron_id AS flywire_id, c.label AS hemibrain_identity, m.confidence, m.evidence
            FROM neuron_mapping m JOIN dataset_neuron dn USING (dataset_neuron_id)
            JOIN canonical_neuron c USING (canonical_neuron_id)
            WHERE m.method = 'published_type_crosswalk' AND m.confidence = 1
            ORDER BY m.mapping_id LIMIT 8""").fetchall(),
    }


@app.get("/api/v1/distribution")
def distribution(conn=Depends(db)):
    deployments = conn.execute("""
        SELECT p.code AS strategy, d.shard_id, d.neurons, d.connections, d.mirrors, d.bytes_on_shard,
               d.seconds, d.verified, d.deployed_at
        FROM shard_deployment d JOIN partition_strategy p USING (strategy_id)
        WHERE d.batch_id IN (SELECT batch_id FROM active_deployment)
        ORDER BY p.strategy_id, d.shard_id""").fetchall()
    strategies = conn.execute("SELECT code, description, parameters FROM partition_strategy "
                              "ORDER BY strategy_id").fetchall()
    per: dict = defaultdict(lambda: {"owned": 0, "mirrors": 0, "bytes": 0, "verified": True, "shards": []})
    for d in deployments:
        s = per[d["strategy"]]
        s["owned"] += d["connections"]
        s["mirrors"] += d["mirrors"]
        s["bytes"] += d["bytes_on_shard"] or 0
        s["verified"] = s["verified"] and d["verified"]
        s["shards"].append(d)
    for s in per.values():
        if s["owned"]:
            s["cut_fraction"] = round(s["mirrors"] / s["owned"], 4)
            s["storage_overhead"] = round((s["owned"] + s["mirrors"]) / s["owned"], 3)
            s["max_share"] = round(max(x["connections"] for x in s["shards"]) / s["owned"], 4)
    from backend.revisions import source_revision, placement_revision
    revision = source_revision(conn)
    for row in conn.execute('''SELECT p.code,p.strategy_id,b.batch_id,b.source_revision,b.placement_revision
        FROM active_deployment a JOIN deployment_batch b USING(batch_id) JOIN partition_strategy p ON p.strategy_id=a.strategy_id''').fetchall():
        code,sid,bid,src,placement = (row[k] for k in ('code','strategy_id','batch_id','source_revision','placement_revision'))
        if code in per:
            per[code]['generation'] = bid
            per[code]['fresh'] = src.strip()==revision and placement.strip()==placement_revision(conn,sid)
    return {"strategies": strategies, "deployments": dict(per),
            "shards": conn.execute("SELECT shard_id, name, host, port FROM shard ORDER BY shard_id").fetchall()}


BENCH_DIR = PROJECT_ROOT / "results" / "benchmarks"
TEST_REPORT = PROJECT_ROOT / "results" / "tests" / "junit.xml"


@app.get("/api/v1/benchmarks/latest")
def latest_benchmark(conn=Depends(db)):
    files = sorted(BENCH_DIR.glob("bench_*.json"))
    if not files:
        raise HTTPException(404, "no benchmark has been run")
    from backend.revisions import source_revision
    report = json.loads(files[-1].read_text(encoding="utf-8"))
    return {"file": files[-1].name, **report, "report_origin": "saved benchmark",
            "matches_current_data": report.get('data_revision') == source_revision(conn)}


@app.get("/api/v1/tests/latest")
def latest_tests():
    """Summary of the last `pytest --junitxml=results/tests/junit.xml` run."""
    if not TEST_REPORT.exists():
        raise HTTPException(404, "no test report; run pytest with --junitxml=results/tests/junit.xml")
    root = ET.parse(TEST_REPORT).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    files: dict = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0, "tests": []})
    for case in suite.iter("testcase"):
        outcome = ("failed" if case.find("failure") is not None or case.find("error") is not None
                   else "skipped" if case.find("skipped") is not None else "passed")
        f = files[case.get("classname", "").rsplit(".", 1)[-1]]
        f[outcome] += 1
        f["tests"].append({"name": case.get("name"), "outcome": outcome, "seconds": float(case.get("time", 0))})
    return {"timestamp": suite.get("timestamp"), "tests": int(suite.get("tests")),
            "failures": int(suite.get("failures")) + int(suite.get("errors")),
            "skipped": int(suite.get("skipped")), "seconds": float(suite.get("time")), "files": files}


# ------------------------------------------------- consoles and DBMS showcase
class SqlQuery(BaseModel):
    sql: str = Field(..., max_length=20_000)
    explain: Literal["plan", "analyze"] | None = None
    # read: SELECT only; trial: one write on the real tables, rolled back;
    # lab: a script in schema lab, committed unless rollback (DATA_EDITOR_PLAN.md)
    target: Literal["read", "trial", "lab"] = "read"
    rollback: bool = False


@app.post("/api/v1/query/sql")
def query_sql(body: SqlQuery, x_review_token: str | None = Header(None)):
    """Run SQL as cc_reader, cc_tryout or cc_lab (see backend/console.py, backend/lab.py)."""
    try:
        if body.target == "lab":
            workspace.require_review_token(x_review_token)
            if body.explain:
                raise console.QueryError("for EXPLAIN in the lab, write EXPLAIN in the script")
            return lab.run_script(body.sql, body.rollback)
        return console.run_sql(body.sql, body.explain, body.target)
    except console.QueryError as e:
        raise HTTPException(400, str(e)) from e


class MongoQuery(BaseModel):
    op: Literal["find", "count", "aggregate", "listIndexes", "listCollections", "insertOne", "insertMany",
                "updateOne", "updateMany", "replaceOne", "deleteOne", "deleteMany", "createIndex", "dropIndex",
                "createCollection", "dropCollection"] = "find"
    # main: the real store, find / count / aggregate only; lab: the editable copy
    target: Literal["main", "lab"] = "main"
    collection: str | None = Field(None, max_length=63)
    filter: dict | None = None
    projection: dict | None = None
    sort: dict | None = None
    pipeline: list | None = None
    limit: int = Field(10, ge=1, le=console.MAX_DOCS)
    skip: int = Field(0, ge=0)
    document: dict | None = None
    documents: list | None = None
    update: dict | list | None = None
    keys: dict | None = None
    unique: bool = False
    name: str | None = Field(None, max_length=127)
    validator: dict | None = None


@app.post("/api/v1/query/mongo")
def query_mongo(body: MongoQuery, x_review_token: str | None = Header(None)):
    try:
        if body.target == "lab":
            if body.op in lab.WRITE_OPS:
                workspace.require_review_token(x_review_token)
            return lab.run_mongo(**body.model_dump(exclude={"target"}))
        if body.op not in ("find", "count", "aggregate"):
            raise console.QueryError(f"the real document store is read-only: {body.op} is allowed only on the "
                                     "Lab target")
        return console.run_mongo(body.op, body.filter, body.projection, body.sort, body.pipeline, body.limit)
    except console.QueryError as e:
        raise HTTPException(400, str(e)) from e


# ------------------------------------------------- data editor (lab)
def _lab(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except console.QueryError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/v1/lab/status")
def lab_status():
    lab.ensure()
    return lab.status()


@app.post("/api/v1/lab/reset")
def lab_reset(x_review_token: str | None = Header(None)):
    """Drop and rebuild schema lab and the MongoDB lab from the real data."""
    workspace.require_review_token(x_review_token)
    return lab.reset()


@app.get("/api/v1/lab/tables")
def lab_tables(schema: Literal["lab", "cc", "er"] = "lab"):
    return _lab(lab.tables, schema)


@app.get("/api/v1/lab/tables/{schema}/{table}")
def lab_rows(schema: Literal["lab", "cc", "er"], table: str, limit: int = Query(50, ge=1, le=MAX_LIMIT),
             offset: int = Query(0, ge=0), sort: str | None = None, desc: bool = False):
    return _lab(lab.rows, schema, table, limit, offset, sort, desc)


class RowEdit(BaseModel):
    key: dict = Field(default_factory=dict)       # primary key columns, for update / delete
    values: dict = Field(default_factory=dict)    # column -> value, for insert / update


@app.post("/api/v1/lab/tables/lab/{table}/{action}")
def lab_edit(table: str, action: Literal["insert", "update", "delete"], body: RowEdit,
             x_review_token: str | None = Header(None)):
    """Insert, update or delete one row of a lab table; returns the SQL that ran."""
    workspace.require_review_token(x_review_token)
    if action == "insert":
        return _lab(lab.insert_row, table, body.values)
    if action == "update":
        return _lab(lab.update_row, table, body.key, body.values)
    return _lab(lab.delete_row, table, body.key)


@app.get("/api/v1/lab/changes")
def lab_changes(limit: int = Query(50, ge=1, le=MAX_LIMIT)):
    return lab.changes(limit)


@app.get("/api/v1/lab/mongo/collections")
def lab_mongo_collections():
    return lab.mongo_collections()


@app.get("/api/v1/catalog")
def system_catalog():
    return showcase.catalog()


@app.post("/api/v1/demo/integrity")
def demo_integrity():
    """Send deliberately invalid writes; each is rejected by a constraint or trigger and rolled back."""
    return showcase.integrity_demo()


@app.get("/api/v1/source")
def source_files():
    return showcase.source_list()


@app.get("/api/v1/source/file")
def source_file(path: str):
    text = showcase.source_text(path)
    if text is None:
        raise HTTPException(404, "not one of the published source files")
    return {"path": path, "text": text}


@app.get("/api/v1/mongo/overview")
def mongo_overview():
    try:
        return json.loads(json_util.dumps(showcase.mongo_overview(), json_options=json_util.RELAXED_JSON_OPTIONS))
    except Exception as e:  # noqa: BLE001 - MongoDB unreachable is reported, not a 500 trace
        raise HTTPException(503, f"MongoDB unavailable: {e}") from e


class RoutedQuery(BaseModel):
    strategy: Literal["hash", "anatomical", "community"]
    kind: Literal["neighbours_out", "neighbours_in", "two_hop", "region", "summary"]
    release: int
    dataset: str | None = None
    source_id: str | None = None
    region: str | None = None


@app.post("/api/v1/distributed/query")
def distributed_query(body: RoutedQuery, conn=Depends(db)):
    neuron = None
    if body.kind in ("neighbours_out", "neighbours_in", "two_hop"):
        row = conn.execute("SELECT find_neuron(%s, %s) AS id", (body.dataset, body.source_id)).fetchone()
        if not row or row["id"] is None:
            raise HTTPException(404, "neuron not found")
        neuron = row["id"]
    if body.kind == "region" and not body.region:
        raise HTTPException(400, "region code required")
    try:
        return showcase.routed_query(body.strategy, body.kind, body.release, neuron, body.region)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e
