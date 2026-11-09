"""Read-only query consoles, integrity demo, catalog and source endpoints
(test database with the toyfly + toyskip fixtures)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.config import PROJECT_ROOT


@pytest.fixture(scope="module")
def client(toyfly_loaded, toyskip_loaded):
    from backend.main import app
    with TestClient(app) as c:
        yield c


def sql(client, text, explain=None):
    return client.post("/api/v1/query/sql", json={"sql": text, "explain": explain})


def test_sql_console_reads_as_read_only_role(client):
    r = sql(client, "SELECT code FROM dataset ORDER BY code;")
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["code"] and ["toyfly"] in body["rows"]
    who = sql(client, "SELECT current_user, current_setting('transaction_read_only')").json()
    assert who["rows"] == [["cc_reader", "on"]]


@pytest.mark.parametrize("text, error", [
    ("DELETE FROM dataset", "read-only"),
    ("SELECT 1; COMMIT; DELETE FROM dataset", "multiple commands"),
    ("WITH d AS (DELETE FROM cc.dataset RETURNING 1) SELECT * FROM d", "read-only transaction"),
    ("SELECT set_config('role', current_setting('role'), false)", "permission denied"),
    ("SELECT pg_read_file('postgresql.conf')", "permission denied"),
])
def test_sql_console_refuses_writes_and_escapes(client, db, text, error):
    before = db.execute("SELECT count(*) FROM dataset").fetchone()[0]
    r = sql(client, text)
    assert r.status_code == 400 and error in r.json()["detail"]
    assert db.execute("SELECT count(*) FROM dataset").fetchone()[0] == before


def test_sql_console_timeout(client, monkeypatch):
    from backend import console
    monkeypatch.setattr(console, "TIMEOUT_MS", 300)
    r = sql(client, "SELECT pg_sleep(3)")
    assert r.status_code == 400 and "statement timeout" in r.json()["detail"]


def test_sql_console_explain(client):
    r = sql(client, "SELECT * FROM connection WHERE pre_neuron_id = 1", explain="plan").json()
    assert r["explain"] == "plan" and any("Scan" in row[0] for row in r["rows"])


def test_every_published_example_passes_its_targets_gate():
    # the real-data examples cannot run on the fixtures; check they pass the console's own gate.
    # Lab examples are scripts (several statements allowed) and are run in test_lab.py.
    from backend.console import ALLOWED_START, TRIAL_START, _strip
    ex = json.loads((PROJECT_ROOT / "frontend/public/examples.json").read_text(encoding="utf-8"))
    assert ex["sql"] and ex["mongo"]
    for e in ex["sql"]:
        target = e.get("target", "read")
        assert target in ("read", "trial", "lab"), e["id"]
        if target == "lab":
            continue
        assert (ALLOWED_START if target == "read" else TRIAL_START).match(_strip(e["sql"])), e["id"]
        assert ";" not in _strip(e["sql"]), e["id"]
    assert all(m.get("target", "main") == "lab" or m["op"] in ("find", "count", "aggregate") for m in ex["mongo"])


def test_mongo_console(client):
    from backend.metadata import sync_release
    sync_release("toyfly", "r1")
    r = client.post("/api/v1/query/mongo", json={"op": "count", "filter": {"dataset": "toyfly"}})
    assert r.status_code == 200 and r.json()["documents"][0]["count"] > 0
    agg = client.post("/api/v1/query/mongo", json={
        "op": "aggregate", "pipeline": [{"$group": {"_id": "$dataset", "n": {"$sum": 1}}}]}).json()
    assert any(d["_id"] == "toyfly" for d in agg["documents"])


@pytest.mark.parametrize("body", [
    {"op": "find", "filter": {"$where": "true"}},
    {"op": "aggregate", "pipeline": [{"$out": "stolen"}]},
    {"op": "aggregate", "pipeline": [{"$match": {}}, {"$merge": {"into": "x"}}]},
])
def test_mongo_console_refuses_code_and_writes(client, body):
    r = client.post("/api/v1/query/mongo", json=body)
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]


def test_integrity_demo_every_statement_rejected_and_nothing_written(client, db):
    before = db.execute("SELECT (SELECT count(*) FROM dataset), (SELECT count(*) FROM connection), "
                        "(SELECT count(*) FROM annotation), (SELECT count(*) FROM diff_run)").fetchone()
    results = client.post("/api/v1/demo/integrity").json()
    assert len(results) == 9
    assert [r["rule"] for r in results if not r["rejected"]] == []
    assert {r["sqlstate"] for r in results} <= {"23514", "23505", "23502", "23503", "23P01", "23000"}
    after = db.execute("SELECT (SELECT count(*) FROM dataset), (SELECT count(*) FROM connection), "
                       "(SELECT count(*) FROM annotation), (SELECT count(*) FROM diff_run)").fetchone()
    assert before == after


def test_catalog_and_sources(client):
    cat = client.get("/api/v1/catalog").json()
    assert {c["type"] for c in cat["constraints"]} == {"PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "EXCLUDE"}
    assert {t["function"] for t in cat["triggers"]} == {"guard_history"}
    assert any(r["schema"] == "er" for r in cat["relations"])
    files = {f["path"] for f in client.get("/api/v1/source").json()}
    assert "backend/db/sql/0010_query_console_role.sql" in files and "backend/metadata.py" in files
    assert "CREATE TABLE dataset" in client.get("/api/v1/source/file",
                                                params={"path": "backend/db/sql/0001_core_schema.sql"}).json()["text"]
    assert client.get("/api/v1/source/file", params={"path": ".env"}).status_code == 404
    assert client.get("/api/v1/source/file", params={"path": "../.env"}).status_code == 404
