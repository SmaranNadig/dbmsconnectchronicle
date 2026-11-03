"""MongoDB source records, brain-region synapse counts and the ER-diagram
views, on the toyfly fixture (tests/fixtures/toy/raw/toyfly)."""
from __future__ import annotations

import pytest

from backend import metadata
from backend.etl.adapters import ValidationError
from backend.etl.regions import load_regions


@pytest.fixture(scope="module")
def synced(toyfly_loaded):
    return {r: metadata.sync_release("toyfly", r) for r in ("r1", "r2", "r3")}


@pytest.fixture(scope="module")
def regions(toyfly_loaded):
    return {r: load_regions("toyfly", r) for r in ("r1", "r2")}


# ------------------------------------------------------------------ MongoDB
def test_one_document_per_neuron_with_original_rows(synced):
    assert [synced[r]["documents"] for r in ("r1", "r2", "r3")] == [5, 5, 6]
    doc = metadata.source_record("toyfly:2")
    assert doc["source_neuron_id"] == "2"
    by_release = {v["release"]: v["records"] for v in doc["releases"].values()}
    assert by_release["r1"][0]["fields"] == {"id": "2", "type": "B"}
    assert by_release["r2"][0]["fields"] == {"id": "2", "type": "B2"}
    assert by_release["r1"][0]["line"] == 3 and len(by_release["r1"][0]["sha256"]) == 64


def test_empty_cells_are_left_out_and_ids_stay_text(synced):
    doc = metadata.source_record("toyfly:3")
    r1 = next(v for v in doc["releases"].values() if v["release"] == "r1")
    assert r1["records"][0]["fields"] == {"id": "3"}            # type is empty in r1
    big = metadata.source_record("toyfly:720575940596125868")
    assert big["source_neuron_id"] == "720575940596125868"


def test_neuron_absent_from_a_release_has_no_entry_for_it(synced):
    doc = metadata.source_record("toyfly:4")
    assert sorted(v["release"] for v in doc["releases"].values()) == ["r1", "r3"]


def test_resync_is_idempotent_and_recorded(db, synced):
    again = metadata.sync_release("toyfly", "r2")
    assert again["documents"] == 5
    assert metadata.mongo_db()[metadata.COLLECTION].count_documents({"dataset": "toyfly"}) == 6
    n = db.execute("""SELECT count(*) FROM metadata_sync s JOIN release r USING (release_id)
                      WHERE r.version_label = 'r2' AND s.status = 'completed'""").fetchone()[0]
    assert n >= 2


def test_cross_store_references_are_consistent(synced):
    report = metadata.check_consistency("toyfly")
    assert report["consistent"], report
    assert report["postgres_neurons"] == report["mongo_documents"] == 6


def test_check_detects_a_missing_document(synced):
    coll = metadata.mongo_db()[metadata.COLLECTION]
    saved = coll.find_one({"_id": "toyfly:5"})
    coll.delete_one({"_id": "toyfly:5"})
    try:
        report = metadata.check_consistency("toyfly")
        assert not report["consistent"] and report["neurons_without_document"] == ["toyfly:5"]
    finally:
        coll.insert_one(saved)


def test_unknown_release_is_refused(toyfly_loaded):
    with pytest.raises(ValidationError):
        metadata.sync_release("toyfly", "r9")


def test_rows_for_skipped_neurons_are_reported(toyskip_loaded):
    assert toyskip_loaded.status == "loaded"
    stats = metadata.sync_release("toyskip", "r1")
    assert stats["rows_for_unknown_neurons"] == 1


def test_api_neuron_includes_source_records(synced):
    from fastapi.testclient import TestClient

    from backend.main import app
    with TestClient(app) as client:
        n = client.get("/api/v1/neurons", params={"dataset": "toyfly", "source_id": "2"}).json()[0]
        detail = client.get(f"/api/v1/neurons/{n['dataset_neuron_id']}").json()
    assert detail["source_records_status"] == "ok"
    assert [r["release"] for r in detail["source_records"]] == ["r1", "r2", "r3"]


# ------------------------------------------------------------------ regions
def _region_counts(db, release):
    return {(src, code): (pre, post) for src, code, pre, post in db.execute(
        """SELECT dn.source_neuron_id, l.code, s.pre_synapses, s.post_synapses
           FROM neuron_region_synapses s JOIN dataset_neuron dn USING (dataset_neuron_id)
           JOIN brain_region_label l USING (brain_region_id) JOIN release r USING (release_id)
           JOIN dataset d ON d.dataset_id = r.dataset_id
           WHERE d.code = 'toyfly' AND r.version_label = %s""", (release,)).fetchall()}


