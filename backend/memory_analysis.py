"""Compose both circuits in a paired arena and benchmark PostgreSQL state storage."""

from time import perf_counter

import numpy as np
from fastapi import HTTPException
from psycopg import sql

from backend.circuit_model import (
    MotionCircuit,
    STEPS,
    experiment_seeds,
    fit_decoder,
    grating,
    scenes,
    wiring,
)
from backend.circuit_snapshot import load_wiring
from backend.memory import graph, load_protocol, restore
from backend.memory_model import CHECKPOINT_EVERY, FINAL_PROBE_START, LAST, Pathway, fingerprint

ARRIVAL_TOLERANCE = 3.0
ARENA_CONDITIONS = (
    ("combined", True, True),
    ("vision_only", True, False),
    ("memory_only", False, True),
    ("disabled", False, False),
)


def arena(conn, branch):
    info = conn.execute(
        """SELECT m.seed, b.intervention, v.run_id, v.snapshot_id, v.seed
           FROM memory_branch b JOIN memory_run m USING (run_id)
           LEFT JOIN circuit_run v ON v.run_id = m.visual_run_id
           WHERE branch_id = %s""",
        (branch,),
    ).fetchone()
    if not info:
        raise HTTPException(404, "Unknown branch")
    seed, mode, visual_run, snapshot, visual_seed = info
    if visual_run is None:
        raise HTTPException(409, "A saved visual circuit run is required for this arena")
    cues = conn.execute(
        """SELECT t.step, t.left_odor, t.choice, s.draw
           FROM memory_trial t JOIN memory_branch b USING (branch_id)
           JOIN memory_schedule s ON (s.run_id, s.step) = (b.run_id, t.step)
           WHERE t.branch_id = %s AND t.phase = 'final'
           ORDER BY t.step""",
        (branch,),
    ).fetchall()
    if not cues:
        raise HTTPException(409, "Advance the branch to its final probes")
    nodes, edges = load_wiring(conn, snapshot)
    calibration_seed, _, control_seed = experiment_seeds(visual_seed)
    vision = wiring(nodes, edges, "intact", control_seed)
    decoder = fit_decoder(vision, calibration_seed)
    # Every branch and ablation sees the same signed crosswinds and wall textures at the same trial.
    arena_seed = int(np.random.SeedSequence([seed, visual_seed, 1701]).generate_state(1)[0])
    indices = [cue[0] - FINAL_PROBE_START - 1 for cue in cues]
    direction, phase, period, speed, contrast = (
        values[indices] for values in scenes(arena_seed, LAST - FINAL_PROBE_START)
    )
    goal = "B" if mode == "paired_B" else "A"
    if mode == "frozen":
        ancestor = conn.execute(
            """WITH RECURSIVE lineage AS (
                   SELECT parent_id, intervention, 0 AS depth FROM memory_branch WHERE branch_id = %s
                   UNION ALL
                   SELECT b.parent_id, b.intervention, l.depth + 1
                   FROM memory_branch b JOIN lineage l ON b.branch_id = l.parent_id
               )
               SELECT intervention FROM lineage WHERE intervention IN ('paired_A', 'paired_B')
               ORDER BY depth LIMIT 1""",
            (branch,),
        ).fetchone()
        if ancestor and ancestor[0] == "paired_B":
            goal = "B"
    results = []
    for name, use_vision, use_memory in ARENA_CONDITIONS:
        choices = [
            learned if use_memory else (left if draw < 0.5 else {"A": "B", "B": "A"}[left])
            for _, left, learned, draw in cues
        ]
        sides = np.array([-1 if selected == cue[1] else 1 for selected, cue in zip(choices, cues)])
        eye = MotionCircuit(vision, len(cues))
        drift = np.zeros(len(cues))
        steering = np.zeros_like(drift)
        paths = []
        actions = []
        for frame in range(STEPS):
            progress = frame / (STEPS - 1)
            wind = direction * speed * (1 + 0.14 * np.sin(frame / 5 + phase))
            drift += wind - steering
            lane = sides * 10 * max(0, (progress - 0.30) / 0.70)
            paths.append(np.round(lane + 0.30 * drift, 4))
            if use_vision:
                frame_pixels = grating(eye.coordinates, drift, 2 * np.pi / period, phase, contrast)
                features = eye.advance(frame_pixels)
                if frame >= 4:
                    steering = np.clip(0.32 * (features @ decoder), -0.45, 0.45)
            actions.append(np.round(steering, 4))
        trials = []
        for index, ((step, left, _, _), selected) in enumerate(zip(cues, choices)):
            error = abs(float(drift[index]) * 0.30)
            correct_arm = selected == goal
            trials.append(
                {
                    "step": step,
                    "left": left,
                    "choice": selected,
                    "goal": goal,
                    "path": [[float(xs[index]), frame / (STEPS - 1)] for frame, xs in enumerate(paths)],
                    "steering": [float(xs[index]) for xs in actions],
                    "correct_arm": correct_arm,
                    "arrival_error": error,
                    "arrived": correct_arm and error <= ARRIVAL_TOLERANCE,
                }
            )
        results.append(
            {
                "condition": name,
                "trials": trials,
                "arrival_rate": sum(t["arrived"] for t in trials) / len(trials),
                "correct_arm": sum(t["correct_arm"] for t in trials) / len(trials),
                "mean_error": float(np.mean([t["arrival_error"] for t in trials])),
            }
        )
    return {"visual_run": visual_run, "arena_seed": arena_seed, "goal": goal, "conditions": results}


