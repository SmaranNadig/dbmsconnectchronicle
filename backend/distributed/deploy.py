"""Copy the coordinator's current state onto the three shard instances
according to one strategy's partition_assignment.

Each strategy gets its own schema on every shard (p_hash, p_anatomical,
p_community), so all three placements can be queried side by side:

    release     replicated reference table (small)
    neuron      replicated dimension table, with the owning shard of each neuron
    connection  rows owned by this shard (is_mirror = false) plus mirrors of
                cut rows whose post-synaptic neuron this shard owns

Rows are streamed coordinator -> shard with binary COPY, indexes are built
after the load, and the deployment is verified against the coordinator
(row counts and sums of ids and synapse counts per shard) before it is
recorded in cc.shard_deployment.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg

from backend.config import get_settings
from backend.db import connect

log = logging.getLogger("cc.deploy")

SHARD_DDL = """
DROP SCHEMA IF EXISTS {s} CASCADE;
CREATE SCHEMA {s};
CREATE TABLE {s}.release (
    release_id     integer PRIMARY KEY,
    dataset_id     smallint NOT NULL,
    version_label  text NOT NULL,
    release_order  integer NOT NULL,
    UNIQUE (dataset_id, release_order)
);
CREATE TABLE {s}.neuron (
    dataset_neuron_id  bigint PRIMARY KEY,
    dataset_id         smallint NOT NULL,
    source_neuron_id   text NOT NULL,
    owner_shard        smallint NOT NULL CHECK (owner_shard BETWEEN 1 AND 64),
    group_key          text,               -- region or community the strategy grouped it by
    primary_region     text                -- most synapses, latest release with region data
);
CREATE TABLE {s}.connection (
    connection_id   bigint PRIMARY KEY,
    dataset_id      smallint NOT NULL,
    pre_neuron_id   bigint NOT NULL,
    post_neuron_id  bigint NOT NULL,
    synapse_count   integer NOT NULL CHECK (synapse_count >= 0),
    valid_rel       int4range NOT NULL CHECK (NOT isempty(valid_rel)),
    is_mirror       boolean NOT NULL
);
"""

SHARD_INDEXES = """
ALTER TABLE {s}.connection ADD FOREIGN KEY (pre_neuron_id) REFERENCES {s}.neuron,
                           ADD FOREIGN KEY (post_neuron_id) REFERENCES {s}.neuron;
