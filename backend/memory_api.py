"""HTTP endpoints for experiments, exact trial replay and the paired arena."""

from threading import BoundedSemaphore
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from backend.circuit_api import records
from backend.db import connect
from backend.memory import advance, branch_start, graph, load_protocol, restore, simple_baseline, suite
from backend.memory_analysis import arena, benchmark
from backend.memory_model import LAST, VERSION, Pathway

router = APIRouter(prefix="/api/v1/memory", tags=["Memory Lab"])
workers = BoundedSemaphore(2)
Identifier = Annotated[int, Path(gt=0, le=2**63 - 1)]


class Experiment(BaseModel):
    seed: int = Field(default=42, ge=0, le=999999)


class Fork(BaseModel):
    parent: int = Field(gt=0, le=2**63 - 1)
    step: int = Field(ge=0, lt=LAST)
    mode: Literal["paired_A", "paired_B", "unpaired", "rewired", "frozen", "silent_output"]
    until: int = Field(default=LAST, ge=0, le=LAST)


@router.get("/runs")
def runs():
    with connect() as conn:
        return records(
            conn,
            """SELECT run_id, seed, version, snapshot_id FROM memory_run
            ORDER BY run_id DESC LIMIT 100""",
        )


@router.post("/runs")
def create(req: Experiment):
    if not workers.acquire(blocking=False):
        raise HTTPException(429, "Both experiment workers are busy. Try again shortly.")
    try:
        with connect(autocommit=True) as conn:
            return {"run_id": suite(conn, req.seed)}
    finally:
        workers.release()


@router.get("/runs/{run_id}")
def detail(run_id: Identifier):
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = records(
            conn,
            """SELECT r.*, s.digest, s.release_id, s.sources FROM memory_run r
            JOIN memory_snapshot s USING (snapshot_id) WHERE run_id = %s""",
            (run_id,),
        )
        if not rows:
            raise HTTPException(404, "Unknown experiment")
        run = rows[0]
        run["protocol"] = load_protocol(conn, run_id)
        run["baseline"] = simple_baseline(run["protocol"])
        run["branches"] = records(
            conn,
            """SELECT b.*, d.first_changed_choice, d.first_changed_state,
            rev.first_sustained_b, rev.encounters_after_fork,
            (SELECT max(step) FROM memory_trial t WHERE t.branch_id = b.branch_id) AS last_step
            FROM memory_branch b LEFT JOIN memory_divergence d USING (branch_id, parent_id)
            LEFT JOIN memory_reversal rev USING (branch_id) WHERE run_id = %s ORDER BY branch_id""",
            (run_id,),
        )
        run["metrics"] = records(
            conn,
            """SELECT p.* FROM memory_preference p JOIN memory_branch USING (branch_id)
            WHERE run_id = %s ORDER BY branch_id, phase""",
            (run_id,),
        )
        run["paired_probes"] = records(
            conn,
            """SELECT p.* FROM memory_paired_probes p JOIN memory_branch b
            ON b.branch_id = p.branch_id WHERE b.run_id = %s ORDER BY p.branch_id, p.phase""",
            (run_id,),
        )
        run["wiring"] = records(
            conn,
            """SELECT pathway, count(*) AS edges, sum(regional_weight) AS synapses
            FROM memory_edge WHERE snapshot_id = %s GROUP BY pathway ORDER BY pathway""",
            (run["snapshot_id"],),
        )
        run["current_version"] = VERSION
        run["last_step"] = LAST
        return run


@router.post("/runs/{run_id}/fork")
def fork(run_id: Identifier, req: Fork):
    if req.until < req.step:
        raise HTTPException(422, "End trial must follow the fork")
    if not workers.acquire(blocking=False):
        raise HTTPException(429, "Both experiment workers are busy. Try again shortly.")
    try:
        with connect(autocommit=True) as conn:
            branch = branch_start(conn, run_id, req.parent, req.step, req.mode)
            advance(conn, branch, req.until)
            return {"branch_id": branch}
    finally:
        workers.release()


@router.post("/branches/{branch}/advance")
def resume(branch: Identifier, until: int = Query(LAST, ge=0, le=LAST)):
    if not workers.acquire(blocking=False):
        raise HTTPException(429, "Both experiment workers are busy. Try again shortly.")
    try:
        with connect(autocommit=True) as conn:
            advance(conn, branch, until)
            return {"branch_id": branch}
    finally:
        workers.release()


@router.get("/branches/{branch}/trials")
def trials(branch: Identifier):
    with connect() as conn:
        rows = records(conn, "SELECT * FROM memory_trial WHERE branch_id = %s ORDER BY step", (branch,))
        if not rows:
            raise HTTPException(404, "Unknown branch")
        return rows


@router.get("/branches/{branch}/state/{step}")
def trial_state(branch: Identifier, step: Annotated[int, Path(ge=0, le=LAST)]):
    with connect() as conn:
        info = conn.execute(
            """SELECT run_id, snapshot_id, seed, intervention FROM memory_branch JOIN memory_run USING (run_id)
               WHERE branch_id = %s""",
            (branch,),
        ).fetchone()
        if not info:
            raise HTTPException(404, "Unknown branch")
        run_id, snapshot, seed, mode = info
        model = Pathway(graph(conn, snapshot), seed, mode)
        state = restore(conn, branch, step, model)
        trial = records(
            conn, "SELECT * FROM memory_trial WHERE branch_id = %s AND step = %s", (branch, step)
        )[0]
        spec = load_protocol(conn, run_id)
        explanation = None
        if trial["phase"] != "checkpoint":
            before = restore(conn, branch, step - 1, model)
            explanation = model.explain(before, state, spec, step, mode)
        checkpoint, changes = conn.execute(
            """WITH anchor AS (
                   SELECT max(step) AS step FROM memory_checkpoint WHERE branch_id=%s AND step<=%s
               )
               SELECT anchor.step, (SELECT count(*) FROM memory_change
                   WHERE branch_id=%s AND step>anchor.step AND step<=%s) FROM anchor""",
            (branch, step, branch, step),
        ).fetchone()
        ranked = sorted(zip(model.ids, map(int, state)), key=lambda item: (item[1], item[0]))[:8]
        evidence = records(
            conn,
            """SELECT connection_id, source_line, regional_weight, neuropil,
            pre_root, post_root, archive_path, sha256, c.pre_neuron_id AS pre_id, c.post_neuron_id AS post_id
            FROM memory_wiring JOIN connection c USING(connection_id)
            WHERE snapshot_id = %s AND connection_id = ANY(%s)""",
            (snapshot, [i for i, _ in ranked]),
        )
        by_id = {edge["connection_id"]: edge for edge in evidence}
        return {
            "trial": trial,
            "edges": [dict(**by_id[i], gain=value / 10000) for i, value in ranked],
            "mixtures": model.mixtures(state, spec["odors"], mode),
            "changed_from_naive": int((state != 10000).sum()),
            "explanation": explanation,
            "reconstruction": {"checkpoint": checkpoint, "changes": changes},
        }


@router.get("/branches/{branch}/arena")
def navigate(branch: Identifier):
    with connect() as conn:
        return arena(conn, branch)


@router.get("/branches/{branch}/benchmark")
def measure(branch: Identifier):
    with connect(autocommit=True) as conn:
        return benchmark(conn, branch)
