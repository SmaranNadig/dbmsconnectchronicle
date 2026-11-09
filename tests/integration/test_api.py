"""REST API against the test database (toyfly fixture)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(toyfly_loaded):
    from backend.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["schema_revision"] == "0012"


def test_releases(client):
    rows = {r["version_label"]: r["neurons"] for r in client.get("/api/v1/releases?dataset=toyfly").json()}
    assert rows == {"r1": 5, "r2": 5, "r3": 6}


def test_neuron_states_and_connections(client):
    n = client.get("/api/v1/neurons", params={"dataset": "toyfly", "source_id": "2"}).json()[0]
    detail = client.get(f"/api/v1/neurons/{n['dataset_neuron_id']}").json()
    assert [s["annotations"]["cell_type"] for s in detail["states"]] == ["B", "B2", "B2"]
    one = client.get("/api/v1/neurons", params={"dataset": "toyfly", "source_id": "1"}).json()[0]
    out = client.get(f"/api/v1/neurons/{one['dataset_neuron_id']}/connections", params={"release": "r2"}).json()
    assert {(c["direction"], c["partner_source_id"], c["synapse_count"]) for c in out} == {("out", "2", 8)} | {
        ("in", "720575940596125868", 4)}


def test_diff_roundtrip(client):
    r = client.post("/api/v1/diffs", json={"dataset": "toyfly", "release_a": "r1", "release_b": "r2"})
    assert r.status_code == 200
    run = r.json()["diff_run_id"]
    ev = client.get(f"/api/v1/diffs/{run}/events", params={"entity_type": "connection", "order": "magnitude"}).json()
    assert ev[0]["neuron_source_id"] == "3" and ev[0]["weight_delta"] == -7


def test_validation_and_not_found(client):
    assert client.get("/api/v1/neurons/999999999").status_code == 404
    assert client.get("/api/v1/neurons", params={"dataset": "toyfly", "limit": 100000}).status_code == 422
    assert client.post("/api/v1/diffs", json={"dataset": "toyfly", "release_a": "r1", "release_b": "nope"}).status_code == 404


def test_overview_counts_come_from_the_database(client):
    o = client.get("/api/v1/overview").json()
    toy = {(r["dataset"], r["version_label"]): (r["neurons"], r["connections"]) for r in o["releases"]}
    assert toy[("toyfly", "r1")] == (5, 4) and toy[("toyfly", "r3")] == (6, 5)
    assert o["storage"]["connection_rows_stored"] <= o["storage"]["rows_if_snapshots"]
    assert o["schema_revision"] == "0012" and any(d["code"] == "toyfly" for d in o["datasets"])


def test_dashboard_endpoints_respond(client):
    assert client.get("/api/v1/mappings/summary").status_code == 200
    assert set(client.get("/api/v1/distribution").json()) == {"strategies", "deployments", "shards"}
    assert client.get("/api/v1/benchmarks/latest").status_code in (200, 404)
    assert client.get("/api/v1/regions", params={"release": 1}).status_code == 200
