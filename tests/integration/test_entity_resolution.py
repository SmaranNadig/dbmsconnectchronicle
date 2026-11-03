"""Entity resolution on synthetic stand-ins for hemibrain and FlyWire
(tests/fixtures/toy/raw/{hemibrain,flywire}/t1)."""
from __future__ import annotations

import psycopg
import pytest

from backend.etl.loader import ingest_release


@pytest.fixture(scope="module")
def mapped(test_database):
    from backend.db import connect
    assert ingest_release("hemibrain", "t1").status == "loaded"
    assert ingest_release("flywire", "t1").status == "loaded"
    with connect() as conn:
        conn.execute("SELECT assign_source_identity('hemibrain')")
        conn.execute("SELECT assign_source_identity('flywire')")
        crosswalk = conn.execute("SELECT build_type_crosswalk()").fetchone()[0]
        fw, hb = (conn.execute("""SELECT release_id FROM release JOIN dataset USING (dataset_id)
                                  WHERE code = %s""", (c,)).fetchone()[0] for c in ("flywire", "hemibrain"))
        result = conn.execute("SELECT propose_crosswalk_mappings(%s, %s)", (fw, hb)).fetchone()[0]
        conn.commit()
    return crosswalk, result


def crosswalk_rows(db):
    return set(db.execute("""
        SELECT f.source_neuron_id, h.source_neuron_id, m.confidence::float, m.review_status, m.evidence->>'reason'
        FROM neuron_mapping m
        JOIN dataset_neuron f ON f.dataset_neuron_id = m.dataset_neuron_id
        LEFT JOIN neuron_mapping hm ON hm.canonical_neuron_id = m.canonical_neuron_id AND hm.method = 'source_identity'
        LEFT JOIN dataset_neuron h ON h.dataset_neuron_id = hm.dataset_neuron_id
        WHERE m.method = 'published_type_crosswalk'""").fetchall())


def test_every_source_neuron_has_an_identity(db, mapped):
    assert db.execute("""SELECT count(*) FROM dataset_neuron dn JOIN dataset d USING (dataset_id)
                         WHERE d.code IN ('hemibrain', 'flywire') AND NOT EXISTS
                         (SELECT 1 FROM neuron_mapping m WHERE m.dataset_neuron_id = dn.dataset_neuron_id
                          AND m.method = 'source_identity')""").fetchone()[0] == 0


def test_identity_assignment_is_idempotent(db, mapped):
    assert db.execute("SELECT assign_source_identity('flywire')").fetchone()[0] == 0


def test_candidates_and_unresolved(db, mapped):
    assert crosswalk_rows(db) == {
        ("100", "10", 1.0, "proposed", None),                                     # unique type + side
        ("101", "11", 0.5, "proposed", None),                                     # two T2 candidates
        ("101", "12", 0.5, "proposed", None),
        ("102", None, None, "unresolved", "no hemibrain neuron of this type and side"),  # T3 only on left
        ("103", None, None, "unresolved", "no hemibrain neuron of this type and side"),  # T9 unknown
    }   # 104 has no hemibrain_type and is not considered


def test_numeric_ids_are_not_matched_by_value(db, mapped):
    """No mapping links neurons just because ids look alike."""
    assert db.execute("""SELECT count(*) FROM neuron_mapping WHERE method NOT IN
                         ('source_identity', 'published_type_crosswalk')""").fetchone()[0] == 0


def test_approve_merges_identity(db, mapped):
    mid = db.execute("""SELECT m.mapping_id FROM neuron_mapping m JOIN dataset_neuron f USING (dataset_neuron_id)
                        WHERE f.source_neuron_id = '100' AND m.method = 'published_type_crosswalk'""").fetchone()[0]
    db.execute("SELECT review_mapping(%s, 'approved', 'tester')", (mid,))
    approved = db.execute("""SELECT m.method, m.canonical_neuron_id FROM neuron_mapping m
                             JOIN dataset_neuron f USING (dataset_neuron_id)
                             WHERE f.source_neuron_id = '100' AND m.review_status = 'approved'""").fetchall()
    assert [a[0] for a in approved] == ["published_type_crosswalk"]
    members = db.execute("""SELECT count(*) FROM neuron_mapping WHERE canonical_neuron_id = %s
                            AND review_status = 'approved'""", (approved[0][1],)).fetchone()[0]
    assert members == 2   # hemibrain 10 and FlyWire 100 now share one canonical neuron
    db.rollback()


def test_one_approved_mapping_per_neuron(db, mapped):
    rows = db.execute("""SELECT m.mapping_id FROM neuron_mapping m JOIN dataset_neuron f USING (dataset_neuron_id)
                         WHERE f.source_neuron_id = '101' AND m.method = 'published_type_crosswalk'
                         ORDER BY mapping_id""").fetchall()
    db.execute("SELECT review_mapping(%s, 'approved', 'tester')", (rows[0][0],))
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("UPDATE neuron_mapping SET review_status = 'approved', reviewed_at = now() WHERE mapping_id = %s",
                   (rows[1][0],))
    db.rollback()


def test_unresolved_requires_null_canonical(db, mapped):
    mid = db.execute("SELECT mapping_id FROM neuron_mapping WHERE review_status = 'unresolved' LIMIT 1").fetchone()[0]
    canon = db.execute("SELECT canonical_neuron_id FROM canonical_neuron LIMIT 1").fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE neuron_mapping SET canonical_neuron_id = %s WHERE mapping_id = %s", (canon, mid))
    db.rollback()
