"""Forking and retrying an interrupted encounter with real PostgreSQL constraints."""

import pytest
from psycopg.errors import CheckViolation
from psycopg.types.json import Jsonb

from backend.memory import advance, branch_start, create_run, graph, load_protocol, restore
from backend.memory_analysis import benchmark
from backend.memory_model import Pathway, fingerprint


def fixture_snapshot(db):
    (dataset,) = db.execute(
        "INSERT INTO dataset(code, name) VALUES ('memory_fixture', 'Memory fixture') RETURNING dataset_id"
    ).fetchone()
    (release,) = db.execute(
        "INSERT INTO release(dataset_id, version_label, release_order) VALUES (%s, 'one', 1) RETURNING release_id",
        (dataset,),
    ).fetchone()
    (provenance,) = db.execute(
        """INSERT INTO provenance(dataset_id, release_id, transformation_name, transformation_version)
           VALUES (%s, %s, 'test', '1') RETURNING provenance_id""",
        (dataset, release),
    ).fetchone()
    neurons = [
        neuron
        for neuron, in db.execute(
            """INSERT INTO dataset_neuron(dataset_id, first_release_id, provenance_id, source_neuron_id)
               SELECT %s, %s, %s, number::text FROM generate_series(0, 13) number
               RETURNING dataset_neuron_id""",
            (dataset, release, provenance),
        )
    ]
    (snapshot,) = db.execute(
        "INSERT INTO memory_snapshot(release_id, digest, sources) VALUES (%s, %s, %s) RETURNING snapshot_id",
        (release, "1" * 64, Jsonb({})),
    ).fetchone()
    pairs = [(i % 4, i, "input", f"PN{i % 4}") for i in range(4, 12)]
    pairs += [(i, 12, "output", "KC") for i in range(4, 12)]
    pairs.append((13, 12, "teaching", "DAN"))
    for line, (pre, post, pathway, channel) in enumerate(pairs, 2):
        (connection,) = db.execute(
            """INSERT INTO connection(dataset_id, pre_neuron_id, post_neuron_id, synapse_count,
                                      valid_rel, provenance_id, source_lines)
               VALUES (%s, %s, %s, 5, int4range(1, NULL), %s, ARRAY[%s]::int[]) RETURNING connection_id""",
            (dataset, neurons[pre], neurons[post], provenance, line),
        ).fetchone()
        db.execute(
            "INSERT INTO memory_edge VALUES (%s, %s, %s, %s, 5, %s)",
            (snapshot, connection, pathway, channel, line),
        )
    return snapshot


def test_failed_trial_rolls_back_and_can_resume(db, monkeypatch):
    snapshot = fixture_snapshot(db)
    run = create_run(db, 42)
    branch = branch_start(db, run, None, 0, "paired_A")
    advance(db, branch, 9)
    model = Pathway(graph(db, snapshot), 42, "paired_A")
    initial = restore(db, branch, 9, model)
    encounter = Pathway.encounter

    def invalid_gain(self, state, spec, step, mode):
        next_state, event = encounter(self, state, spec, step, mode)
        next_state[0] = 10001
        return next_state, event

    with monkeypatch.context() as patch:
        patch.setattr(Pathway, "encounter", invalid_gain)
        with pytest.raises(CheckViolation):
            advance(db, branch, 10)
    assert db.execute("SELECT max(step) FROM memory_trial WHERE branch_id=%s", (branch,)).fetchone() == (9,)
    assert db.execute(
        "SELECT count(*) FROM memory_change WHERE branch_id=%s AND step=10", (branch,)
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT count(*) FROM memory_checkpoint WHERE branch_id=%s AND step=10", (branch,)
    ).fetchone() == (0,)

    expected, _ = model.encounter(initial, load_protocol(db, run), 10, "paired_A")
    advance(db, branch, 10)
    advance(db, branch, 10)
    assert fingerprint(restore(db, branch, 10, model)) == fingerprint(expected)
    assert db.execute("SELECT count(*) FROM memory_trial WHERE branch_id=%s", (branch,)).fetchone() == (11,)

    with pytest.raises(CheckViolation), db.transaction():
        db.execute("INSERT INTO memory_checkpoint VALUES (%s, 9, %s)", (branch, b"broken checkpoint"))


def test_fork_copy_and_storage_equivalence(db):
    snapshot = fixture_snapshot(db)
    run = create_run(db, 27)
    root = branch_start(db, run, None, 0, "paired_A")
    advance(db, root, 45)
    fork = branch_start(db, run, root, 36, "paired_B")
    assert fork == branch_start(db, run, root, 36, "paired_B")
    model = Pathway(graph(db, snapshot), 27, "paired_A")
    assert fingerprint(restore(db, fork, 36, model)) == fingerprint(restore(db, root, 36, model))
    advance(db, fork, 45)
    assert (
        db.execute(
            "SELECT first_changed_state FROM memory_divergence WHERE branch_id=%s", (fork,)
        ).fetchone()[0]
        is not None
    )

    result = benchmark(db, fork)
    assert result["verified"] == 10
    assert result["first_noncheckpoint"][0] == 37
    assert result["last_noncheckpoint"][0] == 45
