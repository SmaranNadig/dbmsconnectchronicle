"""Transactional learning history and replay. A trial is the commit boundary."""

import numpy as np
from fastapi import HTTPException
from psycopg.rows import dict_row

from backend.circuit_model import VERSION as VISION_VERSION
from backend.memory_model import (
    CHECKPOINT_EVERY,
    FINAL_PROBE_START,
    LAST,
    RETRAINING_START,
    VERSION,
    Pathway,
    fingerprint,
    protocol,
)


def graph(conn, snapshot):
    return conn.execute(
        """SELECT e.connection_id, pathway, channel, c.pre_neuron_id, c.post_neuron_id, regional_weight
           FROM memory_edge e JOIN connection c USING (connection_id)
           WHERE snapshot_id = %s ORDER BY connection_id""",
        (snapshot,),
    ).fetchall()


def load_protocol(conn, run_id):
    (channels,) = conn.execute("SELECT channels FROM memory_run WHERE run_id = %s", (run_id,)).fetchone()
    odors = [
        row[0]
        for row in conn.execute(
            "SELECT activity FROM memory_stimulus WHERE run_id = %s ORDER BY odor", (run_id,)
        )
    ]
    with conn.cursor(row_factory=dict_row) as cursor:
        events = cursor.execute(
            """SELECT phase, presented, left_position AS "left", unpaired, draw, noise
               FROM memory_schedule WHERE run_id = %s ORDER BY step""",
            (run_id,),
        ).fetchall()
    return {"channels": channels, "odors": odors, "events": events}


def restore(conn, branch, step, model):
    trial = conn.execute(
        "SELECT state_hash FROM memory_trial WHERE branch_id = %s AND step = %s", (branch, step)
    ).fetchone()
    if not trial:
        raise HTTPException(404, "Trial has not been committed on this branch")
    values = conn.execute(
        "SELECT connection_id, gain FROM memory_state_at(%s, %s) ORDER BY connection_id", (branch, step)
    ).fetchall()
    state = np.zeros_like(model.initial)
    if len(values) != len(state):
        raise RuntimeError("Checkpoint size differs from the frozen circuit")
    for edge, gain in values:
        state[model.index[edge]] = gain
    if fingerprint(state) != trial[0]:
        raise RuntimeError("Reconstructed state differs from the committed trial digest")
    return state


