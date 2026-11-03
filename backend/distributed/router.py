"""Query router over the shard instances for one deployed strategy.

The router keeps the placement map (neuron -> owning shard, and primary
region -> shards) from the coordinator, sends each query only to the shards
that can hold an answer, runs multi-shard steps in parallel, merges the
results, and logs every execution in cc.query_execution (strategy, kind,
shards touched, rows, bytes received, latency).

Queries (all at one release, valid time):
    neighbours(n, release, direction)  1-hop partners; always one shard (mirrors)
    two_hop(n, release)                out-partners of out-partners; hop 2 goes to
                                       the owners of the hop-1 partners
    region(code, release)              connections whose pre-synaptic neuron has its
                                       primary region in `code`; owners of those neurons
    summary(release)                   connections and synapses per dataset; every
                                       shard, owned rows only (mirrors would double count)

`Coordinator` answers the same queries from the single coordinator instance,
as the non-distributed baseline.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import psycopg

from backend.db import connect
from backend.distributed.deploy import shard_conn


def _size(rows) -> int:
    # bytes received, approximated by the text size of the result rows
    return sum(len(str(r)) for r in rows)


class _Logged:
    strategy_id: int | None = None
    target = ""

    def _log(self, kind: str, params: dict, shard_ids, rows: list, t0: float) -> None:
        shard_ids = sorted(shard_ids)
        ms = (time.perf_counter() - t0) * 1000
        self.log_conn.execute(
            """INSERT INTO query_execution (strategy_id, query_kind, parameters, shards_touched, rows_returned,
                                            bytes_transferred, latency_ms) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (self.strategy_id, kind, json.dumps({**params, "target": self.target}), len(shard_ids), len(rows),
             _size(rows), ms))
        self.last = {"kind": kind, "shards": len(shard_ids), "shard_ids": shard_ids, "rows": len(rows),
                     "latency_ms": ms}


class Router(_Logged):
    def __init__(self, strategy: str):
        self.schema = f"p_{strategy}"
        self.target = f"shards:{strategy}"
        self.log_conn = connect(autocommit=True)
        self.strategy_id = self.log_conn.execute(
            "SELECT strategy_id FROM partition_strategy WHERE code = %s", (strategy,)).fetchone()[0]
        if not self.log_conn.execute("""SELECT bool_and(verified) FROM shard_deployment
                                        WHERE strategy_id = %s""", (self.strategy_id,)).fetchone()[0]:
            raise RuntimeError(f"strategy {strategy} has no verified deployment")
        self.owner = dict(self.log_conn.execute(
            "SELECT dataset_neuron_id, shard_id FROM partition_assignment WHERE strategy_id = %s",
            (self.strategy_id,)).fetchall())
        self.region_shards: dict[str, set[int]] = defaultdict(set)
        for code, shard in self.log_conn.execute(
                """SELECT DISTINCT r.code, pa.shard_id FROM neuron_latest_region r
                   JOIN partition_assignment pa USING (dataset_neuron_id) WHERE pa.strategy_id = %s""",
                (self.strategy_id,)).fetchall():
            self.region_shards[code].add(shard)
        shard_ids = [r[0] for r in self.log_conn.execute("SELECT shard_id FROM shard ORDER BY shard_id").fetchall()]
        self.shards = {k: shard_conn(k, autocommit=True) for k in shard_ids}
        self.pool = ThreadPoolExecutor(len(self.shards))

    def close(self) -> None:
        self.pool.shutdown()
        for c in self.shards.values():
            c.close()
        self.log_conn.close()

    def _run(self, shard: int, sql: str, params) -> list:
        return self.shards[shard].execute(sql.format(s=self.schema), params).fetchall()

    def _scatter(self, work: dict[int, tuple[str, tuple]]) -> list:
        futures = [self.pool.submit(self._run, k, sql, p) for k, (sql, p) in work.items()]
        return [row for f in futures for row in f.result()]

    # ------------------------------------------------------------------ queries
    NEIGHBOURS = """
        SELECT n.source_neuron_id, c.{other}, c.synapse_count
        FROM {{s}}.connection c JOIN {{s}}.release r ON r.release_id = %s AND r.dataset_id = c.dataset_id
        JOIN {{s}}.neuron n ON n.dataset_neuron_id = c.{other}
        WHERE c.{me} = %s AND c.valid_rel @> r.release_order"""

    def neighbours(self, neuron: int, release: int, direction: str = "out") -> list:
        t0 = time.perf_counter()
        me, other = ("pre_neuron_id", "post_neuron_id") if direction == "out" else ("post_neuron_id", "pre_neuron_id")
        rows = self._run(self.owner[neuron], self.NEIGHBOURS.format(me=me, other=other), (release, neuron))
        self._log(f"neighbours_{direction}", {"neuron": neuron, "release": release}, [self.owner[neuron]], rows, t0)
        return rows

    TWO_HOP = """
        SELECT c.pre_neuron_id, c.post_neuron_id, c.synapse_count
        FROM {s}.connection c JOIN {s}.release r ON r.release_id = %s AND r.dataset_id = c.dataset_id
        WHERE c.pre_neuron_id = ANY(%s) AND NOT c.is_mirror AND c.valid_rel @> r.release_order"""

    def two_hop(self, neuron: int, release: int) -> list:
        t0 = time.perf_counter()
        first = self._run(self.owner[neuron], self.TWO_HOP, (release, [neuron]))
        by_shard: dict[int, list[int]] = defaultdict(list)
        for _, partner, _ in first:
            by_shard[self.owner[partner]].append(partner)
        rows = self._scatter({k: (self.TWO_HOP, (release, ids)) for k, ids in by_shard.items()})
        self._log("two_hop", {"neuron": neuron, "release": release}, {self.owner[neuron], *by_shard}, rows, t0)
        return rows

    REGION = """
        SELECT c.pre_neuron_id, c.post_neuron_id, c.synapse_count
        FROM {s}.neuron n JOIN {s}.connection c ON c.pre_neuron_id = n.dataset_neuron_id AND NOT c.is_mirror
        JOIN {s}.release r ON r.release_id = %s AND r.dataset_id = c.dataset_id
        WHERE n.primary_region = %s AND c.valid_rel @> r.release_order"""

    def region(self, code: str, release: int) -> list:
        t0 = time.perf_counter()
        targets = self.region_shards.get(code, set())
        rows = self._scatter({k: (self.REGION, (release, code)) for k in targets})
        self._log("region", {"region": code, "release": release}, targets, rows, t0)
        return rows

    SUMMARY = """
        SELECT c.dataset_id, count(*), sum(c.synapse_count)
        FROM {s}.connection c JOIN {s}.release r ON r.release_id = %s AND r.dataset_id = c.dataset_id
        WHERE NOT c.is_mirror AND c.valid_rel @> r.release_order GROUP BY c.dataset_id"""

    def summary(self, release: int) -> list:
        t0 = time.perf_counter()
        parts = self._scatter({k: (self.SUMMARY, (release,)) for k in self.shards})
        merged: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for ds, n, syn in parts:
            merged[ds][0] += n
            merged[ds][1] += syn
        rows = [(ds, n, syn) for ds, (n, syn) in sorted(merged.items())]
        self._log("summary", {"release": release}, self.shards, rows, t0)
        return rows


