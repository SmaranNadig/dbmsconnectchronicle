"""Design: the relation catalogue matches the live schema, every key and
functional dependency holds on loaded data, the descriptions and the data
dictionary come from the catalog, the query catalogue runs, and the design
endpoints (diagrams, anomalies, file maintenance). Test database with the
toyfly + toyskip fixtures."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.design import dictionary, normalisation, queries
from backend.design.relations import RELATIONS


@pytest.fixture(scope="module")
def client(toyfly_loaded, toyskip_loaded):
    from backend.main import app
    from backend.metadata import sync_release
    for r in ("r1", "r2", "r3"):
        sync_release("toyfly", r)
    with TestClient(app) as c:
        assert c.post("/api/v1/lab/reset").status_code == 200
        yield c


def test_relation_catalogue_covers_the_live_schema(client, db):
    cov = normalisation.coverage(db)
    assert cov["missing"] == [] and cov["extra"] == [] and cov["column_mismatch"] == {}
    assert cov["live_tables"] == len(RELATIONS)


def test_every_relation_is_analysed_and_violations_are_documented():
    a = normalisation.analyse()
    assert a["summary"]["relations"] == len(RELATIONS)
    assert a["summary"]["undocumented_violations"] == []
    for r in a["relations"]:
        if r["schema"] == "lab":
            assert r["highest_form"] == "BCNF", r["relation"]
        if "bcnf_decomposition" in r:
            assert r["bcnf_decomposition"]["lossless"], r["relation"]


def test_keys_and_dependencies_hold_on_loaded_data(client, db):
    result = normalisation.verify(db, scan_data=True)
    bad = [(r["relation"], d["fd"]) for r in result["relations"] for d in r["dependencies"] if d["status"] == "violated"]
    assert result["ok"] and not bad, bad


def test_descriptions_come_from_the_catalog(client, db):
    for r in RELATIONS:
        text = db.execute("SELECT obj_description(%s::regclass, 'pg_class')", (f"{r['schema']}.{r['name']}",)).fetchone()[0]
        assert text == r["purpose"], r["name"]
    d = dictionary.build(db)
    assert d["summary"]["tables"] == len(RELATIONS) == d["summary"]["described_tables"]
    conn = next(t for t in d["postgres"]["tables"] if (t["schema"], t["name"]) == ("cc", "connection"))
    kinds = {k["kind"] for k in conn["constraints"]}
    assert {"primary key", "foreign key", "check", "exclusion"} <= kinds
    assert any(m["validator"] for m in d["mongo"])          # the lab collection's $jsonSchema


def test_query_catalogue_runs(client):
    """Every query runs on the toy data. Queries meant to be rejected may find
    no row to conflict with here (they copy real hemibrain rows); when they are
    rejected, it must be for the expected reason. The full checks run on the
    loaded data (scripts/design_docs.py queries → docs/QUERIES.md)."""
    for q in queries.catalogue()["queries"]:
        r = queries.run(q["id"])
        if "error" in (q.get("expect") or {}):
            assert not r.get("error") or q["expect"]["error"] in r["error"], (q["id"], r["error"])
        else:
            assert not r.get("error"), (q["id"], r.get("error"))


def test_dfd_endpoints(client):
    r = client.get("/api/v1/design/dfd").json()
    assert r["check"]["ok"] and len(r["diagrams"]) >= 3
    svg = client.get("/api/v1/design/dfd/level1.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/v1/design/dfd/nope.svg").status_code == 404


def test_normalisation_and_dictionary_endpoints(client):
    n = client.get("/api/v1/design/normalisation").json()
    assert n["analysis"]["summary"]["relations"] == len(RELATIONS) and n["walkthrough"][-1]["form"] == "BCNF"
    v = client.post("/api/v1/design/normalisation/verify").json()
    assert v["verification"]["ok"]
    d = client.get("/api/v1/design/dictionary").json()
    assert d["summary"]["tables"] == len(RELATIONS)
    q = client.get("/api/v1/design/queries").json()
    assert len(q["queries"]) == len(queries.catalogue()["queries"])


def test_anomalies_are_shown_and_rolled_back(client, db):
    before = client.get("/api/v1/lab/status").json()["tables"]
    a = client.post("/api/v1/design/anomalies").json()
    assert a["update"]["normalised"]["distinct_values"] == 1
    assert a["insert"]["unnormalised"]["ok"] is False and a["insert"]["normalised"]["ok"] is True
    assert client.get("/api/v1/lab/status").json()["tables"] == before


def test_file_maintenance(client):
    files = client.get("/api/v1/design/files").json()["tables"]
    assert {f"{t['schema']}.{t['table']}" for t in files} >= {"cc.connection", "lab.connection"}
    assert all(t["file"] for t in files)
    r = client.post("/api/v1/design/maintenance/vacuum").json()
    assert len(r["statements"]) == 5 and all(t["after"]["dead_tuples"] == 0 for t in r["tables"])
    assert client.post("/api/v1/design/maintenance/drop").status_code == 422
