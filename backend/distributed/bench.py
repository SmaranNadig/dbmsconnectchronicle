"""Benchmark the three deployed strategies against the single coordinator.

Workload, at the latest loaded release of each dataset:
    neighbours_out / neighbours_in   for `neurons` sampled neurons per dataset
    two_hop                          for the same neurons
    region                           for REGIONS (present in both datasets)
    summary                          once per release

Every query runs once as warm-up, then `repeat` measured times; each
execution is also logged in cc.query_execution. Before timing, every routed
answer of the first pass is compared with the coordinator's answer (as a
multiset of rows); any mismatch fails the benchmark. Results go to
results/benchmarks/<timestamp>.json with the environment they were measured in.
"""
from __future__ import annotations

import json
import os
import platform
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean, median

import numpy as np

from backend.config import PROJECT_ROOT
from backend.db import connect
from backend.distributed.partition import STRATEGIES
from backend.distributed.router import Coordinator, Router

REGIONS = ["SLP_R", "ME_R", "AL_R", "LO_R", "EB", "FB"]
OUT_DIR = PROJECT_ROOT / "results" / "benchmarks"


def _workload(neurons: int, seed: int) -> list[tuple]:
    rng = random.Random(seed)
    items = []
    with connect() as conn:
        latest = conn.execute("""SELECT DISTINCT ON (dataset_id) release_id, dataset_id FROM release
                                 WHERE ingestion_status = 'loaded'
                                 ORDER BY dataset_id, release_order DESC""").fetchall()
        for rel, ds in latest:
            pool = [r[0] for r in conn.execute(
                """SELECT DISTINCT c.pre_neuron_id FROM connection c JOIN release r ON r.release_id = %s
                   WHERE c.dataset_id = %s AND c.valid_rel @> r.release_order AND upper_inf(c.tx)
                   ORDER BY 1""", (rel, ds)).fetchall()]
            for n in rng.sample(pool, neurons):
                items += [("neighbours", (n, rel, "out")), ("neighbours", (n, rel, "in")), ("two_hop", (n, rel))]
            items += [("region", (code, rel)) for code in REGIONS]
            items.append(("summary", (rel,)))
    return items


def _call(target, kind, args):
    return getattr(target, kind)(*args)


def _environment() -> dict:
    with connect() as conn:
        pg = conn.execute("SELECT version()").fetchone()[0]
        size = conn.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        deployments = conn.execute("""SELECT s.code, d.shard_id, d.connections, d.mirrors, d.bytes_on_shard
                                      FROM shard_deployment d JOIN partition_strategy s USING (strategy_id)
                                      WHERE d.deployment_id IN (SELECT max(deployment_id) FROM shard_deployment
                                                                GROUP BY strategy_id, shard_id)
                                      ORDER BY 1, 2""").fetchall()
    return {"host_os": platform.platform(), "python": platform.python_version(), "cpu_logical": os.cpu_count(),
            "postgres": pg, "coordinator_db_size": size,
            "topology": "1 coordinator + 3 shard PostgreSQL containers on one Docker Desktop VM (one machine)",
            "deployments": [dict(zip(["strategy", "shard", "owned_rows", "mirrors", "bytes"], d))
                            for d in deployments]}


def run(neurons: int = 25, repeat: int = 3, seed: int = 7) -> dict:
    items = _workload(neurons, seed)
    targets = {"coordinator": Coordinator(), **{s: Router(s) for s in STRATEGIES}}
    try:
        # correctness: every routed answer equals the coordinator's
        mismatches = []
        for kind, args in items:
            expected = Counter(map(tuple, _call(targets["coordinator"], kind, args)))
            for s in STRATEGIES:
                if Counter(map(tuple, _call(targets[s], kind, args))) != expected:
                    mismatches.append((s, kind, args))
        if mismatches:
            raise AssertionError(f"{len(mismatches)} routed answers differ from the coordinator: {mismatches[:5]}")
        # timing (the correctness pass above is the warm-up)
        samples: dict[tuple, list] = defaultdict(list)
        for _ in range(repeat):
            order = items[:]
            random.Random(seed).shuffle(order)
            for name, target in targets.items():
                for kind, args in order:
                    _call(target, kind, args)
                    label = f"{kind}_{args[2]}" if kind == "neighbours" else kind
                    samples[(name, label)].append(target.last)
    finally:
        for t in targets.values():
            t.close()
    summary = {}
    for (name, label), runs in sorted(samples.items()):
        lat = [r["latency_ms"] for r in runs]
        summary.setdefault(label, {})[name] = {
            "executions": len(runs),
            "p50_ms": round(median(lat), 3),
            "p95_ms": round(float(np.percentile(lat, 95)), 3),
            "mean_shards_touched": round(mean(r["shards"] for r in runs), 2),
            "mean_rows": round(mean(r["rows"] for r in runs), 1)}
    result = {"measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "parameters": {"neurons_per_dataset": neurons, "repeat": repeat, "seed": seed, "regions": REGIONS,
                             "warm_up": "one full pass (also the correctness check)"},
              "correctness": {"queries_compared": len(items) * len(STRATEGIES), "mismatches": 0},
              "environment": _environment(), "results": summary}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"bench_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    result["file"] = str(path.relative_to(PROJECT_ROOT))
    return result
