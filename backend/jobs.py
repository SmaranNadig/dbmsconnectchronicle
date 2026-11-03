"""Durable job status with two local workers. Run one API process per database."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from backend.workspace import db_connection, require_review_token

router = APIRouter(prefix="/api/v1/workspace", tags=["Jobs"])
_executor = None
log = logging.getLogger("cc.jobs")


def start_workers():
    global _executor
    if _executor is not None:
        return
    with db_connection() as conn:
        conn.execute(
            "UPDATE workspace_job SET status='failed',message='Worker restarted; submit the operation again',updated_at=now() WHERE status='running'"
        )
        pending = conn.execute(
            "SELECT job_id FROM workspace_job WHERE status='queued' ORDER BY created_at"
        ).fetchall()
    _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cc-job")
    for row in pending:
        _executor.submit(_run, str(row["job_id"]))


def stop_workers():
    global _executor
    if _executor:
        _executor.shutdown(wait=True, cancel_futures=False)
        _executor = None


def progress(job_id, percent, message):
    with db_connection() as conn:
        conn.execute(
            "UPDATE workspace_job SET progress=%s,message=%s,updated_at=now() WHERE job_id=%s",
            (percent, message, job_id),
        )


def _run(job_id):
    with db_connection() as conn:
        job = conn.execute(
            "UPDATE workspace_job SET status='running',progress=5,message='Starting',updated_at=now() WHERE job_id=%s AND status='queued' RETURNING *",
            (job_id,),
        ).fetchone()
    if not job:
        return
    try:
        request, kind = job["request"], job["kind"]
        if kind == "diff":
            from backend.main import create_diff, DiffRequest

            progress(job_id, 15, "Comparing temporal states in PostgreSQL")
            with db_connection() as conn:
                result = create_diff(DiffRequest(**request), conn)
        elif kind == "anomalies":
            from backend.analysis import score_diff

            result = score_diff(request["diff_id"], lambda p, m: progress(job_id, p, m))
        elif kind == "matching":
            from backend.analysis import score_mappings

            result = score_mappings(request.get("limit", 5000))
        else:
            from backend.distributed.partition import compute
            from backend.distributed.deploy import deploy

            progress(job_id, 15, "Computing partition placement")
            compute(request["strategy"])
            progress(
                job_id, 40, "Building staged shard copies and verifying full content"
            )
            result = deploy(request["strategy"])
        with db_connection() as conn:
            conn.execute(
                "UPDATE workspace_job SET status='succeeded',progress=100,message='Complete',result=%s,updated_at=now() WHERE job_id=%s",
                (json.dumps(result, default=str), job_id),
            )
    except Exception as exc:
        log.exception("Workspace job failed: %s", job_id)
        with db_connection() as conn:
            conn.execute(
                "UPDATE workspace_job SET status='failed',message=%s,updated_at=now() WHERE job_id=%s",
                (str(exc).splitlines()[0][:500], job_id),
            )


class JobRequest(BaseModel):
    kind: Literal["diff", "anomalies", "matching", "deploy"]
    request: dict = Field(default_factory=dict)


@router.post("/jobs", status_code=202)
def submit(body: JobRequest, x_review_token: str | None = Header(None)):
    from backend.workspace import instant

    if body.kind == "diff":
        from backend.main import DiffRequest
        from pydantic import ValidationError

        try:
            parsed = DiffRequest(**body.request)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        instant(parsed.as_of)
        if parsed.release_a == parsed.release_b:
            raise HTTPException(422, "select two different releases")
        body.request = parsed.model_dump(mode="json")
    elif body.kind == "anomalies":
        if (
            not isinstance(body.request.get("diff_id"), int)
            or body.request["diff_id"] <= 0
        ):
            raise HTTPException(422, "a positive diff_id is required")
        body.request = {"diff_id": body.request["diff_id"]}
    elif body.kind == "deploy":
        require_review_token(x_review_token)
        if body.request.get("strategy") not in ("hash", "anatomical", "community"):
            raise HTTPException(422, "unknown strategy")
        body.request = {"strategy": body.request["strategy"]}
    else:
        require_review_token(x_review_token)
        limit = body.request.get("limit", 5000)
        if not isinstance(limit, int) or not 1 <= limit <= 20000:
            raise HTTPException(422, "matching limit must be between 1 and 20000")
        body.request = {"limit": limit}
    request_json = json.dumps(body.request)
    with db_connection() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (body.kind + request_json,),
        )
        old = conn.execute(
            "SELECT * FROM workspace_job WHERE kind=%s AND request=%s::jsonb AND status IN ('queued','running') ORDER BY created_at LIMIT 1",
            (body.kind, request_json),
        ).fetchone()
        if old:
            return old
        job_id = str(uuid.uuid4())
        job = conn.execute(
            "INSERT INTO workspace_job(job_id,kind,request) VALUES (%s,%s,%s) RETURNING *",
            (job_id, body.kind, request_json),
        ).fetchone()
    if _executor is None:
        start_workers()
    _executor.submit(_run, job_id)
    return job


@router.get("/jobs")
def list_jobs():
    with db_connection() as conn:
        return conn.execute(
            "SELECT * FROM workspace_job ORDER BY created_at DESC LIMIT 30"
        ).fetchall()


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID):
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM workspace_job WHERE job_id=%s", (job_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "job not found")
        return row


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID):
    with db_connection() as conn:
        row = conn.execute(
            "UPDATE workspace_job SET status='cancelled',message='Cancelled before execution',updated_at=now() WHERE job_id=%s AND status='queued' RETURNING *",
            (job_id,),
        ).fetchone()
        if not row:
            raise HTTPException(
                409,
                "Only queued jobs can be cancelled. Running database transactions finish atomically.",
            )
        return row
