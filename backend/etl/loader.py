"""Load one release into the bitemporal store.

Transaction boundaries:
  T1  register dataset / release / schema mappings, open an ingestion_run  (commit)
  T2  stage, validate and merge the whole release                          (commit or roll back)
  T3  on failure only: mark the run and release failed                     (commit)
A failure in T2 leaves every previously loaded release untouched.

Merge rule per versioned table (neuron_version, annotation, connection), for
the new release with order N:
  - an open row (current belief, valid until further notice) whose fact is not
    restated identically in N is retired: its tx interval is closed at now()
    and a copy valid only up to N is recorded;
  - a fact of N without an identical open row is inserted valid from N.
Unchanged facts keep their single row, so storage grows with change, not with
the number of releases.
"""
from __future__ import annotations

import io
import json
import logging
import time
from dataclasses import dataclass, field

import pandas as pd
import psycopg
import yaml

from backend.config import PIPELINE_VERSION, SCHEMA_MAPPING_DIR, load_catalog
from backend.db import connect
from backend.etl.adapters import DatasetAdapter, ValidationError, get_adapter
from backend.etl.sources import SourceRef, file_sha256, manifest_url

log = logging.getLogger("cc.etl")


@dataclass
class LoadResult:
    dataset: str
    release: str
    status: str
    run_id: int | None = None
    stats: dict = field(default_factory=dict)


# ----------------------------------------------------------------- helpers
def _copy_frame(cur: psycopg.Cursor, table: str, cols: list[str], df: pd.DataFrame) -> int:
    buf = io.StringIO()
    df[cols].to_csv(buf, header=False, index=False)
    with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN (FORMAT csv)") as cp:
        cp.write(buf.getvalue())
    return len(df)


def _scalar(cur: psycopg.Cursor, sql: str, params: dict | tuple | None = None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _register(conn: psycopg.Connection, dataset: str, release: str) -> tuple[int, int, str]:
    catalog = load_catalog()
    ds = catalog[dataset]
    rel = ds["releases"][release]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO dataset (code, name, organism, source_url, license, organization, repository,
                                   data_formats, description)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, organization = EXCLUDED.organization,
                   repository = EXCLUDED.repository, data_formats = EXCLUDED.data_formats,
                   description = EXCLUDED.description
               RETURNING dataset_id""",
            (dataset, ds["name"], ds.get("organism"), ds.get("source_url"), ds.get("license"),
             ds.get("organization"), ds.get("repository"), ds.get("data_formats", []), ds.get("description")))
        ds_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO release (dataset_id, version_label, release_order, release_date)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (dataset_id, version_label) DO UPDATE SET release_order = release.release_order
               RETURNING release_id, ingestion_status""",
            (ds_id, release, rel["release_order"], rel.get("release_date")))
        rel_id, status = cur.fetchone()
        _register_schema_mapping(cur, ds_id, dataset)
    return ds_id, rel_id, status


