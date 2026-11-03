"""Regression and workflow checks for the interactive workspace on synthetic data."""

import time
from datetime import datetime, timedelta, timezone
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(toyfly_loaded, toyskip_loaded):
    from backend.metadata import sync_release
    from backend.etl.regions import load_regions

    for release in ("r1", "r2", "r3"):
        sync_release("toyfly", release)
        if (
            release != "r3"
        ):  # r3 intentionally contains an invalid region for rollback testing
            load_regions("toyfly", release)
    from backend.main import app

    with TestClient(app) as client:
        yield client


def compare(client):
    response = client.post(
        "/api/v1/diffs",
        json={"dataset": "toyfly", "release_a": "r1", "release_b": "r2"},
    )
    assert response.status_code == 200, response.text
    return response.json()["diff_run_id"]


def wait_job(client, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/workspace/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    pytest.fail("Workspace job did not finish in 30 seconds")


def test_current_type_filter_does_not_match_old_intervals(client):
    assert (
        client.get(
            "/api/v1/neurons", params={"dataset": "toyfly", "cell_type": "B"}
        ).json()
        == []
    )
    old = client.get(
        "/api/v1/neurons",
        params={"dataset": "toyfly", "cell_type": "B", "release": "r1"},
    ).json()
    assert [(r["source_neuron_id"], r["cell_type"]) for r in old] == [("2", "B")]
    absent = client.get(
        "/api/v1/neurons",
        params={"dataset": "toyfly", "source_id": "4", "release": "r2"},
    ).json()
    assert absent == []


@pytest.mark.parametrize(
    "timestamp",
    [
        (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "2020-01-01T00:00:00",
    ],
)
def test_invalid_knowledge_times_are_client_errors(client, timestamp):
    response = client.post(
        "/api/v1/diffs",
        json={
            "dataset": "toyfly",
            "release_a": "r1",
            "release_b": "r2",
            "as_of": timestamp,
        },
    )
    assert response.status_code == 422


def test_equal_release_comparison_is_rejected(client):
    assert (
        client.post(
            "/api/v1/diffs",
            json={"dataset": "toyfly", "release_a": "r1", "release_b": "r1"},
        ).status_code
        == 422
    )


def test_default_comparison_reuses_same_data_revision(client):
    assert compare(client) == compare(client)


def test_graph_is_bounded_and_keeps_exact_source_ids(client):
    root = client.get(
        "/api/v1/neurons", params={"dataset": "toyfly", "source_id": "1"}
    ).json()[0]["dataset_neuron_id"]
    graph = client.get(
        f"/api/v1/workspace/neurons/{root}/graph",
        params={"release": "r2", "hops": 2, "max_nodes": 2, "max_edges": 1},
    ).json()
    assert len(graph["nodes"]) <= 2 and len(graph["edges"]) <= 1 and graph["truncated"]
    larger = client.get(
        f"/api/v1/workspace/neurons/{root}/graph", params={"release": "r2"}
    ).json()
    assert any(n["label"] == "720575940596125868" for n in larger["nodes"])


def test_diff_event_has_explanation_and_both_sources(client):
    diff = compare(client)
    events = client.get(
        f"/api/v1/workspace/diffs/{diff}/events",
        params={"entity_type": "connection", "change_type": "modified"},
    ).json()
    assert events["total"] >= 1
    detail = client.get(
        f"/api/v1/workspace/events/{events['rows'][0]['diff_event_id']}"
    ).json()
    assert detail["explanation"] and set(detail["provenance"]) == {"before", "after"}
    assert detail["dataset_neuron_id"] > 0
    csv = client.get(
        f"/api/v1/workspace/diffs/{diff}/export",
        params={"entity_type": "connection", "format": "csv"},
    )
    assert (
        csv.status_code == 200
        and "diff_event_id" in csv.text
        and csv.headers["x-export-truncated"] == "false"
    )
    exported = client.get(
        f"/api/v1/workspace/diffs/{diff}/export",
        params={"format": "json", "entity_type": "annotation"},
    ).json()
    assert all(row["entity_type"] == "annotation" for row in exported["rows"])


def test_document_content_tampering_is_detected(client):
    from backend.metadata import mongo_db, check_consistency

    coll = mongo_db()["neuron_source_record"]
    saved = coll.find_one({"_id": "toyfly:1"})
    release = next(iter(saved["releases"]))
    try:
        coll.update_one(
            {"_id": saved["_id"]}, {"$set": {f"releases.{release}.records": []}}
        )
        report = check_consistency("toyfly")
        assert (
            report["references_consistent"]
            and not report["content_verified"]
            and not report["consistent"]
        )
        assert report["content_mismatch_count"] == 1
    finally:
        coll.replace_one({"_id": saved["_id"]}, saved)


def test_dynamic_mongo_sample_and_empty_collection(client):
    from backend.metadata import mongo_db

    coll = mongo_db()["neuron_source_record"]
    saved = list(coll.find())
    live = client.get("/api/v1/mongo/overview").json()
    assert live["sample"]["_id"] == live["sample_postgres_row"]["metadata_doc_id"]
    examples = client.get("/api/v1/workspace/examples").json()
    one = next(e for e in examples["mongo"] if e["id"] == "m-one")
    projection = next(e for e in examples["mongo"] if e["id"] == "m-project")
    assert one["filter"]["_id"] == live["sample"]["_id"]
    assert all(
        f"releases.{rid}.release" in projection["projection"]
        for rid in live["sample"]["releases"]
    )
    try:
        coll.delete_many({})
        empty = client.get("/api/v1/mongo/overview")
        assert empty.status_code == 200 and empty.json()["sample"] is None
    finally:
        if saved:
            coll.insert_many(saved)


def test_job_roundtrip_and_small_sample_model(client):
    response = client.post(
        "/api/v1/workspace/jobs",
        json={
            "kind": "diff",
            "request": {"dataset": "toyfly", "release_a": "r1", "release_b": "r2"},
        },
    )
    assert response.status_code == 202
    done = wait_job(client, response.json()["job_id"])
    assert done["status"] == "succeeded", done
    response = client.post(
        "/api/v1/workspace/jobs",
        json={
            "kind": "anomalies",
            "request": {"diff_id": done["result"]["diff_run_id"]},
        },
    )
    model = wait_job(client, response.json()["job_id"])
    assert model["status"] == "succeeded", model
    assert model["result"]["model_kind"] == "threshold"
    results = client.get(
        f"/api/v1/workspace/models/{model['result']['model_run_id']}/results"
    ).json()
    assert results["total"] > 0 and all(
        r["explanation"] and r["features"] for r in results["rows"]
    )


def test_reviewer_token_is_enforced(client, monkeypatch):
    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "cc_review_token", "test-review-token")
    response = client.post(
        "/api/v1/mappings/999999/review",
        json={"decision": "approved", "reviewer": "fixture"},
    )
    assert response.status_code == 401
    response = client.post(
        "/api/v1/workspace/jobs",
        json={"kind": "matching", "request": {}},
        headers={"X-Review-Token": "wrong"},
    )
    assert response.status_code == 401


def test_full_shard_verification_catches_corrupt_mirrors_and_recovers(
    client, monkeypatch
):
    import backend.distributed.deploy as deployment
    from backend.distributed.partition import compute
    from backend.distributed.router import Router

    compute("hash")
    first = deployment.deploy("hash")
    original = deployment._stream

    def corrupt(src, dst, query, table):
        result = original(src, dst, query, table)
        if table.endswith(".connection"):
            dst.execute(
                f"UPDATE {table} SET synapse_count=synapse_count+1 WHERE is_mirror"
            )
        return result

    with monkeypatch.context() as patch:
        patch.setattr(deployment, "_stream", corrupt)
        with pytest.raises(RuntimeError, match="Full content verification failed"):
            deployment.deploy("hash")
    # Failed staging did not change active generation; an existing successful copy still serves.
    router = Router("hash")
    assert router.batch_id == first["batch_id"]
    router.close()
    recovered = deployment.deploy("hash")
    router = Router("hash")
    assert router.batch_id == recovered["batch_id"]
    router.close()
    compute("hash")
    with pytest.raises(RuntimeError, match="stale"):
        Router("hash")


def test_isolation_forest_streams_and_records_reproducibility(client):
    from backend.workspace import db_connection
    from backend.analysis import score_diff

    # Deliberately constructed ML fixture, independent of biological data or matcher labels.
    with db_connection() as conn:
        releases = conn.execute(
            "SELECT release_id FROM release JOIN dataset USING(dataset_id) WHERE code='toyfly' ORDER BY release_order"
        ).fetchall()
        ds = conn.execute(
            "SELECT dataset_id FROM dataset WHERE code='toyfly'"
        ).fetchone()["dataset_id"]
        n = conn.execute(
            "SELECT dataset_neuron_id FROM dataset_neuron WHERE dataset_id=%s ORDER BY dataset_neuron_id",
            (ds,),
        ).fetchall()
        run = conn.execute(
            "INSERT INTO diff_run(mode,dataset_a,dataset_b,release_a,release_b,as_of,status,parameters) VALUES ('temporal',%s,%s,%s,%s,now(),'succeeded',%s) RETURNING diff_run_id",
            (
                ds,
                ds,
                releases[0]["release_id"],
                releases[1]["release_id"],
                json.dumps({"synthetic_ml_fixture": True}),
            ),
        ).fetchone()["diff_run_id"]
        for i in range(20):
            conn.execute(
                "INSERT INTO diff_event(diff_run_id,entity_type,change_type,dataset_neuron_id,post_neuron_id,old_weight,new_weight,record_a_id,record_b_id) VALUES (%s,'connection','modified',%s,%s,%s,%s,1,2)",
                (
                    run,
                    n[i % len(n)]["dataset_neuron_id"],
                    n[(i + 1) % len(n)]["dataset_neuron_id"],
                    10 + i % 3,
                    12 + i % 3 if i < 19 else 10000,
                ),
            )
    result = score_diff(run)
    assert (
        result["model_kind"] == "isolation_forest" and result["metrics"]["rows"] == 20
    )
    with db_connection() as conn:
        model = conn.execute(
            "SELECT * FROM ml_model_run WHERE model_run_id=%s",
            (result["model_run_id"],),
        ).fetchone()
        top = conn.execute(
            "SELECT features FROM anomaly_result WHERE model_run_id=%s ORDER BY score_rank LIMIT 1",
            (result["model_run_id"],),
        ).fetchone()
    assert model["random_seed"] == 42 and len(model["artifact_sha256"].strip()) == 64
    assert top["features"]["relative_delta"] > 0.9
