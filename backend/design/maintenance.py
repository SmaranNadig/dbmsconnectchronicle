"""File maintenance and updation (docs/NORMALISATION.md §Maintenance).

  anomalies()   reproduces the insertion, update and deletion anomalies on an
                unnormalised copy of the lab data (one wide row per connection,
                neuron facts repeated on every row) and runs the same change on
                the normalised lab tables, all inside one transaction that is
                rolled back: nothing is kept.
  file_stats()  how each table is stored: its file on disk, heap / index /
                TOAST sizes, live and dead tuple versions, last vacuum / analyze.
  maintain()    VACUUM (ANALYZE), ANALYZE or REINDEX on the lab tables (as role
                cc_lab, which owns them), with the statistics before and after.
"""
from __future__ import annotations

import time

from psycopg import ClientCursor, sql
from psycopg.rows import dict_row

from backend import lab
from backend.db import connect

LAB_TABLES = ["dataset", "release", "neuron", "connection", "change_log"]

FLAT_SQL = """
CREATE TEMP TABLE connection_flat ON COMMIT DROP AS
SELECT r.release_id, r.version_label, d.code AS dataset_code,
       c.pre_neuron_id,  a.source_neuron_id AS pre_source_id,  a.cell_type AS pre_cell_type,
       c.post_neuron_id, b.source_neuron_id AS post_source_id, b.cell_type AS post_cell_type,
       c.synapse_count
FROM lab.connection c
JOIN lab.release r USING (release_id) JOIN lab.dataset d USING (dataset_id)
JOIN lab.neuron a ON a.neuron_id = c.pre_neuron_id
JOIN lab.neuron b ON b.neuron_id = c.post_neuron_id;
ALTER TABLE connection_flat ADD PRIMARY KEY (release_id, pre_neuron_id, post_neuron_id)"""


def _one(cur, query, params=None):
    cur.execute(query, params)
    row = cur.fetchone()
    return None if row is None else (list(row.values())[0] if len(row) == 1 else dict(row))


def _try(cur, statement, params=None) -> dict:
    """Run one statement inside a savepoint; report rows or the error."""
    text = " ".join((cur.mogrify(statement, params) if params else statement).split())
    cur.execute("SAVEPOINT step")
    try:
        cur.execute(statement, params)
        out = {"sql": text, "ok": True, "rows": cur.rowcount, "status": cur.statusmessage}
        cur.execute("RELEASE SAVEPOINT step")
    except Exception as e:      # noqa: BLE001 - the error is the result being shown
        cur.execute("ROLLBACK TO SAVEPOINT step")
        out = {"sql": text, "ok": False, "error": str(e).splitlines()[0],
               "sqlstate": getattr(e, "sqlstate", None)}
    return out


def anomalies() -> dict:
    lab.ensure()
    t0 = time.perf_counter()
    with lab.lab_connect() as conn:
        conn.autocommit = False
        cur = ClientCursor(conn)      # mogrify(): show each statement with its values
        try:
            cur.execute(FLAT_SQL)
            flat_rows = _one(cur, "SELECT count(*) FROM connection_flat")
            # the neuron whose facts are repeated most often in the wide table
            hub = _one(cur, """SELECT n.neuron_id, n.source_neuron_id, n.cell_type,
                                      (SELECT count(*) FROM connection_flat f WHERE f.pre_neuron_id = n.neuron_id) AS copies
                               FROM lab.neuron n WHERE n.cell_type IS NOT NULL ORDER BY copies DESC, n.neuron_id LIMIT 1""")
            redundancy = {
                "unnormalised_rows": flat_rows,
                "neuron": hub,
                "copies_of_its_cell_type": hub["copies"],
                "normalised_copies": 1,
                # rows that repeat facts of a pre-synaptic neuron already stated on another row
                "repeated_neuron_facts": _one(cur, "SELECT count(*) - count(DISTINCT pre_neuron_id) FROM connection_flat"),
            }

            # ---- update anomaly: a change must reach every copy
            one_release = _one(cur, "SELECT min(release_id) FROM connection_flat WHERE pre_neuron_id = %s",
                               (hub["neuron_id"],))
            partial = _try(cur, """UPDATE connection_flat SET pre_cell_type = 'renamed_type'
                                   WHERE pre_neuron_id = %s AND release_id = %s""", (hub["neuron_id"], one_release))
            types_after = _one(cur, """SELECT array_agg(DISTINCT pre_cell_type) FROM connection_flat
                                       WHERE pre_neuron_id = %s""", (hub["neuron_id"],))
            normal = _try(cur, "UPDATE lab.neuron SET cell_type = 'renamed_type' WHERE neuron_id = %s",
                          (hub["neuron_id"],))
            update = {
                "unnormalised": {**partial, "result": f"the neuron now has {len(types_after)} different cell types: "
                                                      f"{', '.join(map(str, types_after))}",
                                 "distinct_values": len(types_after)},
                "normalised": {**normal, "result": "one row changed; every connection sees the new type through the join",
                               "distinct_values": _one(cur, """SELECT count(DISTINCT n.cell_type) FROM lab.connection c
                                   JOIN lab.neuron n ON n.neuron_id = c.pre_neuron_id WHERE c.pre_neuron_id = %s""",
                                                       (hub["neuron_id"],))},
            }

            # ---- insertion anomaly: a neuron without connections has no row to live in
            ds = _one(cur, "SELECT dataset_id, code FROM lab.dataset ORDER BY dataset_id LIMIT 1")
            rel = _one(cur, "SELECT release_id, version_label FROM lab.release WHERE dataset_id = %s ORDER BY release_order DESC LIMIT 1",
                       (ds["dataset_id"],))
            insert = {
                "unnormalised": _try(cur, """INSERT INTO connection_flat (release_id, version_label, dataset_code,
                                                 pre_neuron_id, pre_source_id, pre_cell_type)
                                             VALUES (%s, %s, %s, 900000000001, 'new-neuron', 'new_type')""",
                                     (rel["release_id"], rel["version_label"], ds["code"])),
                "normalised": _try(cur, """INSERT INTO lab.neuron (dataset_id, source_neuron_id, cell_type)
                                           VALUES (%s, 'new-neuron', 'new_type')""", (ds["dataset_id"],)),
            }
            insert["unnormalised"]["result"] = ("rejected: post_neuron_id is part of the key, so a neuron that has "
                                                "no connection yet cannot be recorded")
            insert["normalised"]["result"] = "recorded in lab.neuron; connections can be added later"

            # ---- deletion anomaly: deleting the last connection deletes the neuron's facts
            leaf = _one(cur, """SELECT n.neuron_id, n.source_neuron_id, n.cell_type, count(*) AS connections
                                FROM lab.neuron n JOIN lab.connection c ON n.neuron_id IN (c.pre_neuron_id, c.post_neuron_id)
                                WHERE n.cell_type IS NOT NULL
                                GROUP BY n.neuron_id ORDER BY count(*), n.neuron_id LIMIT 1""")
            flat_del = _try(cur, "DELETE FROM connection_flat WHERE %(n)s IN (pre_neuron_id, post_neuron_id)",
                            {"n": leaf["neuron_id"]})
            left_flat = _one(cur, """SELECT count(*) FROM connection_flat
                                     WHERE %(n)s IN (pre_neuron_id, post_neuron_id)""", {"n": leaf["neuron_id"]})
            norm_del = _try(cur, "DELETE FROM lab.connection WHERE %(n)s IN (pre_neuron_id, post_neuron_id)",
                            {"n": leaf["neuron_id"]})
            left_norm = _one(cur, "SELECT cell_type FROM lab.neuron WHERE neuron_id = %s", (leaf["neuron_id"],))
            delete = {
                "neuron": leaf,
                "unnormalised": {**flat_del, "result": f"no row mentions neuron {leaf['source_neuron_id']} any more "
                                                       f"({left_flat} rows): its cell type '{leaf['cell_type']}' is lost"},
                "normalised": {**norm_del, "result": f"the neuron stays in lab.neuron with cell type '{left_norm}'"},
            }
        finally:
            conn.rollback()       # nothing of the demonstration is kept
    return {"redundancy": redundancy, "update": update, "insert": insert, "delete": delete,
            "unnormalised_definition": FLAT_SQL.split(";")[0].strip(), "rolled_back": True,
            "seconds": round(time.perf_counter() - t0, 2)}