def test_region_synapses_are_aggregated_per_neuron(db, regions):
    assert _region_counts(db, "r1") == {
        ("1", "SLP_R"): (3, 0), ("1", "ME_L"): (2, 0), ("1", "MB_VL_R"): (0, 4),
        ("2", "SLP_R"): (3, 3), ("2", "ME_L"): (0, 2), ("3", "SLP_R"): (0, 3), ("3", "EB"): (7, 0), ("4", "EB"): (0, 7),
        ("720575940596125868", "MB_VL_R"): (4, 0)}
    assert _region_counts(db, "r2")[("1", "SLP_R")] == (8, 0)


def test_source_codes_recorded_as_aliases(db, regions):
    rows = dict(db.execute("""SELECT a.source_code, l.code FROM brain_region_alias a
                              JOIN brain_region_label l USING (brain_region_id) JOIN dataset d USING (dataset_id)
                              WHERE d.code = 'toyfly'""").fetchall())
    assert rows == {"SLP(R)": "SLP_R", "ME(L)": "ME_L", "EB": "EB", "a'L(R)": "MB_VL_R"}


def test_primary_region(db, regions):
    rows = dict(db.execute("""SELECT dn.source_neuron_id, l.code FROM neuron_primary_region p
                              JOIN dataset_neuron dn USING (dataset_neuron_id)
                              JOIN brain_region_label l USING (brain_region_id) JOIN release r USING (release_id)
                              WHERE r.version_label = 'r1' AND dn.dataset_id = r.dataset_id""").fetchall())
    assert rows["1"] == "MB_VL_R" and rows["3"] == "EB" and rows["2"] == "SLP_R"


def test_unknown_region_code_fails_and_keeps_earlier_rows(db, regions):
    with pytest.raises(ValidationError, match="XYZ"):
        load_regions("toyfly", "r3")
    assert len(_region_counts(db, "r1")) == 9 and _region_counts(db, "r3") == {}


def test_region_reload_replaces_rows(db, regions):
    load_regions("toyfly", "r1")
    assert len(_region_counts(db, "r1")) == 9


# ------------------------------------------------------------------ ER views
@pytest.fixture(scope="module")
def identities(toyfly_loaded, regions):
    from backend.db import connect
    with connect() as conn:
        conn.execute("SELECT assign_source_identity('toyfly')")
        conn.commit()


def _canonical(db, source_id):
    return db.execute("SELECT canonical_neuron_id FROM er.canonical_neuron WHERE name = %s",
                      (f"toyfly:{source_id}",)).fetchone()[0]


def test_er_connection_links_canonical_neurons_with_validity(db, identities):
    one, two = _canonical(db, "1"), _canonical(db, "2")
    rows = db.execute("""SELECT synapse_count, weight, v1.version_label, v2.version_label
                         FROM er.connection c JOIN release v1 ON v1.release_id = c.valid_from
                         LEFT JOIN release v2 ON v2.release_id = c.valid_to
                         WHERE from_neuron_id = %s AND to_neuron_id = %s AND transaction_to IS NULL
                         ORDER BY v1.release_order""", (one, two)).fetchall()
    assert rows == [(5, 5, "r1", "r2"), (8, 8, "r2", "r3"), (5, 5, "r3", None)]


def test_er_annotation_and_dataset_neuron(db, identities):
    two = _canonical(db, "2")
    ann = db.execute("""SELECT annotation_value, v1.version_label, v2.version_label, source
                        FROM er.annotation a JOIN release v1 ON v1.release_id = a.valid_from
                        LEFT JOIN release v2 ON v2.release_id = a.valid_to
                        WHERE canonical_neuron_id = %s AND annotation_type = 'cell_type'
                        ORDER BY v1.release_order""", (two,)).fetchall()
    assert [a[:3] for a in ann] == [("B", "r1", "r2"), ("B2", "r2", None)]
    assert ann[0][3] == "toyfly/r1/neurons.csv:3"
    dn = db.execute("""SELECT external_neuron_id, original_type, original_region, raw_metadata
                       FROM er.dataset_neuron WHERE raw_metadata = 'toyfly:2'""").fetchone()
    assert dn == ("2", "B2", "SLP_R", "toyfly:2")


def test_er_canonical_neuron_and_record_provenance(db, identities):
    row = db.execute("""SELECT cell_type, brain_region, alternative_names FROM er.canonical_neuron
                        WHERE name = 'toyfly:1'""").fetchone()
    assert row == ("A", "SLP_R", ["toyfly:1"])
    cid = db.execute("""SELECT c.connection_id FROM connection c
                        JOIN dataset_neuron a ON a.dataset_neuron_id = c.pre_neuron_id
                        JOIN dataset_neuron b ON b.dataset_neuron_id = c.post_neuron_id
                        WHERE a.metadata_doc_id = 'toyfly:2' AND b.metadata_doc_id = 'toyfly:3'
                          AND upper_inf(c.tx)""").fetchone()[0]
    prov = db.execute("SELECT source_file, source_record_id, row_number FROM er.record_provenance('connection', %s)",
                      (cid,)).fetchall()
    assert prov == [("toyfly/r1/edges.csv", "2->3", 3)]
