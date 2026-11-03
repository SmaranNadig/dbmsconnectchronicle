"""Ingestion, integrity, bitemporal reconstruction and provenance on the
synthetic toyfly fixture (tests/fixtures/toy)."""
from __future__ import annotations

import psycopg
import pytest

from backend.etl.adapters import ValidationError
from backend.etl.loader import ingest_release

BIG_ID = "720575940596125868"   # 64-bit FlyWire-style root id


def nid(db, source_id: str) -> int:
    return db.execute("SELECT find_neuron('toyfly', %s)", (source_id,)).fetchone()[0]


def state(db, source_id, release_id, as_of=None):
    return db.execute("SELECT present, annotations FROM neuron_at_release(%s, %s, coalesce(%s, now()))",
                      (nid(db, source_id), release_id, as_of)).fetchone()


def weight(db, pre, post, release_id, as_of=None):
    rows = db.execute("""SELECT synapse_count FROM connections_at_release(%s, %s, coalesce(%s, now()), 0)
                         WHERE direction = 'out' AND partner_source_id = %s""",
                      (nid(db, pre), release_id, as_of, post)).fetchall()
    assert len(rows) <= 1
    return rows[0][0] if rows else None


def test_release_counts(db, toyfly_loaded):
    rows = dict((r[0], r[1:]) for r in db.execute(
        "SELECT version_label, neurons, connections FROM release_overview WHERE dataset = 'toyfly'"))
    assert rows == {"r1": (5, 4), "r2": (5, 5), "r3": (6, 5)}


def test_reingest_is_idempotent(db, toyfly_loaded):
    before = db.execute("SELECT count(*) FROM connection").fetchone()[0]
    assert ingest_release("toyfly", "r2").status == "already_loaded"
    assert db.execute("SELECT count(*) FROM connection").fetchone()[0] == before


def test_unchanged_facts_are_stored_once(db, toyfly_loaded):
    # 2->3 = 3 holds in r1, r2 and r3: one row, valid from r1 with no end
    rows = db.execute("""SELECT valid_from_release, valid_until_release FROM connection_history
                         WHERE dataset = 'toyfly' AND pre_source_id = '2' AND post_source_id = '3'""").fetchall()
    assert rows == [("r1", None)]


def test_source_ids_preserved_losslessly(db, toyfly_loaded):
    assert db.execute("SELECT source_neuron_id FROM dataset_neuron WHERE source_neuron_id = %s",
                      (BIG_ID,)).fetchone()[0] == BIG_ID


def test_reconstruct_neuron_per_release(db, toyfly_loaded):
    r = {k: v["id"] for k, v in toyfly_loaded.items()}
    assert state(db, "4", r["r1"]) == (True, {"cell_type": "D"})
    assert state(db, "4", r["r2"]) == (False, {})          # removed in r2
    assert state(db, "4", r["r3"]) == (True, {"cell_type": "D"})
    assert state(db, "2", r["r1"])[1] == {"cell_type": "B"}
    assert state(db, "2", r["r2"])[1] == {"cell_type": "B2"}
    assert state(db, "3", r["r1"])[1] == {}                # untyped in r1
    assert state(db, "3", r["r2"])[1] == {"cell_type": "C"}


def test_reconstruct_connections_per_release(db, toyfly_loaded):
    r = {k: v["id"] for k, v in toyfly_loaded.items()}
    assert [weight(db, "1", "2", r[x]) for x in ("r1", "r2", "r3")] == [5, 8, 5]
    assert [weight(db, "3", "4", r[x]) for x in ("r1", "r2", "r3")] == [7, None, None]
    assert weight(db, "5", "1", r["r2"]) == 0               # zero weights are allowed


def test_transaction_time_as_of(db, toyfly_loaded):
    """Right after r1 was loaded the database believed B open-ended; today it
    knows B only held in r1. Both beliefs are queryable."""
    after_r1 = toyfly_loaded["r1"]["loaded_at"]
    then = db.execute("SELECT annotation_value, valid_from_release, valid_until_release "
                      "FROM neuron_as_of(%s, %s)", (nid(db, "2"), after_r1)).fetchall()
    assert then == [("B", "r1", None)]
    now = db.execute("SELECT annotation_value, valid_from_release, valid_until_release "
                     "FROM neuron_as_of(%s, now())", (nid(db, "2"),)).fetchall()
    assert now == [("B", "r1", "r2"), ("B2", "r2", None)]
    # and r2 did not exist yet at that transaction time
    r2 = toyfly_loaded["r2"]["id"]
    assert weight(db, "1", "2", r2, after_r1) == 5     # belief then: r1 value extends forward


def test_history_is_append_only(db, toyfly_loaded):
    cid = db.execute("SELECT connection_id FROM connection WHERE upper_inf(tx) LIMIT 1").fetchone()[0]
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        db.execute("UPDATE connection SET synapse_count = synapse_count + 1 WHERE connection_id = %s", (cid,))
    db.rollback()
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        db.execute("DELETE FROM connection WHERE connection_id = %s", (cid,))
    db.rollback()