def create_run(conn, seed):
    with conn.transaction():
        snapshot = conn.execute(
            "SELECT snapshot_id, release_id FROM memory_snapshot ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        if not snapshot:
            raise HTTPException(409, "Load the FlyWire memory snapshot first")
        snapshot_id, release_id = snapshot
        key = (snapshot_id, seed, VERSION)
        existing = conn.execute(
            "SELECT run_id FROM memory_run WHERE (snapshot_id, seed, version) = (%s, %s, %s)", key
        ).fetchone()
        if existing:
            return existing[0]
        edges = graph(conn, snapshot_id)
        spec = protocol(sorted({edge[2] for edge in edges if edge[1] == "input"}), seed)
        vision = conn.execute(
            """SELECT r.run_id FROM circuit_run r JOIN circuit_snapshot s USING (snapshot_id)
               WHERE s.release_id = %s AND s.radius = 4 AND r.model_version = %s
               ORDER BY (r.seed = 42) DESC, r.run_id LIMIT 1""",
            (release_id, VISION_VERSION),
        ).fetchone()
        row = conn.execute(
            """INSERT INTO memory_run(snapshot_id, seed, version, visual_run_id, channels)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING run_id""",
            (*key, vision[0] if vision else None, spec["channels"]),
        ).fetchone()
        if not row:
            return conn.execute(
                "SELECT run_id FROM memory_run WHERE (snapshot_id, seed, version) = (%s, %s, %s)", key
            ).fetchone()[0]
        run_id = row[0]
        conn.execute(
            "INSERT INTO memory_stimulus VALUES (%s, 'A', %s), (%s, 'B', %s)",
            (run_id, spec["odors"][0], run_id, spec["odors"][1]),
        )
        with conn.cursor().copy(
            "COPY memory_schedule(run_id, step, phase, presented, left_position, unpaired, draw, noise) FROM STDIN"
        ) as copy:
            for step, event in enumerate(spec["events"], 1):
                copy.write_row(
                    (
                        run_id,
                        step,
                        event["phase"],
                        event["presented"],
                        event["left"],
                        event["unpaired"],
                        event["draw"],
                        event["noise"],
                    )
                )
        return run_id


def branch_start(conn, run_id, parent, fork, mode):
    with conn.transaction():
        run = conn.execute(
            "SELECT snapshot_id, seed FROM memory_run WHERE run_id = %s FOR UPDATE", (run_id,)
        ).fetchone()
        if not run:
            raise HTTPException(404, "Unknown memory experiment")
        existing = conn.execute(
            """SELECT branch_id FROM memory_branch WHERE run_id = %s AND parent_id IS NOT DISTINCT FROM %s
               AND fork_step = %s AND intervention = %s""",
            (run_id, parent, fork, mode),
        ).fetchone()
        if existing:
            return existing[0]
        model = Pathway(graph(conn, run[0]), run[1], mode)
        state = model.initial
        if parent is not None:
            if not conn.execute(
                "SELECT 1 FROM memory_branch WHERE run_id = %s AND branch_id = %s", (run_id, parent)
            ).fetchone():
                raise HTTPException(404, "Parent belongs to another experiment")
            state = restore(conn, parent, fork, model)
        branch = conn.execute(
            """INSERT INTO memory_branch(run_id, parent_id, fork_step, intervention)
               VALUES (%s, %s, %s, %s) RETURNING branch_id""",
            (run_id, parent, fork, mode),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO memory_trial(branch_id, step, phase, reward, state_hash)
               VALUES (%s, %s, 'checkpoint', false, %s)""",
            (branch, fork, fingerprint(state)),
        )
        conn.execute("INSERT INTO memory_checkpoint VALUES (%s, %s, %s)", (branch, fork, state.tobytes()))
        return branch


def advance(conn, branch, until=LAST):
    info = conn.execute(
        """SELECT run_id, snapshot_id, seed, intervention FROM memory_branch
           JOIN memory_run USING (run_id) WHERE branch_id = %s""",
        (branch,),
    ).fetchone()
    if not info:
        raise HTTPException(404, "Unknown branch")
    run_id, snapshot, seed, mode = info
    spec = load_protocol(conn, run_id)
    model = Pathway(graph(conn, snapshot), seed, mode)
    cached_step, state = None, None
    while True:
        with conn.transaction():
            conn.execute("SELECT 1 FROM memory_branch WHERE branch_id = %s FOR UPDATE", (branch,))
            (last,) = conn.execute(
                "SELECT max(step) FROM memory_trial WHERE branch_id = %s", (branch,)
            ).fetchone()
            if last >= until:
                return
            if cached_step != last:
                state = restore(conn, branch, last, model)
            step = last + 1
            new, event = model.encounter(state, spec, step, mode)
            conn.execute(
                """INSERT INTO memory_trial(branch_id, step, phase, presented, reward, left_odor, choice, p_a, state_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (branch, step, *event, fingerprint(new)),
            )
            changed = np.flatnonzero(state != new)
            if len(changed):
                with conn.cursor().copy("COPY memory_change FROM STDIN") as copy:
                    for i in changed:
                        copy.write_row((branch, step, model.ids[i], int(new[i])))
            if step % CHECKPOINT_EVERY == 0:
                conn.execute(
                    "INSERT INTO memory_checkpoint VALUES (%s, %s, %s)", (branch, step, new.tobytes())
                )
            cached_step, state = step, new


def suite(conn, seed):
    run_id = create_run(conn, seed)
    root = branch_start(conn, run_id, None, 0, "paired_A")
    advance(conn, root)
    for mode, fork in (
        ("paired_B", RETRAINING_START),
        ("frozen", 0),
        ("unpaired", 0),
        ("rewired", 0),
        ("frozen", RETRAINING_START),
    ):
        advance(conn, branch_start(conn, run_id, root, fork, mode))
    silenced = branch_start(conn, run_id, root, FINAL_PROBE_START, "silent_output")
    advance(conn, silenced)
    advance(conn, branch_start(conn, run_id, silenced, FINAL_PROBE_START, "frozen"))
    return run_id


def simple_baseline(spec):
    values = np.zeros(2)
    templates = np.array(spec["odors"])
    probes = {}
    for event in spec["events"]:
        phase = event["phase"]
        training = phase in ("conditioning", "retraining")
        perturb = np.random.default_rng(event["noise"]).normal(
            0, 0.09 if training else 0.18, templates.shape
        )
        sensed = np.clip(templates + perturb, 0, 1)
        identity = np.argmin(((sensed[:, None, :] - templates[None, :, :]) ** 2).sum(axis=2), axis=1)
        probability = 1 / (1 + np.exp(-6 * (values[identity[0]] - values[identity[1]])))
        probes.setdefault(phase, []).append(float(probability))
        if training:
            cue = event["presented"]
            classified = identity[cue]
            values[classified] += 0.3 * (int(cue == 0) - values[classified])
    return {phase: float(np.mean(outcomes)) for phase, outcomes in probes.items()}