def benchmark(conn, branch):
    info = conn.execute(
        """SELECT run_id, seed, snapshot_id, intervention, fork_step FROM memory_branch
           JOIN memory_run USING (run_id) WHERE branch_id = %s""",
        (branch,),
    ).fetchone()
    if not info:
        raise HTTPException(404, "Unknown branch")
    run_id, seed, snapshot, mode, start = info
    spec = load_protocol(conn, run_id)
    model = Pathway(graph(conn, snapshot), seed, mode)
    saved = dict(
        conn.execute(
            "SELECT step, state_hash FROM memory_trial WHERE branch_id = %s ORDER BY step", (branch,)
        ).fetchall()
    )
    state = restore(conn, branch, start, model)
    # Generate both representations independently of the stored change stream.
    full, checkpoints, changes = [], [], []
    for step, digest in saved.items():
        previous = state
        if step != start:
            state, _ = model.encounter(state, spec, step, mode)
        if fingerprint(state) != digest:
            raise RuntimeError("Independent model replay differs from saved history")
        full.append((step, state.tobytes()))
        if step == start or step % CHECKPOINT_EVERY == 0:
            checkpoints.append(full[-1])
        changes.extend((step, int(i), int(state[i])) for i in np.flatnonzero(previous != state))

    with conn.transaction():
        conn.execute(
            """
            CREATE TEMP TABLE dense(step int PRIMARY KEY, payload bytea) ON COMMIT DROP;
            CREATE TEMP TABLE checkpoints(LIKE pg_temp.dense INCLUDING ALL) ON COMMIT DROP;
            CREATE TEMP TABLE changes(step int, edge int, gain int, PRIMARY KEY(step, edge)) ON COMMIT DROP;
        """
        )
        costs = {}
        for table, rows in [("dense", full), ("checkpoints", checkpoints), ("changes", changes)]:
            before = perf_counter()
            with conn.cursor().copy(
                sql.SQL("COPY pg_temp.{} FROM STDIN").format(sql.Identifier(table))
            ) as copy:
                for row in rows:
                    copy.write_row(row)
            write_ms = 1000 * (perf_counter() - before)
            (size,) = conn.execute(
                "SELECT pg_total_relation_size(%s::regclass)", ("pg_temp." + table,)
            ).fetchone()
            costs[table] = (write_ms, size)
        timings = []
        for step, digest in saved.items():
            before = perf_counter()
            (dense,) = conn.execute("SELECT payload FROM pg_temp.dense WHERE step = %s", (step,)).fetchone()
            dense_ms = (perf_counter() - before) * 1000
            before = perf_counter()
            blob, deltas = conn.execute(
                """WITH last_checkpoint AS (
                       SELECT step, payload FROM pg_temp.checkpoints WHERE step <= %s ORDER BY step DESC LIMIT 1
                   )
                   SELECT payload, ARRAY(
                       SELECT ARRAY[edge, gain] FROM (
                           SELECT DISTINCT ON (edge) edge, gain FROM pg_temp.changes
                           WHERE step > last_checkpoint.step AND step <= %s ORDER BY edge, step DESC
                       ) AS latest
                   ) FROM last_checkpoint""",
                (step, step),
            ).fetchone()
            rebuilt = np.frombuffer(blob, dtype="<u2").copy()
            for index, gain in deltas:
                rebuilt[index] = gain
            sparse_ms = (perf_counter() - before) * 1000
            if rebuilt.tobytes() != dense or fingerprint(rebuilt) != digest:
                raise RuntimeError("Storage benchmark replay mismatch")
            timings.append((step, dense_ms, sparse_ms))
    noncheckpoints = [row for row in timings if row[0] != start and row[0] % CHECKPOINT_EVERY != 0]
    return {
        "verified": len(saved),
        "gains": len(model.ids),
        "changed_rows": len(changes),
        "checkpoint_count": len(checkpoints),
        "dense_bytes": costs["dense"][1],
        "sparse_bytes": costs["checkpoints"][1] + costs["changes"][1],
        "dense_write_ms": costs["dense"][0],
        "sparse_write_ms": costs["checkpoints"][0] + costs["changes"][0],
        "median_read_ms": np.median(np.array(timings)[:, 1:], axis=0).tolist(),
        "first_noncheckpoint": noncheckpoints[0] if noncheckpoints else None,
        "last_noncheckpoint": noncheckpoints[-1] if noncheckpoints else None,
    }
