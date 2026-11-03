"""Compute which shard owns each source neuron, for one partitioning strategy.

    hash        owner = hashint8(dataset_neuron_id) mod 3 (computed in SQL)
    anatomical  group = the neuron's primary brain region (most synapses, latest
                release with region data); groups packed onto shards
    community   group = Louvain community of the dataset's latest-release graph
                (undirected, weighted by synapse count); groups packed onto shards

Packing is longest-processing-time first: groups in decreasing order of edge
load go to the currently least-loaded shard. A group's load is the number of
current connection rows whose pre-synaptic neuron is in it (plus one per
neuron). Neurons without a group (no region data / not in the latest graph)
fall back to the hash owner; their count is reported. The result replaces the
strategy's rows in cc.partition_assignment in one transaction.
"""
from __future__ import annotations

import logging
import random
import time

import numpy as np
import pandas as pd

from backend.db import connect
from backend.etl.loader import _copy_frame

log = logging.getLogger("cc.partition")

SHARD_COUNT = 3
STRATEGIES = ("hash", "anatomical", "community")
HASH_SQL = f"((hashint8(dataset_neuron_id) % {SHARD_COUNT} + {SHARD_COUNT}) % {SHARD_COUNT} + 1)"


def _loads(cur) -> pd.DataFrame:
    cur.execute("""SELECT dn.dataset_neuron_id, dn.dataset_id, 1 + count(c.connection_id) AS load,
                          """ + HASH_SQL.replace("dataset_neuron_id", "dn.dataset_neuron_id") + """ AS hash_shard
                   FROM dataset_neuron dn
                   LEFT JOIN connection c ON c.pre_neuron_id = dn.dataset_neuron_id AND upper_inf(c.tx)
                   GROUP BY dn.dataset_neuron_id, dn.dataset_id""")
    return pd.DataFrame(cur.fetchall(), columns=["neuron", "dataset_id", "load", "hash_shard"])


def pack(group_load: dict[str, int], shards: int = SHARD_COUNT) -> dict[str, int]:
    """Longest-processing-time-first bin packing of groups onto shards 1..n."""
    totals = [0] * shards
    out = {}
    for key, load in sorted(group_load.items(), key=lambda kv: (-kv[1], kv[0])):
        i = min(range(shards), key=lambda s: (totals[s], s))
        out[key] = i + 1
        totals[i] += load
    return out


def _region_groups(cur) -> pd.DataFrame:
    cur.execute("SELECT dataset_neuron_id, code FROM neuron_latest_region")
    return pd.DataFrame(cur.fetchall(), columns=["neuron", "group_key"])


def _community_groups(cur, seed: int) -> tuple[pd.DataFrame, dict]:
    import igraph as ig

    random.seed(seed)
    ig.set_random_number_generator(random)
    frames, info = [], {}
    cur.execute("""SELECT d.dataset_id, d.code, max(r.release_order) FROM dataset d JOIN release r USING (dataset_id)
                   WHERE r.ingestion_status = 'loaded' GROUP BY d.dataset_id, d.code ORDER BY d.dataset_id""")
    for ds_id, code, order in cur.fetchall():
        cur.execute("""SELECT pre_neuron_id, post_neuron_id, synapse_count FROM connection
                       WHERE dataset_id = %s AND valid_rel @> %s::int AND upper_inf(tx) AND synapse_count > 0""",
                    (ds_id, order))
        edges = np.array(cur.fetchall(), dtype=np.int64)
        if len(edges) == 0:
            continue
        ids, inv = np.unique(edges[:, :2], return_inverse=True)
        pairs = inv.reshape(-1, 2)
        g = ig.Graph(n=len(ids), edges=pairs.tolist(), directed=False)
        g.es["weight"] = edges[:, 2].tolist()
        g.simplify(combine_edges="sum")             # a->b and b->a become one weighted edge
        parts = g.community_multilevel(weights="weight")
        info[code] = {"release_order": int(order), "neurons": len(ids), "edges": g.ecount(),
                      "communities": len(parts), "modularity": round(parts.modularity, 4),
                      "largest_community": max(parts.sizes())}
        frames.append(pd.DataFrame({"neuron": ids, "group_key": [f"{code}:{m}" for m in parts.membership]}))
        log.info("community %s: %s", code, info[code])
    return pd.concat(frames, ignore_index=True), info


def compute(strategy: str, seed: int = 42) -> dict:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    t0 = time.perf_counter()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT strategy_id FROM partition_strategy WHERE code = %s", (strategy,))
        sid = cur.fetchone()[0]
        loads = _loads(cur)
        info: dict = {}
        if strategy == "hash":
            loads["shard"] = loads["hash_shard"]
            loads["group_key"] = None
        else:
            groups, info = (_region_groups(cur), {}) if strategy == "anatomical" else _community_groups(cur, seed)
            df = loads.merge(groups, on="neuron", how="left")
            grouped = df.dropna(subset=["group_key"])
            placement = pack(grouped.groupby("group_key")["load"].sum().to_dict())
            df["shard"] = df["group_key"].map(placement)
            info |= {"groups": len(placement), "fallback_to_hash": int(df["shard"].isna().sum())}
            df["shard"] = df["shard"].fillna(df["hash_shard"]).astype(int)
            loads = df
        cur.execute("DELETE FROM partition_assignment WHERE strategy_id = %s", (sid,))
        loads["strategy_id"] = sid
        _copy_frame(cur, "partition_assignment", ["strategy_id", "dataset_neuron_id", "shard_id", "group_key"],
                    loads.rename(columns={"neuron": "dataset_neuron_id", "shard": "shard_id"})[
                        ["strategy_id", "dataset_neuron_id", "shard_id", "group_key"]])
        params = {"seed": seed, **info} if strategy == "community" else info
        cur.execute("""UPDATE partition_strategy SET parameters = parameters || %s::jsonb
                       WHERE strategy_id = %s""", (pd.Series(params).to_json(), sid))
        conn.commit()
    shard_load = loads.groupby("shard")["load"].sum()
    result = {"strategy": strategy, "neurons": len(loads), **info,
              "load_per_shard": {int(k): int(v) for k, v in shard_load.items()},
              "load_imbalance_max_over_mean": round(float(shard_load.max() / shard_load.mean()), 3),
              "seconds": round(time.perf_counter() - t0, 1)}
    log.info("%s", result)
    return result
