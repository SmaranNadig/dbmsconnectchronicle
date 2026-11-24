import numpy as np
from fastapi.testclient import TestClient

from backend.circuit_model import experiment
from backend.circuit_snapshot import INPUT, MID, OUTPUT
from backend.main import app


def test_circuit_controls_and_reproducibility():
    nodes = [(i, typ, 0, col) for i, (typ, col) in enumerate(
        (typ, col) for typ in INPUT + MID + OUTPUT for col in range(3))]
    edges = [(a, b, 1 + abs(p-q)) for a, ta, _, p in nodes for b, tb, _, q in nodes
             if (ta in INPUT and tb in MID) or (ta in MID and tb in OUTPUT)]
    rows = experiment(nodes, edges, 7)
    assert rows == experiment(nodes, edges, 7)
    assert len(rows) == 8 * 24
    for condition, _, _, score, heading, baseline, frames in rows:
        assert len(frames) == 32 and len(frames[0]["activity"]) == 8
        assert np.isfinite([score, heading, baseline]).all()
        if condition == "disconnected":
            assert score == 0 and heading == baseline
            assert all(f["action"] == 0 for f in frames)


def test_circuit_requires_a_real_snapshot(test_database):
    with TestClient(app) as client:
        assert client.get("/api/v1/circuit/snapshots").json() == []
        assert client.post("/api/v1/circuit/runs", json={"snapshot_id":1,"seed":42}).status_code == 404
        assert client.post("/api/v1/circuit/runs", json={"snapshot_id":1,"seed":-1}).status_code == 422

