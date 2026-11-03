"""ConnectomeDiff correctness on the synthetic toyfly fixture."""
from __future__ import annotations

import psycopg
import pytest


def run_diff(db, a, b, as_of=None):
    run = db.execute("SELECT run_temporal_diff(%s, %s, coalesce(%s, now()))", (a, b, as_of)).fetchone()[0]
    db.commit()
    return run


def events(db, run):
    return set(db.execute("""SELECT entity_type, change_type, neuron_source_id, post_source_id,
                                    annotation_key, old_value, new_value, old_weight, new_weight
                             FROM diff_event_detail WHERE diff_run_id = %s""", (run,)).fetchall())


def test_diff_r1_r2(db, toyfly_loaded):
    r = {k: v["id"] for k, v in toyfly_loaded.items()}
    got = events(db, run_diff(db, r["r1"], r["r2"]))
    assert got == {
        ("neuron", "removed", "4", None, None, None, None, None, None),
        ("neuron", "added", "5", None, None, None, None, None, None),
        ("annotation", "modified", "2", None, "cell_type", "B", "B2", None, None),
        ("annotation", "added", "3", None, "cell_type", None, "C", None, None),
        ("annotation", "removed", "4", None, "cell_type", "D", None, None, None),
        ("annotation", "added", "5", None, "cell_type", None, "E", None, None),
        ("connection", "modified", "1", "2", None, None, None, 5, 8),
        ("connection", "removed", "3", "4", None, None, None, 7, None),
        ("connection", "added", "3", "5", None, None, None, None, 2),
        ("connection", "added", "5", "1", None, None, None, None, 0),
    }   # 2->3 and the 64-bit neuron's edge are unchanged and absent


def test_weight_delta_and_stats(db, toyfly_loaded):
    r = {k: v["id"] for k, v in toyfly_loaded.items()}
    run = run_diff(db, r["r1"], r["r2"])
    assert db.execute("""SELECT weight_delta FROM diff_event_detail WHERE diff_run_id = %s
                         AND entity_type = 'connection' AND change_type = 'modified'""", (run,)).fetchone()[0] == 3
    stats = db.execute("SELECT stats FROM diff_run WHERE diff_run_id = %s", (run,)).fetchone()[0]
    assert stats == {"neuron_added": 1, "neuron_removed": 1, "annotation_added": 2, "annotation_removed": 1,
                     "annotation_modified": 1, "connection_added": 2, "connection_removed": 1,
                     "connection_modified": 1}


def test_same_value_in_different_rows_is_unchanged(db, toyfly_loaded):
    """1->2 is 5 in r1 and r3 (stored as different versions): no event."""
    r = {k: v["id"] for k, v in toyfly_loaded.items()}
    got = events(db, run_diff(db, r["r1"], r["r3"]))
    assert not any(e[0] == "connection" and e[2:4] == ("1", "2") for e in got)
    assert not any(e[0] == "neuron" and e[2] == "4" for e in got)


def test_diff_is_idempotent(db, toyfly_loaded):
    r = {k: v["id"] for k, v in toyfly_loaded.items()}
    as_of = db.execute("SELECT now() - interval '1 second'").fetchone()[0]
    first = run_diff(db, r["r2"], r["r3"], as_of)
    second = run_diff(db, r["r2"], r["r3"], as_of)
    assert first == second
    assert db.execute("SELECT count(*) FROM diff_run WHERE release_a = %s AND release_b = %s AND as_of = %s",
                      (r["r2"], r["r3"], as_of)).fetchone()[0] == 1


def test_diff_events_link_to_provenance(db, toyfly_loaded):
    r = {k: v["id"] for k, v in toyfly_loaded.items()}
    run = run_diff(db, r["r1"], r["r2"])
    rec = db.execute("""SELECT record_b_id FROM diff_event_detail WHERE diff_run_id = %s
                        AND entity_type = 'connection' AND change_type = 'added' AND post_source_id = '5'""",
                     (run,)).fetchone()[0]
    t = db.execute("SELECT trace_provenance('connection', %s)", (rec,)).fetchone()[0]
    assert t["source_file"]["archive_path"] == "toyfly/r2/edges.csv"


def test_temporal_diff_rejects_cross_dataset(db, toyfly_loaded):
    """Temporal mode is restricted to one dataset by a CHECK constraint."""
    ds = db.execute("INSERT INTO dataset (code, name) VALUES ('diff_probe', 'probe') RETURNING dataset_id").fetchone()[0]
    other = db.execute("""INSERT INTO release (dataset_id, version_label, release_order)
                          VALUES (%s, 'p1', 1) RETURNING release_id""", (ds,)).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("SELECT run_temporal_diff(%s, %s)", (toyfly_loaded["r1"]["id"], other))
    db.rollback()