def _register_schema_mapping(cur: psycopg.Cursor, ds_id: int, dataset: str) -> None:
    with open(SCHEMA_MAPPING_DIR / f"{dataset}.yaml", encoding="utf-8") as fh:
        m = yaml.safe_load(fh)
    rows = []
    for nf in m["neuron_files"]:
        rows.append((nf["file"], nf["id_column"], "dataset_neuron", "source_neuron_id", "identity"))
        for src, key in nf.get("annotations", {}).items():
            rows.append((nf["file"], src, "annotation", key, "strip; empty -> absent"))
    c = m["connections"]
    rows += [(c["file"], c["pre_column"], "connection", "pre_neuron_id", "source id -> dataset_neuron"),
             (c["file"], c["post_column"], "connection", "post_neuron_id", "source id -> dataset_neuron"),
             (c["file"], c["weight_column"], "connection", "synapse_count",
              "sum over rows of a pair" if c.get("duplicate_pairs") == "sum" else "integer")]
    cur.executemany(
        """INSERT INTO schema_mapping (dataset_id, source_file_pattern, source_field,
                                      canonical_entity, canonical_field, transform)
           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        [(ds_id, *r) for r in rows])


def _source_file(cur: psycopg.Cursor, rel_id: int, ref: SourceRef, rows: int) -> int:
    cur.execute(
        """INSERT INTO source_file (release_id, archive_path, member_path, sha256, size_bytes, source_url, row_count)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (release_id, archive_path, member_path)
           DO UPDATE SET row_count = EXCLUDED.row_count
           RETURNING source_file_id""",
        (rel_id, ref.archive_rel, ref.member, file_sha256(ref.archive), ref.archive.stat().st_size,
         manifest_url(ref.archive), rows))
    return cur.fetchone()[0]


def _provenance(cur, ds_id, rel_id, sf_id, run_id, name, params=None) -> int:
    cur.execute(
        """INSERT INTO provenance (dataset_id, release_id, source_file_id, ingestion_run_id,
                                   transformation_name, transformation_version, parameters)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING provenance_id""",
        (ds_id, rel_id, sf_id, run_id, name, PIPELINE_VERSION, json.dumps(params or {})))
    return cur.fetchone()[0]


def _fail_if(cur: psycopg.Cursor, sql: str, message: str, params=None) -> None:
    """Run a query returning (count, sample); raise ValidationError if count > 0."""
    cur.execute(sql, params)
    n, sample = cur.fetchone()
    if n:
        raise ValidationError(f"{message}: {n} rows, e.g. {sample}")


# ----------------------------------------------------------------- the load
def _check_order(cur: psycopg.Cursor, ds_id: int, rel_id: int) -> int:
    # serialize loads of the same dataset
    cur.execute("SELECT 1 FROM dataset WHERE dataset_id = %s FOR UPDATE", (ds_id,))
    order = _scalar(cur, "SELECT release_order FROM release WHERE release_id = %s", (rel_id,))
    later = _scalar(cur, """SELECT string_agg(version_label, ', ') FROM release
                            WHERE dataset_id = %s AND release_order > %s AND ingestion_status = 'loaded'""",
                    (ds_id, order))
    if later:
        raise ValidationError(f"releases must be loaded in order; already loaded later release(s): {later}")
    earlier = _scalar(cur, """SELECT string_agg(version_label, ', ') FROM release
                              WHERE dataset_id = %s AND release_order < %s AND ingestion_status <> 'loaded'""",
                      (ds_id, order))
    if earlier:
        raise ValidationError(f"load earlier release(s) first: {earlier}")
    return order


def _stage(cur: psycopg.Cursor, adapter: DatasetAdapter, ds_id: int, rel_id: int, run_id: int, stats: dict):
    cur.execute("""CREATE TEMP TABLE stg_neuron (source_id text NOT NULL, line integer NOT NULL)
                   ON COMMIT DROP""")
    cur.execute("""CREATE TEMP TABLE stg_annotation (source_id text NOT NULL, key text NOT NULL,
                   value text NOT NULL, line integer NOT NULL, provenance_id bigint NOT NULL) ON COMMIT DROP""")
    cur.execute("""CREATE TEMP TABLE stg_connection_raw (pre text NOT NULL, post text NOT NULL,
                   weight text NOT NULL, line integer NOT NULL) ON COMMIT DROP""")

    # neurons
    defining = next(f for f in adapter.neuron_files if f.defines_neurons)
    n = sum(_copy_frame(cur, "stg_neuron", ["source_id", "line"], df) for df in adapter.extract_neurons())
    sf = _source_file(cur, rel_id, defining.ref, n)
    neuron_prov = _provenance(cur, ds_id, rel_id, sf, run_id, "register_neuron",
                              {"id_column": defining.id_column})
    stats["neurons_in_source"] = n

    # annotations, one provenance record per source file
    stats["missing_mapped_columns"] = {}
    for nf in adapter.neuron_files:
        if nf.missing_columns:
            stats["missing_mapped_columns"][nf.ref.display] = nf.missing_columns
        if not nf.annotations:
            continue
        sf_id = sf if nf is defining else _source_file(cur, rel_id, nf.ref, adapter.count_rows(nf.ref))
        prov = _provenance(cur, ds_id, rel_id, sf_id, run_id, "extract_annotation",
                           {"columns": nf.annotations, "unknown_neuron_policy": nf.unknown_neuron_policy})
        if nf.unknown_neuron_policy == "skip":
            stats.setdefault("_skip_unknown_provenance", []).append(prov)
        rows = 0
        for df in adapter.extract_annotations(nf):
            df["provenance_id"] = prov
            rows += _copy_frame(cur, "stg_annotation", ["source_id", "key", "value", "line", "provenance_id"], df)
        stats.setdefault("annotations_in_source", {})[nf.ref.display] = rows

    # connections
    cf = adapter.connection_file
    rows = sum(_copy_frame(cur, "stg_connection_raw", ["pre", "post", "weight", "line"], df)
               for df in adapter.extract_connections())
    csf = _source_file(cur, rel_id, cf.ref, rows)
    conn_prov = _provenance(cur, ds_id, rel_id, csf, run_id,
                            "aggregate_pair_synapses" if cf.duplicate_pairs == "sum" else "load_pair_synapses",
                            {"pre": cf.pre_column, "post": cf.post_column, "weight": cf.weight_column,
                             "duplicate_pairs": cf.duplicate_pairs})
    stats["connection_rows_in_source"] = rows
    cur.execute("ANALYZE stg_neuron; ANALYZE stg_annotation; ANALYZE stg_connection_raw")
    return neuron_prov, conn_prov


def _validate_staged(cur: psycopg.Cursor, adapter: DatasetAdapter, stats: dict) -> None:
    pat = adapter.id_re.pattern
    # files whose mapping says unknown_neuron_policy: skip - drop those rows,
    # but record exactly which ids were dropped
    skip = stats.pop("_skip_unknown_provenance", [])
    if skip:
        cur.execute("""DELETE FROM stg_annotation a
                       WHERE a.provenance_id = ANY(%s)
                         AND NOT EXISTS (SELECT 1 FROM stg_neuron n WHERE n.source_id = a.source_id)
                       RETURNING a.source_id, a.line""", (skip,))
        dropped = sorted({(sid, line) for sid, line in cur.fetchall()}, key=lambda r: r[1])
        if dropped:
            stats["skipped_rows_unknown_neuron"] = {"count": len(dropped),
                                                    "rows": [{"source_id": s, "line": ln} for s, ln in dropped[:50]]}
    _fail_if(cur, "SELECT count(*), min(source_id) FROM stg_neuron WHERE source_id !~ %s",
             "neuron ids not matching id_pattern", (pat,))
    _fail_if(cur, """SELECT count(*), min(source_id) FROM
                     (SELECT source_id FROM stg_neuron GROUP BY 1 HAVING count(*) > 1) d""",
             "duplicate neuron ids")
    _fail_if(cur, """SELECT count(*), min(a.source_id) FROM stg_annotation a
                     WHERE NOT EXISTS (SELECT 1 FROM stg_neuron n WHERE n.source_id = a.source_id)""",
             "annotations for neurons not in the neuron file")
    _fail_if(cur, """SELECT count(*), min(source_id || ':' || key) FROM
                     (SELECT source_id, key FROM stg_annotation GROUP BY 1, 2 HAVING count(*) > 1) d""",
             "conflicting values for the same (neuron, annotation key)")
    _fail_if(cur, "SELECT count(*), min(weight) FROM stg_connection_raw WHERE weight !~ '^[0-9]+$'",
             "synapse counts that are not non-negative integers")
    _fail_if(cur, """SELECT count(*), min(c.pre || '->' || c.post) FROM stg_connection_raw c
                     WHERE NOT EXISTS (SELECT 1 FROM stg_neuron n WHERE n.source_id = c.pre)
                        OR NOT EXISTS (SELECT 1 FROM stg_neuron n WHERE n.source_id = c.post)""",
             "connections whose endpoints are not in the neuron file")
    if adapter.connection_file.duplicate_pairs == "error":
        _fail_if(cur, """SELECT count(*), min(pre || '->' || post) FROM
                         (SELECT pre, post FROM stg_connection_raw GROUP BY 1, 2 HAVING count(*) > 1) d""",
                 "duplicate (pre, post) pairs")


# Release fingerprint: SHA-256 over the sorted (sha256, path) list of every
# source file the release was loaded from.
MANIFEST_HASH_SQL = """
    SELECT encode(sha256(convert_to(string_agg(sha256 || ' ' || archive_path || '!' || member_path, E'\\n'
                                               ORDER BY archive_path, member_path), 'UTF8')), 'hex')
    FROM source_file WHERE release_id = %(rel)s"""

MERGE_SQL = {
    # ---- neurons ---------------------------------------------------------
    "neuron_ids": """
        INSERT INTO dataset_neuron (dataset_id, source_neuron_id, first_release_id, provenance_id,
                                    source_line, metadata_doc_id)
        SELECT %(ds)s, s.source_id, %(rel)s, %(nprov)s, s.line, %(code)s || ':' || s.source_id
        FROM stg_neuron s
        ON CONFLICT (dataset_id, source_neuron_id) DO NOTHING""",
    "neuron_resolve": """
        CREATE TEMP TABLE stg_neuron_res ON COMMIT DROP AS
        SELECT dn.dataset_neuron_id, s.source_id, s.line
        FROM stg_neuron s JOIN dataset_neuron dn
          ON dn.dataset_id = %(ds)s AND dn.source_neuron_id = s.source_id;
        CREATE UNIQUE INDEX ON stg_neuron_res (source_id);
        CREATE UNIQUE INDEX ON stg_neuron_res (dataset_neuron_id);
        ANALYZE stg_neuron_res""",
    "neuron_close": """
        CREATE TEMP TABLE close_nv ON COMMIT DROP AS
        SELECT nv.* FROM neuron_version nv JOIN dataset_neuron dn USING (dataset_neuron_id)
        WHERE dn.dataset_id = %(ds)s AND upper_inf(nv.tx) AND upper_inf(nv.valid_rel)
          AND NOT EXISTS (SELECT 1 FROM stg_neuron_res s WHERE s.dataset_neuron_id = nv.dataset_neuron_id);
        UPDATE neuron_version nv SET tx = tstzrange(lower(nv.tx), now())
        FROM close_nv c WHERE nv.neuron_version_id = c.neuron_version_id;
        INSERT INTO neuron_version (dataset_neuron_id, valid_rel, provenance_id, source_line)
        SELECT dataset_neuron_id, int4range(lower(valid_rel), %(ord)s), provenance_id, source_line
        FROM close_nv""",
    "neuron_open": """
        INSERT INTO neuron_version (dataset_neuron_id, valid_rel, provenance_id, source_line)
        SELECT s.dataset_neuron_id, int4range(%(ord)s, NULL), %(nprov)s, s.line
        FROM stg_neuron_res s
        WHERE NOT EXISTS (SELECT 1 FROM neuron_version nv
                          WHERE nv.dataset_neuron_id = s.dataset_neuron_id
                            AND upper_inf(nv.tx) AND upper_inf(nv.valid_rel))""",
    # ---- annotations -----------------------------------------------------
    "annotation_resolve": """
        CREATE TEMP TABLE stg_annotation_res ON COMMIT DROP AS
        SELECT r.dataset_neuron_id, a.key, a.value, a.line, a.provenance_id
        FROM stg_annotation a JOIN stg_neuron_res r USING (source_id);
        CREATE INDEX ON stg_annotation_res (dataset_neuron_id, key);
        ANALYZE stg_annotation_res""",
    "annotation_close": """
        CREATE TEMP TABLE close_an ON COMMIT DROP AS
        SELECT a.* FROM annotation a JOIN dataset_neuron dn USING (dataset_neuron_id)
        WHERE dn.dataset_id = %(ds)s AND upper_inf(a.tx) AND upper_inf(a.valid_rel)
          AND NOT EXISTS (SELECT 1 FROM stg_annotation_res s
                          WHERE s.dataset_neuron_id = a.dataset_neuron_id
                            AND s.key = a.annotation_key AND s.value = a.annotation_value);
        UPDATE annotation a SET tx = tstzrange(lower(a.tx), now())
        FROM close_an c WHERE a.annotation_id = c.annotation_id;
        INSERT INTO annotation (dataset_neuron_id, annotation_key, annotation_value, valid_rel,
                                provenance_id, source_line)
        SELECT dataset_neuron_id, annotation_key, annotation_value, int4range(lower(valid_rel), %(ord)s),
               provenance_id, source_line
        FROM close_an""",
    "annotation_open": """
        INSERT INTO annotation (dataset_neuron_id, annotation_key, annotation_value, valid_rel,
                                provenance_id, source_line)
        SELECT s.dataset_neuron_id, s.key, s.value, int4range(%(ord)s, NULL), s.provenance_id, s.line
        FROM stg_annotation_res s
        WHERE NOT EXISTS (SELECT 1 FROM annotation a
                          WHERE a.dataset_neuron_id = s.dataset_neuron_id AND a.annotation_key = s.key
                            AND upper_inf(a.tx) AND upper_inf(a.valid_rel))""",
    # ---- connections -----------------------------------------------------
    "connection_resolve": """
        CREATE TEMP TABLE stg_connection ON COMMIT DROP AS
        SELECT pre.dataset_neuron_id AS pre_id, post.dataset_neuron_id AS post_id,
               sum(c.weight::integer)::integer AS synapse_count,
               array_agg(c.line ORDER BY c.line) AS source_lines
        FROM stg_connection_raw c
        JOIN stg_neuron_res pre  ON pre.source_id  = c.pre
        JOIN stg_neuron_res post ON post.source_id = c.post
        GROUP BY 1, 2;
        CREATE UNIQUE INDEX ON stg_connection (pre_id, post_id);
        ANALYZE stg_connection""",
    "connection_close": """
        CREATE TEMP TABLE close_cn ON COMMIT DROP AS
        SELECT c.* FROM connection c
        WHERE c.dataset_id = %(ds)s AND upper_inf(c.tx) AND upper_inf(c.valid_rel)
          AND NOT EXISTS (SELECT 1 FROM stg_connection s
                          WHERE s.pre_id = c.pre_neuron_id AND s.post_id = c.post_neuron_id
                            AND s.synapse_count = c.synapse_count);
        UPDATE connection c SET tx = tstzrange(lower(c.tx), now())
        FROM close_cn x WHERE c.connection_id = x.connection_id;
        INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count, valid_rel,
                                provenance_id, source_lines)
        SELECT dataset_id, pre_neuron_id, post_neuron_id, synapse_count, int4range(lower(valid_rel), %(ord)s),
               provenance_id, source_lines
        FROM close_cn ORDER BY pre_neuron_id, post_neuron_id""",
    "connection_open": """
        INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count, valid_rel,
                                provenance_id, source_lines)
        SELECT %(ds)s, s.pre_id, s.post_id, s.synapse_count, int4range(%(ord)s, NULL), %(cprov)s, s.source_lines
        FROM stg_connection s
        WHERE NOT EXISTS (SELECT 1 FROM connection c
                          WHERE c.pre_neuron_id = s.pre_id AND c.post_neuron_id = s.post_id
                            AND upper_inf(c.tx) AND upper_inf(c.valid_rel))
        ORDER BY s.pre_id, s.post_id  -- sorted input keeps GiST/btree inserts local""",
}


def ingest_release(dataset: str, release: str, release_dir=None) -> LoadResult:
    # Hold a session-level dataset lease across registration, atomic load and failure logging.
    # A concurrent retry cannot mistake a live run for an interrupted one.
    with connect(autocommit=True) as lease:
        key = 'ingest:' + dataset
        lease.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))', (key,))
        try:
            return _ingest_release_locked(dataset, release, release_dir)
        finally:
            lease.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))


def _ingest_release_locked(dataset: str, release: str, release_dir=None) -> LoadResult:
    adapter = get_adapter(dataset, release, release_dir)
    adapter.validate()

    # T1: registration
    with connect() as conn:
        ds_id, rel_id, status = _register(conn, dataset, release)
        if status == "loaded":
            conn.commit()
            return LoadResult(dataset, release, "already_loaded")
        # crash recovery: a run still 'running' here lost its server or client
        # before it could record the failure; its T2 was rolled back by PostgreSQL
        conn.execute("""UPDATE ingestion_run SET status = 'failed', finished_at = clock_timestamp(),
                        error_message = 'interrupted: connection or server lost before completion'
                        WHERE release_id = %s AND status = 'running'""", (rel_id,))
        run_id = _scalar(conn.cursor(), """INSERT INTO ingestion_run (release_id, pipeline_version)
                                           VALUES (%s, %s) RETURNING run_id""", (rel_id, PIPELINE_VERSION))
        conn.commit()

    stats: dict = {}
    t0 = time.perf_counter()
    try:
        # T2: the load, all-or-nothing
        with connect() as conn, conn.cursor() as cur:
            order = _check_order(cur, ds_id, rel_id)
            cur.execute("UPDATE release SET ingestion_status = 'loading' WHERE release_id = %s", (rel_id,))
            nprov, cprov = _stage(cur, adapter, ds_id, rel_id, run_id, stats)
            stats["stage_seconds"] = round(time.perf_counter() - t0, 1)
            _validate_staged(cur, adapter, stats)
            params = {"ds": ds_id, "rel": rel_id, "ord": order, "nprov": nprov, "cprov": cprov, "code": dataset}
            for step, sql in MERGE_SQL.items():
                t = time.perf_counter()
                # parameterised statements must be sent one at a time; the
                # step's row count is that of its last (data-changing) statement
                for stmt in filter(str.strip, sql.split(";")):
                    cur.execute(stmt, params)
                stats[f"{step}_rows"] = cur.rowcount
                stats[f"{step}_seconds"] = round(time.perf_counter() - t, 1)
                log.info("%s %s: %s rows in %.1fs", dataset, step, cur.rowcount, time.perf_counter() - t)
            stats["total_seconds"] = round(time.perf_counter() - t0, 1)
            cur.execute(f"""UPDATE release SET ingestion_status = 'loaded', loaded_at = now(),
                                manifest_hash = ({MANIFEST_HASH_SQL})
                            WHERE release_id = %(rel)s""", {"rel": rel_id})
            cur.execute("""UPDATE ingestion_run SET status = 'succeeded', finished_at = clock_timestamp(),
                           stats = %s WHERE run_id = %s""", (json.dumps(stats), run_id))
            # counts commit together with the data they describe
            cur.execute("REFRESH MATERIALIZED VIEW release_stats")
            conn.commit()
        return LoadResult(dataset, release, "loaded", run_id, stats)
    except Exception as exc:
        # T3: record the failure; T2 has been rolled back
        with connect() as conn:
            conn.execute("""UPDATE ingestion_run SET status = 'failed', finished_at = clock_timestamp(),
                            error_message = %s, stats = %s WHERE run_id = %s""",
                         (f"{type(exc).__name__}: {exc}"[:4000], json.dumps(stats), run_id))
            conn.execute("UPDATE release SET ingestion_status = 'failed' WHERE release_id = %s", (rel_id,))
            conn.commit()
        raise