class Coordinator(_Logged):
    """The same queries on the single coordinator instance (baseline)."""

    target = "coordinator"

    def __init__(self):
        self.log_conn = connect(autocommit=True)
        self.conn: psycopg.Connection = connect(autocommit=True)
        # the shards store each neuron's primary region as a column; give the
        # baseline the same precomputed, indexed lookup so the comparison is fair
        self.conn.execute("CREATE TEMP TABLE primary_region AS SELECT dataset_neuron_id, code FROM neuron_latest_region")
        self.conn.execute("CREATE INDEX ON primary_region (code); ANALYZE primary_region")

    def close(self) -> None:
        self.conn.close()
        self.log_conn.close()

    def _q(self, kind, params, sql, args) -> list:
        t0 = time.perf_counter()
        rows = self.conn.execute(sql, args).fetchall()
        self._log(kind, params, [0], rows, t0)    # 0 = the coordinator
        return rows

    def neighbours(self, neuron: int, release: int, direction: str = "out") -> list:
        me, other = ("pre_neuron_id", "post_neuron_id") if direction == "out" else ("post_neuron_id", "pre_neuron_id")
        return self._q(f"neighbours_{direction}", {"neuron": neuron, "release": release}, f"""
            SELECT n.source_neuron_id, c.{other}, c.synapse_count
            FROM connection c JOIN release r ON r.release_id = %s AND r.dataset_id = c.dataset_id
            JOIN dataset_neuron n ON n.dataset_neuron_id = c.{other}
            WHERE c.{me} = %s AND c.valid_rel @> r.release_order AND upper_inf(c.tx)""", (release, neuron))

    def two_hop(self, neuron: int, release: int) -> list:
        return self._q("two_hop", {"neuron": neuron, "release": release}, """
            SELECT c2.pre_neuron_id, c2.post_neuron_id, c2.synapse_count
            FROM release r
            JOIN connection c1 ON c1.dataset_id = r.dataset_id AND c1.pre_neuron_id = %s
                              AND c1.valid_rel @> r.release_order AND upper_inf(c1.tx)
            JOIN connection c2 ON c2.pre_neuron_id = c1.post_neuron_id
                              AND c2.valid_rel @> r.release_order AND upper_inf(c2.tx)
            WHERE r.release_id = %s""", (neuron, release))

    def region(self, code: str, release: int) -> list:
        return self._q("region", {"region": code, "release": release}, """
            SELECT c.pre_neuron_id, c.post_neuron_id, c.synapse_count
            FROM primary_region n
            JOIN connection c ON c.pre_neuron_id = n.dataset_neuron_id AND upper_inf(c.tx)
            JOIN release r ON r.release_id = %s AND r.dataset_id = c.dataset_id
            WHERE n.code = %s AND c.valid_rel @> r.release_order""", (release, code))

    def summary(self, release: int) -> list:
        return self._q("summary", {"release": release}, """
            SELECT c.dataset_id, count(*), sum(c.synapse_count)
            FROM connection c JOIN release r ON r.release_id = %s AND r.dataset_id = c.dataset_id
            WHERE c.valid_rel @> r.release_order AND upper_inf(c.tx) GROUP BY c.dataset_id""", (release,))
