"""Data editor: the editable lab (schema lab + MongoDB lab), the trial target
on the real tables, and the access rules that keep the real data unchanged
(DATA_EDITOR_PLAN.md). Test database with the toyfly + toyskip fixtures."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.config import PROJECT_ROOT


@pytest.fixture(scope="module")
def client(toyfly_loaded, toyskip_loaded):
    from backend.main import app
    from backend.metadata import sync_release
    for r in ("r1", "r2", "r3"):
        sync_release("toyfly", r)
    with TestClient(app) as c:
        assert c.post("/api/v1/lab/reset").status_code == 200
        yield c


def lab_sql(client, text, rollback=False):
    return client.post("/api/v1/query/sql", json={"sql": text, "target": "lab", "rollback": rollback})


def real_counts(db):
    return db.execute("SELECT (SELECT count(*) FROM dataset), (SELECT count(*) FROM dataset_neuron), "
                      "(SELECT count(*) FROM connection), (SELECT count(*) FROM annotation)").fetchone()


def test_lab_is_a_copy_of_real_data(client, db):
    s = client.get("/api/v1/lab/status").json()
    assert s["built"] and s["role"] == "cc_lab"
    t = s["tables"]
    assert t["datasets"] == db.execute("SELECT count(*) FROM dataset").fetchone()[0]
    assert t["releases"] == db.execute("SELECT count(*) FROM release WHERE ingestion_status = 'loaded'").fetchone()[0]
    assert t["neurons"] > 0 and t["connections"] > 0 and t["changes"] == 0
    # every lab connection exists, with the same weight, in the real store for that release
    missing = db.execute("""SELECT count(*) FROM lab.connection l JOIN release r USING (release_id)
        WHERE NOT EXISTS (SELECT 1 FROM connection c WHERE c.pre_neuron_id = l.pre_neuron_id
                          AND c.post_neuron_id = l.post_neuron_id AND c.synapse_count = l.synapse_count
                          AND c.valid_rel @> r.release_order AND upper_inf(c.tx))""").fetchone()[0]
    assert missing == 0
    # the MongoDB lab holds the source documents of exactly the lab neurons
    ids = {r[0] for r in db.execute("SELECT neuron_id FROM lab.neuron")}
    from backend.lab import mongo_lab
    from backend.metadata import COLLECTION
    docs = {d["dataset_neuron_id"] for d in mongo_lab()[COLLECTION].find({}, {"dataset_neuron_id": 1})}
    assert docs and docs <= ids


def test_lab_script_runs_statements_and_saves(client):
    r = lab_sql(client, "INSERT INTO lab.neuron (dataset_id, source_neuron_id, cell_type) "
                        "SELECT dataset_id, 't-1', 'T' FROM lab.dataset ORDER BY dataset_id LIMIT 1 RETURNING neuron_id;"
                        "UPDATE lab.neuron SET side = 'L' WHERE source_neuron_id = 't-1';"
                        "SELECT current_user, side FROM lab.neuron WHERE source_neuron_id = 't-1'")
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert [x["status"] for x in res] == ["INSERT 0 1", "UPDATE 1", "SELECT 1"]
    assert res[2]["rows"] == [["cc_lab", "L"]]
    again = lab_sql(client, "SELECT count(*) FROM lab.neuron WHERE source_neuron_id = 't-1'").json()
    assert again["results"][0]["rows"] == [[1]]          # committed, visible to a new connection


def test_lab_preview_and_open_transactions_are_rolled_back(client):
    r = lab_sql(client, "INSERT INTO lab.neuron (dataset_id, source_neuron_id) "
                        "SELECT dataset_id, 'preview' FROM lab.dataset LIMIT 1", rollback=True)
    assert r.status_code == 200 and "rolled back" in r.json()["executed_as"]
    r = lab_sql(client, "BEGIN; INSERT INTO lab.neuron (dataset_id, source_neuron_id) "
                        "SELECT dataset_id, 'left-open' FROM lab.dataset LIMIT 1")
    assert r.json()["results"][-1]["status"].startswith("ROLLBACK")
    n = lab_sql(client, "SELECT count(*) FROM lab.neuron WHERE source_neuron_id IN ('preview', 'left-open')").json()
    assert n["results"][0]["rows"] == [[0]]


def test_lab_failing_statement_undoes_the_script(client):
    r = lab_sql(client, "INSERT INTO lab.neuron (dataset_id, source_neuron_id) "
                        "SELECT dataset_id, 'atomic' FROM lab.dataset LIMIT 1;"
                        "UPDATE lab.connection SET synapse_count = -1")
    assert r.status_code == 400 and "connection_synapse_count_check" in r.json()["detail"]
    n = lab_sql(client, "SELECT count(*) FROM lab.neuron WHERE source_neuron_id = 'atomic'").json()
    assert n["results"][0]["rows"] == [[0]]


def test_lab_ddl_transactions_and_cascade(client):
    r = lab_sql(client, """CREATE TABLE lab.note (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                                                   neuron_id bigint NOT NULL REFERENCES lab.neuron ON DELETE CASCADE,
                                                   text text NOT NULL);
        INSERT INTO lab.note (neuron_id, text) SELECT neuron_id, 'n' FROM lab.neuron WHERE source_neuron_id = 't-1';
        BEGIN; SAVEPOINT s; DELETE FROM lab.connection; ROLLBACK TO SAVEPOINT s; COMMIT;
        DELETE FROM lab.neuron WHERE source_neuron_id = 't-1';
        SELECT (SELECT count(*) FROM lab.note), (SELECT count(*) FROM lab.connection) > 0""")
    assert r.status_code == 200, r.text
    assert r.json()["results"][-1]["rows"] == [[0, True]]


@pytest.mark.parametrize("text, error", [
    ("DELETE FROM cc.connection", "permission denied"),
    ("UPDATE cc.dataset SET name = 'x'", "permission denied"),
    ("DROP SCHEMA cc CASCADE", "must be owner"),
    ("CREATE TABLE cc.stolen (x int)", "permission denied"),
    ("RESET ROLE; DELETE FROM cc.dataset", "permission denied"),
    ("SET ROLE chronicle", "permission denied"),
    ("SET SESSION AUTHORIZATION chronicle", "permission denied"),
    ("COPY (SELECT 1) TO PROGRAM 'echo x'", "permission denied"),
])
def test_lab_role_cannot_touch_the_real_data(client, db, text, error):
    before = real_counts(db)
    r = lab_sql(client, text)
    assert r.status_code == 400 and error in r.json()["detail"], r.text
    assert real_counts(db) == before


def test_lab_timeout_cannot_be_switched_off(client, monkeypatch):
    from backend import console, lab
    monkeypatch.setattr(console, "TIMEOUT_MS", 400)
    monkeypatch.setattr(lab, "TIMEOUT_MS", 400)
    r = lab_sql(client, "SET statement_timeout = 0; SELECT pg_sleep(5)")
    assert r.status_code == 400 and "cancel" in r.json()["detail"].lower()


def test_table_editor_insert_update_delete_with_sql_shown(client):
    tables = client.get("/api/v1/lab/tables", params={"schema": "lab"}).json()
    assert {"dataset", "release", "neuron", "connection", "change_log"} <= {t["name"] for t in tables["tables"]}
    ds = client.get("/api/v1/lab/tables/lab/dataset").json()["rows"][0]["dataset_id"]
    ins = client.post("/api/v1/lab/tables/lab/neuron/insert",
                      json={"values": {"dataset_id": str(ds), "source_neuron_id": "form-1", "cell_type": "",
                                       "neuron_id": ""}})
    assert ins.status_code == 200, ins.text
    row = ins.json()["rows"][0]
    assert row["cell_type"] is None and row["neuron_id"] > 0
    assert ins.json()["sql"].startswith('INSERT INTO lab."neuron"') and "'form-1'" in ins.json()["sql"]
    key = {"neuron_id": row["neuron_id"]}
    bad = client.post("/api/v1/lab/tables/lab/neuron/update", json={"key": key, "values": {"side": "X"}})
    assert bad.status_code == 400 and "neuron_side_check" in bad.json()["detail"]
    up = client.post("/api/v1/lab/tables/lab/neuron/update", json={"key": key, "values": {"side": "R"}})
    assert up.status_code == 200 and up.json()["rows"][0]["side"] == "R"
    assert up.json()["sql"].startswith('UPDATE lab."neuron" SET "side" = \'R\'')
    dup = client.post("/api/v1/lab/tables/lab/neuron/insert",
                      json={"values": {"dataset_id": str(ds), "source_neuron_id": "form-1"}})
    assert dup.status_code == 400 and "neuron_dataset_id_source_neuron_id_key" in dup.json()["detail"]
    gone = client.post("/api/v1/lab/tables/lab/neuron/delete", json={"key": key})
    assert gone.status_code == 200 and gone.json()["rowcount"] == 1
    again = client.post("/api/v1/lab/tables/lab/neuron/delete", json={"key": key})
    assert again.status_code == 400 and "no row" in again.json()["detail"]
    log = client.get("/api/v1/lab/changes").json()
    ops = [(c["table_name"], c["operation"]) for c in log if (c["old_row"] or c["new_row"]).get("neuron_id") == key["neuron_id"]]
    assert ops == [("neuron", "DELETE"), ("neuron", "UPDATE"), ("neuron", "INSERT")]
    assert log[0]["changed_by"] == "cc_lab"


@pytest.mark.parametrize("path, body, error", [
    ("lab/tables/lab/neuron/update", {"key": {}, "values": {"side": "L"}}, "primary key"),
    ("lab/tables/lab/neuron/insert", {"values": {"no_such_column": 1}}, "unknown column"),
    ("lab/tables/lab/change_log/insert", {"values": {"change_id": 5}}, "GENERATED ALWAYS"),
    ("lab/tables/lab/no_such_table/insert", {"values": {"x": 1}}, "no table"),
])
def test_table_editor_refuses_bad_requests(client, path, body, error):
    r = client.post(f"/api/v1/{path}", json=body)
    assert r.status_code == 400 and error in r.json()["detail"], r.text


def test_real_tables_can_be_browsed_but_not_edited(client):
    r = client.get("/api/v1/lab/tables/cc/connection", params={"limit": 3}).json()
    assert len(r["rows"]) == 3 and not r["editable"] and r["primary_key"] == ["connection_id"]
    assert any(c["type"] == "CHECK" for c in r["constraints"])
    r = client.post("/api/v1/lab/tables/cc/dataset/insert", json={"values": {"code": "x"}})
    assert r.status_code in (404, 405)            # only schema lab has editing routes
    assert client.get("/api/v1/lab/tables", params={"schema": "pg_catalog"}).status_code == 422


def test_trial_writes_run_with_constraints_and_are_rolled_back(client, db):
    before = real_counts(db)
    r = client.post("/api/v1/query/sql", json={
        "target": "trial", "sql": "INSERT INTO dataset (code, name) VALUES ('trial_ds', 'x') RETURNING code"})
    assert r.status_code == 200 and r.json()["rows"] == [["trial_ds"]] and r.json()["status"] == "INSERT 0 1"
    for text, error in [("DELETE FROM connection WHERE connection_id = (SELECT min(connection_id) FROM connection)",
                         "append-only"),
                        ("INSERT INTO dataset (code, name) VALUES ('Bad Code', 'x')", "dataset_code_check"),
                        ("SELECT 1; COMMIT; DELETE FROM dataset", "multiple commands"),
                        ("DROP TABLE dataset", "Lab target"),
                        ("SELECT set_config('role', 'chronicle', false)", "permission denied")]:
        r = client.post("/api/v1/query/sql", json={"target": "trial", "sql": text})
        assert r.status_code == 400 and error in r.json()["detail"], (text, r.text)
    assert real_counts(db) == before
    assert db.execute("SELECT count(*) FROM dataset WHERE code = 'trial_ds'").fetchone()[0] == 0


def mongo(client, **body):
    return client.post("/api/v1/query/mongo", json={"target": "lab", **body})


def test_mongo_lab_crud_validator_and_unique_index(client):
    doc = {"dataset": "evaluator", "source_neuron_id": "d-1", "dataset_neuron_id": 900000001}
    assert mongo(client, op="insertOne", document=doc).status_code == 200
    dup = mongo(client, op="insertOne", document={**doc, "dataset_neuron_id": 900000002})
    assert dup.status_code == 400 and "duplicate key" in dup.json()["detail"]
    invalid = mongo(client, op="insertOne", document={"dataset": "evaluator", "source_neuron_id": "d-2"})
    assert invalid.status_code == 400 and "failed validation" in invalid.json()["detail"]
    up = mongo(client, op="updateOne", filter={"source_neuron_id": "d-1"}, update={"$set": {"note": "edited"}}).json()
    assert up["matched"] == 1 and up["modified"] == 1
    found = mongo(client, op="find", filter={"dataset": "evaluator"}).json()["documents"]
    assert [d["note"] for d in found] == ["edited"]
    rep = mongo(client, op="replaceOne", filter={"_id": found[0]["_id"]}, document={**doc, "note": "replaced"}).json()
    assert rep["modified"] == 1
    assert mongo(client, op="createIndex", keys={"note": 1}).json()["index"] == "note_1"
    assert "note_1" in [i["name"] for i in mongo(client, op="listIndexes").json()["indexes"]]
    assert mongo(client, op="dropIndex", name="note_1").status_code == 200
    assert mongo(client, op="deleteMany", filter={"dataset": "evaluator"}).json()["deleted"] == 1


def test_mongo_lab_new_collection_with_validator(client):
    v = {"$jsonSchema": {"bsonType": "object", "required": ["text"], "properties": {"text": {"bsonType": "string"}}}}
    assert mongo(client, op="createCollection", collection="notes", validator=v).status_code == 200
    ok = mongo(client, op="insertMany", collection="notes", documents=[{"text": "a"}, {"text": "b"}]).json()
    assert len(ok["inserted_ids"]) == 2
    assert mongo(client, op="insertOne", collection="notes", document={"x": 1}).status_code == 400
    names = [c["name"] for c in client.get("/api/v1/lab/mongo/collections").json()["collections"]]
    assert "notes" in names
    assert mongo(client, op="dropCollection", collection="notes").status_code == 200
    assert mongo(client, op="find", collection="system.users").status_code == 400


def test_real_document_store_stays_read_only(client):
    from backend.metadata import mongo_db, COLLECTION
    before = mongo_db()[COLLECTION].count_documents({})
    for body in ({"op": "insertOne", "document": {"x": 1}}, {"op": "deleteMany", "filter": {}},
                 {"op": "dropCollection"}):
        r = client.post("/api/v1/query/mongo", json=body)          # target defaults to the real store
        assert r.status_code == 400 and "read-only" in r.json()["detail"]
    assert mongo(client, op="find", filter={"$where": "true"}).status_code == 400
    assert mongo_db()[COLLECTION].count_documents({}) == before


def test_lab_examples_run_on_the_fixtures(client):
    """Every published lab example runs, or is rejected exactly where it is meant to be."""
    ex = json.loads((PROJECT_ROOT / "frontend/public/examples.json").read_text(encoding="utf-8"))
    rejected = {"lab-check": "check constraint", "lab-fk": "foreign key", "lab-unique": "duplicate key",
                "lab-no-real-writes": "permission denied"}
    for e in (x for x in ex["sql"] if x.get("target") == "lab"):
        r = lab_sql(client, e["sql"])
        if e["id"] in rejected:
            assert r.status_code == 400 and rejected[e["id"]] in r.json()["detail"], (e["id"], r.text)
        else:
            assert r.status_code == 200, (e["id"], r.text)
    expected_fail = {"ml-invalid": "failed validation", "ml-duplicate": "duplicate key"}
    for e in (x for x in ex["mongo"] if x.get("target") == "lab"):
        body = {k: v for k, v in e.items() if k not in ("id", "title", "target")}
        r = mongo(client, **body)
        if e["id"] in expected_fail:
            assert r.status_code == 400 and expected_fail[e["id"]] in r.json()["detail"], (e["id"], r.text)
        else:
            assert r.status_code == 200, (e["id"], r.text)


def test_reset_restores_the_copy(client):
    lab_sql(client, "DELETE FROM lab.connection; DROP VIEW lab.neuron_degree")
    r = client.post("/api/v1/lab/reset").json()
    assert r["tables"]["connections"] > 0 and r["tables"]["changes"] == 0
    assert client.get("/api/v1/lab/tables/lab/neuron_degree").status_code == 200


def test_review_token_guards_lab_writes(client, monkeypatch):
    from backend.config import get_settings
    monkeypatch.setattr(get_settings(), "cc_review_token", "secret")
    assert lab_sql(client, "SELECT 1").status_code == 401
    assert client.post("/api/v1/lab/reset").status_code == 401
    assert mongo(client, op="insertOne", document={}).status_code == 401
    assert mongo(client, op="find").status_code == 200                    # reading needs no token
    ok = client.post("/api/v1/query/sql", json={"sql": "SELECT 1", "target": "lab"},
                     headers={"X-Review-Token": "secret"})
    assert ok.status_code == 200
