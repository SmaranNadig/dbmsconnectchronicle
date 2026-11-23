"""Design endpoints (/api/v1/design): data flow diagrams, normal forms,
anomalies, file maintenance, the query catalogue and the data dictionary.

Results that take long to compute on the full data (dependency checks over
all rows, the whole query catalogue) are served from the last run saved in
results/design/ by scripts/design_docs.py, with its timestamp; each can also
be re-run from here.
"""
from __future__ import annotations

import json
import threading
import time
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response

from backend.console import QueryError
from backend.db import connect
from backend.design import dfd, dictionary, maintenance, normalisation, queries

router = APIRouter(prefix="/api/v1/design", tags=["Design"])
RESULTS = Path(__file__).resolve().parents[2] / "results" / "design"


def _saved(name: str) -> dict | None:
    path = RESULTS / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@lru_cache(maxsize=1)
def _analysis() -> dict:
    """The analysis depends only on backend/design/relations.py, so it is computed once per process."""
    return normalisation.analyse()


threading.Thread(target=_analysis, daemon=True).start()   # warm the cache at start-up


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@router.get("/dfd")
def data_flow():
    """Data flow diagrams (context, level 1, level 2) and the notation check."""
    spec = dfd.load()
    return {"check": dfd.check(spec), "diagrams": dfd.diagrams(spec),
            "externals": spec["externals"], "stores": spec["stores"]}


@router.get("/dfd/{diagram_id}.svg")
def data_flow_svg(diagram_id: str):
    """One diagram drawn in Gane–Sarson notation."""
    try:
        return Response(dfd.render(diagram_id), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-cache"})
    except StopIteration:
        raise HTTPException(404, "no such diagram") from None


@router.get("/normalisation")
def normal_forms():
    """Keys, dependencies and normal form of every relation, the UNF→BCNF
    walkthrough, and the last verification against the loaded data."""
    saved = _saved("normalisation") or {}
    with connect() as conn:
        walk = normalisation.walkthrough(conn)
    return {"analysis": _analysis(), "walkthrough": walk,
            "verification": saved.get("verification"), "verified_at": saved.get("generated_at"),
            "verification_seconds": saved.get("verification_seconds")}


@router.post("/normalisation/verify")
def verify_normal_forms(scan_data: bool = True):
    """Check every key and dependency against the live database now."""
    t0 = time.perf_counter()
    with connect() as conn:
        out = normalisation.verify(conn, scan_data=scan_data)
    return {"verification": out, "verified_at": _now(), "verification_seconds": round(time.perf_counter() - t0, 1)}


@router.post("/anomalies")
def anomalies():
    """Update, insertion and deletion anomalies of an unnormalised copy of the
    lab connections, next to the normalised tables (rolled back)."""
    try:
        return maintenance.anomalies()
    except QueryError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/files")
def files():
    """Storage file, size, live and dead tuples and last vacuum/analyze of every table."""
    return {"tables": maintenance.file_stats(), "read_at": _now()}


@router.post("/maintenance/{operation}")
def maintain(operation: str, x_review_token: str | None = Header(None)):
    """VACUUM, ANALYZE or REINDEX the lab tables; returns statistics before and after."""
    from backend.workspace import require_review_token

    require_review_token(x_review_token)
    try:
        return maintenance.maintain(operation)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/queries")
def query_catalogue():
    """The query catalogue with the results and plans of its last full run."""
    cat = queries.catalogue()
    saved = _saved("queries") or {}
    last = {r["id"]: r for r in saved.get("results", [])}
    return {"categories": cat["categories"], "queries": [{**q, "last": last.get(q["id"])} for q in cat["queries"]],
            "summary": saved.get("summary"), "ran_at": saved.get("generated_at")}


@router.post("/queries/{query_id}/run")
def run_query(query_id: str):
    """Run one catalogue query now: result, plan and checks."""
    try:
        return queries.run(query_id)
    except KeyError:
        raise HTTPException(404, "no such query") from None


@router.get("/dictionary")
def data_dictionary():
    """Tables, columns, constraints, indexes, triggers, views, functions and
    MongoDB collections, read from the catalogs."""
    with connect() as conn:
        return {**dictionary.build(conn), "read_at": _now()}
