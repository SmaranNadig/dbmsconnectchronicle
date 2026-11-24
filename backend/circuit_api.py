"""Database-backed visual motion experiments and reproducible trial replays."""
from threading import BoundedSemaphore

from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

import numpy as np

from backend.circuit_model import CONDITIONS, VERSION, experiment, rollout, wiring
from backend.circuit_snapshot import OUTPUT, URL, load_wiring
from backend.db import connect

router = APIRouter(prefix="/api/v1/circuit", tags=["Circuit Lab"])
worker = BoundedSemaphore(1)


class RunRequest(BaseModel):
    snapshot_id: int = Field(gt=0)
    seed: int = Field(default=42, ge=0, le=999999)


def records(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


@router.get("/snapshots")
def snapshots():
    with connect() as conn:
        return records(conn, """SELECT s.*, d.code AS dataset, r.version_label,
            (SELECT count(*) FROM circuit_node n WHERE n.snapshot_id=s.snapshot_id) AS neurons,
            (SELECT count(*) FROM circuit_edge e WHERE e.snapshot_id=s.snapshot_id) AS edges
            FROM circuit_snapshot s JOIN release r USING(release_id) JOIN dataset d USING(dataset_id)
            ORDER BY radius, snapshot_id DESC""")


@router.get("/snapshots/{snapshot_id}/evidence")
def evidence(snapshot_id: int):
    with connect() as conn:
        return {"columns": URL, "edges": records(conn, """SELECT a.source_neuron_id AS pre_root,
            b.source_neuron_id AS post_root, u.cell_type AS pre_type, v.cell_type AS post_type,
            e.connection_id, e.weight, f.archive_path, f.sha256, c.source_lines
            FROM circuit_edge e JOIN circuit_node u ON (u.snapshot_id,u.neuron_id)=(e.snapshot_id,e.pre_id)
            JOIN circuit_node v ON (v.snapshot_id,v.neuron_id)=(e.snapshot_id,e.post_id)
            JOIN dataset_neuron a ON a.dataset_neuron_id=e.pre_id JOIN dataset_neuron b ON b.dataset_neuron_id=e.post_id
            JOIN connection c USING(connection_id) JOIN provenance p ON p.provenance_id=c.provenance_id
            JOIN source_file f USING(source_file_id) WHERE e.snapshot_id=%s
            ORDER BY weight DESC, pre_id, post_id LIMIT 12""", (snapshot_id,))}


@router.post("/runs")
def run(req: RunRequest):
    with connect() as conn:
        key = (req.snapshot_id, VERSION, req.seed)
        previous = conn.execute("""SELECT run_id FROM circuit_run WHERE
            (snapshot_id,model_version,seed)=(%s,%s,%s)""", key).fetchone()
        if previous:
            return {"run_id": previous[0]}
        if not worker.acquire(blocking=False):
            raise HTTPException(429, "Another experiment is running. Try again shortly.")
        try:
            nodes, edges = load_wiring(conn, req.snapshot_id)
            if not nodes:
                raise HTTPException(404, "Circuit snapshot unavailable")
            rows = experiment(nodes, edges, req.seed)
            saved = conn.execute("""INSERT INTO circuit_run(snapshot_id,model_version,seed)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING run_id""", key).fetchone()
            if saved:
                with conn.cursor().copy("""COPY circuit_trial(run_id,condition,trial,direction,score,
                        heading_error,open_loop_error,replay) FROM STDIN""") as copy:
                    for *trial, frames in rows:
                        copy.write_row((saved[0], *trial, Jsonb(frames)))
            else:
                saved = conn.execute("""SELECT run_id FROM circuit_run WHERE
                    (snapshot_id,model_version,seed)=(%s,%s,%s)""", key).fetchone()
            return {"run_id": saved[0]}
        finally:
            worker.release()


@router.get("/runs")
def history():
    with connect() as conn:
        return records(conn, "SELECT * FROM circuit_run ORDER BY run_id DESC LIMIT 50")


@router.get("/runs/{run_id}")
def result(run_id: int):
    with connect() as conn:
        runs = records(conn, """SELECT r.*,s.digest,s.radius,s.release_id,s.column_sha256
            FROM circuit_run r JOIN circuit_snapshot s USING(snapshot_id) WHERE run_id=%s""", (run_id,))
        if not runs:
            raise HTTPException(404, "Unknown experiment")
        return {**runs[0], "metrics": records(conn, """SELECT * FROM circuit_results
            WHERE run_id=%s ORDER BY condition""", (run_id,)), "paired_diff": records(conn,
            "SELECT * FROM circuit_paired_diff WHERE run_id=%s ORDER BY condition", (run_id,)), "outputs": OUTPUT}


@router.get("/snapshots/{snapshot_id}/polarity")
def polarity(snapshot_id: int):
    with connect() as conn:
        nodes, edges = load_wiring(conn, snapshot_id)
        if not nodes:
            raise HTTPException(404, "Circuit snapshot unavailable")
    graph = wiring(nodes, edges, "intact", 0)
    span = np.ptp(graph[2])
    scene = (np.array([-1, 1]), np.zeros(2), np.ones(2), np.full(2, span/32), np.full(2, .4))
    probes = {}
    for label, sign in [("bright", 1), ("dark", -1)]:
        activity = rollout(graph, scene, edge_polarity=sign)[0].mean(axis=0)
        probes[label] = {"T4": float(activity[:4].mean()), "T5": float(activity[4:].mean())}
    return probes


@router.get("/runs/{run_id}/replay")
def replay(run_id: int, condition: str = "intact", trial: int = Query(0, ge=0, le=23)):
    if condition not in CONDITIONS:
        raise HTTPException(422, "Unknown condition")
    with connect() as conn:
        rows = records(conn, "SELECT * FROM circuit_trial WHERE run_id=%s AND condition=%s AND trial=%s",
                       (run_id, condition, trial))
        if not rows:
            raise HTTPException(404, "Trial unavailable")
        return rows[0]