STATS_SQL = """
SELECT n.nspname AS schema, c.relname AS table, pg_relation_filepath(c.oid) AS file,
       pg_relation_size(c.oid) AS heap_bytes, pg_indexes_size(c.oid) AS index_bytes,
       pg_total_relation_size(c.oid) - pg_relation_size(c.oid) - pg_indexes_size(c.oid) AS toast_bytes,
       pg_total_relation_size(c.oid) AS total_bytes,
       (pg_relation_size(c.oid) / current_setting('block_size')::int) AS pages,
       s.n_live_tup AS live_tuples, s.n_dead_tup AS dead_tuples,
       s.n_tup_ins AS inserted, s.n_tup_upd AS updated, s.n_tup_del AS deleted, s.n_tup_hot_upd AS hot_updated,
       s.seq_scan, s.idx_scan, s.last_vacuum, s.last_autovacuum, s.last_analyze, s.last_autoanalyze,
       s.vacuum_count + s.autovacuum_count AS vacuums
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE c.relkind = 'r' AND n.nspname = ANY (%s)
ORDER BY n.nspname, pg_total_relation_size(c.oid) DESC"""


def file_stats(schemas=("cc", "lab"), conn=None) -> list[dict]:
    if conn is not None:
        return conn.cursor(row_factory=dict_row).execute(STATS_SQL, (list(schemas),)).fetchall()
    with connect() as c:
        return c.cursor(row_factory=dict_row).execute(STATS_SQL, (list(schemas),)).fetchall()


OPERATIONS = {
    "vacuum": "VACUUM (ANALYZE) lab.{t}",
    "analyze": "ANALYZE lab.{t}",
    "reindex": "REINDEX TABLE lab.{t}",
}


def maintain(operation: str) -> dict:
    if operation not in OPERATIONS:
        raise ValueError(f"operation must be one of {sorted(OPERATIONS)}")
    lab.ensure()
    before = {r["table"]: r for r in file_stats(["lab"])}
    statements = []
    with lab.lab_connect() as conn:        # autocommit: VACUUM cannot run in a transaction
        for t in LAB_TABLES:
            q = sql.SQL(OPERATIONS[operation].replace("{t}", "{}")).format(sql.Identifier(t))
            t0 = time.perf_counter()
            conn.execute(q)
            statements.append({"sql": q.as_string(conn), "ms": round((time.perf_counter() - t0) * 1000, 1)})
    # the statistics collector reports asynchronously; give it a moment
    time.sleep(0.6)
    after = {r["table"]: r for r in file_stats(["lab"])}
    keys = ["file", "heap_bytes", "index_bytes", "pages", "live_tuples", "dead_tuples", "last_vacuum", "last_analyze"]
    return {"operation": operation, "statements": statements,
            "tables": [{"table": t, "before": {k: before.get(t, {}).get(k) for k in keys},
                        "after": {k: after.get(t, {}).get(k) for k in keys}} for t in LAB_TABLES]}