def test_bitemporal_exclusion_constraint(db, toyfly_loaded):
    """A second current belief for the same pair and overlapping releases is rejected."""
    row = db.execute("""SELECT dataset_id, pre_neuron_id, post_neuron_id, provenance_id FROM connection
                        WHERE upper_inf(tx) AND upper_inf(valid_rel) LIMIT 1""").fetchone()
    with pytest.raises(psycopg.errors.ExclusionViolation):
        db.execute("""INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count,
                                              valid_rel, provenance_id, source_lines)
                      VALUES (%s, %s, %s, 1, int4range(1, NULL), %s, '{1}')""", row)
    db.rollback()


def test_check_constraints(db, toyfly_loaded):
    row = db.execute("SELECT dataset_id, pre_neuron_id, post_neuron_id, provenance_id FROM connection LIMIT 1").fetchone()
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("""INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count,
                                              valid_rel, provenance_id, source_lines)
                      VALUES (%s, %s, %s, -3, int4range(9, NULL), %s, '{1}')""", row)
    db.rollback()


def test_edges_cannot_cross_datasets(db, toyfly_loaded):
    """Composite FK: an edge's endpoints must belong to the edge's dataset."""
    other = db.execute("INSERT INTO dataset (code, name) VALUES ('fk_probe', 'probe') RETURNING dataset_id").fetchone()[0]
    prov = db.execute("SELECT provenance_id FROM connection LIMIT 1").fetchone()[0]
    # toyfly neurons 4 -> 1 have no edge, so only the FK can object
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("""INSERT INTO connection (dataset_id, pre_neuron_id, post_neuron_id, synapse_count,
                                              valid_rel, provenance_id, source_lines)
                      VALUES (%s, %s, %s, 1, int4range(9, NULL), %s, '{1}')""",
                   (other, nid(db, "4"), nid(db, "1"), prov))
    db.rollback()


def test_provenance_trace(db, toyfly_loaded):
    cid = db.execute("""SELECT connection_id FROM connection_history
                        WHERE dataset = 'toyfly' AND pre_source_id = '3' AND post_source_id = '4'
                        ORDER BY recorded_from LIMIT 1""").fetchone()[0]
    t = db.execute("SELECT trace_provenance('connection', %s)", (cid,)).fetchone()[0]
    assert t["source_file"]["archive_path"] == "toyfly/r1/edges.csv"
    assert t["source_lines"] == [4]                     # header is line 1
    assert t["release"]["version_label"] == "r1"
    assert t["dataset"]["code"] == "toyfly"
    assert t["provenance"]["transformation_name"] == "load_pair_synapses"
    assert len(t["source_file"]["sha256"]) == 64


def test_unknown_neuron_rows_skipped_and_reported(db, toyskip_loaded):
    res = toyskip_loaded
    assert res.status == "loaded"
    assert res.stats["skipped_rows_unknown_neuron"] == {"count": 1, "rows": [{"source_id": "77", "line": 4}]}
    assert db.execute("""SELECT count(*) FROM dataset_neuron JOIN dataset USING (dataset_id)
                         WHERE code = 'toyskip'""").fetchone()[0] == 2
    assert db.execute("""SELECT count(*) FROM annotation a JOIN dataset_neuron USING (dataset_neuron_id)
                         JOIN dataset d USING (dataset_id)
                         WHERE d.code = 'toyskip' AND a.annotation_key = 'class'""").fetchone()[0] == 2


def test_interrupted_run_is_closed_on_retry(db, test_database):
    """Simulates a client/server crash: a run left 'running' is closed when the release is retried."""
    with pytest.raises(ValidationError):
        ingest_release("toyorphan", "r1")          # registers the release (and fails as designed)
    rel = db.execute("""SELECT release_id FROM release JOIN dataset USING (dataset_id)
                        WHERE code = 'toyorphan'""").fetchone()[0]
    run = db.execute("INSERT INTO ingestion_run (release_id, pipeline_version) VALUES (%s, 'x') RETURNING run_id",
                     (rel,)).fetchone()[0]
    db.commit()
    with pytest.raises(ValidationError):
        ingest_release("toyorphan", "r1")
    status, err = db.execute("SELECT status, error_message FROM ingestion_run WHERE run_id = %s", (run,)).fetchone()
    assert status == "failed" and err.startswith("interrupted")


@pytest.mark.parametrize("dataset, message", [
    ("toyorphan", "endpoints are not in the neuron file"),
    ("toydup", "duplicate (pre, post) pairs"),
])
def test_bad_release_rolls_back(db, test_database, dataset, message):
    with pytest.raises(ValidationError, match=message.replace("(", r"\(").replace(")", r"\)")):
        ingest_release(dataset, "r1")
    status, err = db.execute("""SELECT r.status, r.error_message FROM ingestion_run r
                                JOIN release USING (release_id) JOIN dataset d USING (dataset_id)
                                WHERE d.code = %s ORDER BY run_id DESC LIMIT 1""", (dataset,)).fetchone()
    assert status == "failed" and message in err
    assert db.execute("""SELECT count(*) FROM dataset_neuron JOIN dataset USING (dataset_id)
                         WHERE code = %s""", (dataset,)).fetchone()[0] == 0
    assert db.execute("""SELECT ingestion_status FROM release JOIN dataset USING (dataset_id)
                         WHERE code = %s""", (dataset,)).fetchone()[0] == "failed"
