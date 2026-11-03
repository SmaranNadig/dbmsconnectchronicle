"""Partitioning, deployment to the shard instances, and routed queries, on
the fixtures in the test databases (connectome_test on coordinator and shards)."""
from __future__ import annotations

from collections import Counter

import pytest

from backend.distributed.deploy import deploy
from backend.distributed.partition import STRATEGIES, compute, pack
from backend.distributed.router import Coordinator, Router
from backend.etl.regions import load_regions


@pytest.fixture(scope="module")
def deployed(toyfly_loaded):
    for r in ("r1", "r2"):
        load_regions("toyfly", r)
    return {s: (compute(s), deploy(s)) for s in STRATEGIES}


def test_pack_is_longest_processing_time_first():
    assert pack({"a": 10, "b": 7, "c": 6, "d": 5, "e": 1}) == {"a": 1, "b": 2, "c": 3, "d": 3, "e": 2}


def test_every_neuron_has_exactly_one_owner(db, deployed):
    for s in STRATEGIES:
        n, owners, bad = db.execute("""
            SELECT (SELECT count(*) FROM dataset_neuron),
                   count(DISTINCT pa.dataset_neuron_id), count(*) FILTER (WHERE pa.shard_id NOT BETWEEN 1 AND 3)
            FROM partition_assignment pa JOIN partition_strategy ps USING (strategy_id)
            WHERE ps.code = %s""", (s,)).fetchone()
        assert n == owners and bad == 0, s


def test_anatomical_keeps_a_region_on_one_shard(db, deployed):
    rows = db.execute("""SELECT pa.group_key, count(DISTINCT pa.shard_id)
                         FROM partition_assignment pa JOIN partition_strategy ps USING (strategy_id)
                         WHERE ps.code = 'anatomical' AND pa.group_key IS NOT NULL
                         GROUP BY pa.group_key""").fetchall()
    assert rows and all(n == 1 for _, n in rows)


def test_deployment_is_verified_and_complete(db, deployed):
    open_rows = db.execute("SELECT count(*) FROM connection WHERE upper_inf(tx)").fetchone()[0]
    for s, (_, dep) in deployed.items():
        assert dep["verified"] and dep["owned_rows"] == open_rows, s
        cut_rows = db.execute("SELECT sum(mirrored_in) FROM partition_quality(%s)", (s,)).fetchone()[0]
        assert dep["mirror_rows"] == cut_rows, s


def _items(db):
    rels = [r[0] for r in db.execute("""SELECT release_id FROM release JOIN dataset USING (dataset_id)
                                         WHERE code = 'toyfly' ORDER BY release_order""").fetchall()]
    neurons = [r[0] for r in db.execute("""SELECT dataset_neuron_id FROM dataset_neuron JOIN dataset USING (dataset_id)
                                           WHERE code = 'toyfly'""").fetchall()]
    items = [("summary", (r,)) for r in rels] + [("region", (c, r)) for c in ("SLP_R", "EB", "MB_VL_R") for r in rels]
    for r in rels:
        for n in neurons:
            items += [("neighbours", (n, r, "out")), ("neighbours", (n, r, "in")), ("two_hop", (n, r))]
    return items


def test_routed_answers_equal_the_coordinator(db, deployed):
    coord = Coordinator()
    try:
        for s in STRATEGIES:
            router = Router(s)
            try:
                for kind, args in _items(db):
                    got = Counter(map(tuple, getattr(router, kind)(*args)))
                    want = Counter(map(tuple, getattr(coord, kind)(*args)))
                    assert got == want, (s, kind, args)
            finally:
                router.close()
    finally:
        coord.close()


def test_one_hop_touches_one_shard_and_executions_are_logged(db, deployed):
    one = db.execute("SELECT find_neuron('toyfly', '1')").fetchone()[0]
    r2 = db.execute("""SELECT release_id FROM release JOIN dataset USING (dataset_id)
                       WHERE code = 'toyfly' AND version_label = 'r2'""").fetchone()[0]
    router = Router("hash")
    try:
        # in r2: 720575940596125868 -> 1 (4 synapses) and the zero-weight 5 -> 1
        assert {(row[0], row[2]) for row in router.neighbours(one, r2, "in")} == {
            ("720575940596125868", 4), ("5", 0)}
        assert router.last["shards"] == 1
        router.summary(r2)
        assert router.last["shards"] == 3
    finally:
        router.close()
    n = db.execute("""SELECT count(*) FROM query_execution q JOIN partition_strategy s USING (strategy_id)
                      WHERE s.code = 'hash' AND q.query_kind = 'summary'""").fetchone()[0]
    assert n >= 1