CREATE INDEX ON {s}.connection (pre_neuron_id);
CREATE INDEX ON {s}.connection (post_neuron_id);
CREATE INDEX ON {s}.neuron (group_key);
CREATE INDEX ON {s}.neuron (owner_shard);
CREATE INDEX ON {s}.neuron (primary_region);
ANALYZE {s}.release; ANALYZE {s}.neuron; ANALYZE {s}.connection;
"""

RELEASE_SQL = """COPY (SELECT release_id, dataset_id, version_label, release_order FROM cc.release
                      WHERE ingestion_status = 'loaded') TO STDOUT (FORMAT binary)"""
NEURON_SQL = """COPY (SELECT dn.dataset_neuron_id, dn.dataset_id, dn.source_neuron_id, pa.shard_id, pa.group_key,
                            pr.code
                     FROM cc.dataset_neuron dn JOIN cc.partition_assignment pa USING (dataset_neuron_id)
                     LEFT JOIN cc.neuron_latest_region pr USING (dataset_neuron_id)
                     WHERE pa.strategy_id = {sid}) TO STDOUT (FORMAT binary)"""
CONNECTION_SQL = """COPY (SELECT c.connection_id, c.dataset_id, c.pre_neuron_id, c.post_neuron_id, c.synapse_count,
                                 c.valid_rel, ap.shard_id <> {k} AS is_mirror
                         FROM cc.connection c
                         JOIN cc.partition_assignment ap ON ap.strategy_id = {sid} AND ap.dataset_neuron_id = c.pre_neuron_id
                         JOIN cc.partition_assignment aq ON aq.strategy_id = {sid} AND aq.dataset_neuron_id = c.post_neuron_id
                         WHERE upper_inf(c.tx) AND (ap.shard_id = {k} OR aq.shard_id = {k}))
                  TO STDOUT (FORMAT binary)"""
EXPECTED_SQL = """SELECT ap.shard_id, count(*), coalesce(sum(c.connection_id), 0), coalesce(sum(c.synapse_count), 0)
                  FROM cc.connection c
                  JOIN cc.partition_assignment ap ON ap.strategy_id = %s AND ap.dataset_neuron_id = c.pre_neuron_id
                  WHERE upper_inf(c.tx) GROUP BY ap.shard_id"""


def shard_conn(shard_id: int, autocommit: bool = False) -> psycopg.Connection:
    s = get_settings()
    return psycopg.connect(s.pg_dsn(port=s.shard_ports[shard_id - 1]), autocommit=autocommit)


def _stream(src: psycopg.Connection, dst: psycopg.Connection, select_copy: str, table: str) -> None:
    with src.cursor().copy(select_copy) as out, dst.cursor().copy(f"COPY {table} FROM STDIN (FORMAT binary)") as inp:
        for block in out:
            inp.write(block)


def _deploy_shard(strategy: str, sid: int, k: int) -> dict:
    schema = f"p_{strategy}"
    t0 = time.perf_counter()
    with connect() as src, shard_conn(k) as dst:
        dst.execute(SHARD_DDL.format(s=schema))
        _stream(src, dst, RELEASE_SQL, f"{schema}.release")
        _stream(src, dst, NEURON_SQL.format(sid=sid), f"{schema}.neuron")
        _stream(src, dst, CONNECTION_SQL.format(sid=sid, k=k), f"{schema}.connection")
        dst.execute(SHARD_INDEXES.format(s=schema))
        dst.commit()
        row = dst.execute(f"""SELECT (SELECT count(*) FROM {schema}.neuron WHERE owner_shard = %s),
                                     count(*) FILTER (WHERE NOT is_mirror),
                                     coalesce(sum(connection_id) FILTER (WHERE NOT is_mirror), 0),
                                     coalesce(sum(synapse_count) FILTER (WHERE NOT is_mirror), 0),
                                     count(*) FILTER (WHERE is_mirror),
                                     (SELECT sum(pg_total_relation_size(c.oid)) FROM pg_class c
                                      JOIN pg_namespace n ON n.oid = c.relnamespace
                                      WHERE n.nspname = %s AND c.relkind = 'r')
                              FROM {schema}.connection""", (k, schema)).fetchone()
    res = dict(zip(["neurons", "connections", "id_sum", "synapse_sum", "mirrors", "bytes"], row))
    res["seconds"] = round(time.perf_counter() - t0, 1)
    log.info("%s shard %d: %s", strategy, k, res)
    return res


def deploy(strategy: str) -> dict:
    with connect() as conn:
        sid = conn.execute("SELECT strategy_id FROM partition_strategy WHERE code = %s", (strategy,)).fetchone()[0]
        if not conn.execute("SELECT 1 FROM partition_assignment WHERE strategy_id = %s LIMIT 1", (sid,)).fetchone():
            raise RuntimeError(f"no assignment for {strategy}; compute the partition first")
        shards = [r[0] for r in conn.execute("SELECT shard_id FROM shard ORDER BY shard_id").fetchall()]
    with ThreadPoolExecutor(len(shards)) as pool:
        results = dict(zip(shards, pool.map(lambda k: _deploy_shard(strategy, sid, k), shards)))
    with connect() as conn:
        expected = {k: (n, ids, syn) for k, n, ids, syn in conn.execute(EXPECTED_SQL, (sid,)).fetchall()}
        verified = all(expected.get(k, (0, 0, 0)) == (r["connections"], r["id_sum"], r["synapse_sum"])
                       for k, r in results.items())
        for k, r in results.items():
            conn.execute("""INSERT INTO shard_deployment (strategy_id, shard_id, schema_name, neurons, connections,
                                                          mirrors, bytes_on_shard, seconds, verified)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                         (sid, k, f"p_{strategy}", r["neurons"], r["connections"], r["mirrors"], r["bytes"],
                          r["seconds"], verified))
        conn.commit()
    total = sum(r["connections"] for r in results.values())
    mirrors = sum(r["mirrors"] for r in results.values())
    summary = {"strategy": strategy, "verified": verified, "owned_rows": total, "mirror_rows": mirrors,
               "cut_fraction": round(mirrors / total, 4) if total else None,
               "storage_overhead": round((total + mirrors) / total, 3) if total else None,
               "shards": {k: {x: r[x] for x in ("neurons", "connections", "mirrors", "bytes", "seconds")}
                          for k, r in results.items()}}
    if not verified:
        raise RuntimeError(f"deployment of {strategy} does not match the coordinator: {summary} vs {expected}")
    return summary
