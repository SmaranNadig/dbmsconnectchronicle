"""Run the query catalogue (config/query_catalogue.yaml) on the real data.

Every SQL query runs through the same path as the dashboard console
(backend/console.py: role cc_reader in a READ ONLY transaction, or cc_tryout
for trial writes; always rolled back) or, for target lab, through the lab
(rolled back). Each query is run twice: once for its result, and once under
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) for its plan. MongoDB queries use
the collection's explain() for the winning plan. The `expect` block of each
query is then checked, so a query that errors, returns nothing or stops
using its index shows up as a failed check.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path

import yaml

from backend import console, lab
from backend.console import QueryError

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = ROOT / "config" / "query_catalogue.yaml"
SAMPLE_ROWS = 10


@lru_cache(maxsize=2)
def _load(mtime: float) -> dict:
    return yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))


def catalogue() -> dict:
    return _load(CATALOGUE.stat().st_mtime)


def get(query_id: str) -> dict:
    for q in catalogue()["queries"]:
        if q["id"] == query_id:
            return q
    raise KeyError(query_id)


# ---------------------------------------------------------------- SQL plans
def _walk(node: dict, out: list):
    out.append(node)
    for child in node.get("Plans", []):
        _walk(child, out)


def plan_summary(plan_json) -> dict:
    """Node types, scanned tables and indexes of an EXPLAIN (FORMAT JSON) plan."""
    doc = plan_json[0] if isinstance(plan_json, list) else plan_json
    nodes: list[dict] = []
    _walk(doc["Plan"], nodes)
    scans = []
    for n in nodes:
        t = n["Node Type"]
        if "Scan" in t:
            scans.append({"node": t, "relation": n.get("Relation Name"), "index": n.get("Index Name"),
                          "rows": n.get("Actual Rows"), "loops": n.get("Actual Loops")})
    return {
        "planning_ms": doc.get("Planning Time"), "execution_ms": doc.get("Execution Time"),
        "top_node": doc["Plan"]["Node Type"], "nodes": sorted({n["Node Type"] for n in nodes}),
        "scans": scans,
        "indexes": sorted({s["index"] for s in scans if s["index"]}),
        "seq_scans": sorted({s["relation"] for s in scans if s["node"] == "Seq Scan" and s["relation"]}),
        "shared_hit_blocks": doc["Plan"].get("Shared Hit Blocks"),
        "shared_read_blocks": doc["Plan"].get("Shared Read Blocks"),
    }


def _explain_sql(sql: str, target: str) -> dict:
    stmt = sql.strip().rstrip(";")
    r = console.run_sql(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {stmt}", target=target)
    cell = r["rows"][0][0]
    return plan_summary(json.loads(cell) if isinstance(cell, str) else cell)


def _run_sql(q: dict) -> dict:
    target = q.get("target", "read")
    if target == "lab":
        r = lab.run_script(q["sql"], rollback=True)
        last = next((x for x in reversed(r.get("results", [])) if x.get("columns")), None)
        out = {"columns": (last or {}).get("columns", []), "rows": (last or {}).get("rows", [])[:SAMPLE_ROWS],
               "row_count": len((last or {}).get("rows", [])), "elapsed_ms": r.get("elapsed_ms"),
               "statements": [{"status": x.get("status"), "rows": x.get("rowcount")} for x in r.get("results", [])],
               "executed_as": "cc_lab in the lab schema (transaction rolled back)"}
        return {"result": out, "plan": None}
    r = console.run_sql(q["sql"], target=target)
    result = {"columns": r["columns"], "rows": r["rows"][:SAMPLE_ROWS], "row_count": r["row_count"],
              "truncated": r["truncated"], "elapsed_ms": r["elapsed_ms"], "status": r["status"],
              "executed_as": r["executed_as"]}
    return {"result": result, "plan": _explain_sql(q["sql"], target)}


# ------------------------------------------------------------ MongoDB plans
def _stages(plan: dict, out: list):
    if not isinstance(plan, dict):
        return
    if "stage" in plan:
        out.append({"stage": plan["stage"], "index": plan.get("indexName")})
    for key in ("inputStage", "queryPlan"):
        _stages(plan.get(key), out)
    for child in plan.get("inputStages", []):
        _stages(child, out)


def mongo_plan(explain: dict) -> dict:
    planner = explain.get("queryPlanner")
    if planner is None:          # aggregate: the first stage is the cursor
        for st in explain.get("stages", []):
            if "$cursor" in st:
                planner = st["$cursor"]["queryPlanner"]
                break
    stages: list[dict] = []
    _stages((planner or {}).get("winningPlan", {}), stages)
    stats = explain.get("executionStats") or {}
    return {"stages": [s["stage"] for s in stages], "indexes": sorted({s["index"] for s in stages if s["index"]}),
            "docs_examined": stats.get("totalDocsExamined"), "keys_examined": stats.get("totalKeysExamined")}


def _run_mongo(q: dict) -> dict:
    m = q["mongo"]
    r = console.run_mongo(m["op"], filter=m.get("filter"), projection=m.get("projection"), sort=m.get("sort"),
                          pipeline=m.get("pipeline"), limit=m.get("limit", 10))
    docs = r["documents"]
    result = {"documents": docs[:SAMPLE_ROWS], "row_count": len(docs), "elapsed_ms": r["elapsed_ms"]}
    return {"result": result, "plan": mongo_plan(console.explain_mongo(m["op"], filter=m.get("filter"),
                                                                       pipeline=m.get("pipeline")))}


# ------------------------------------------------------------------ checks
def _check(q: dict, out: dict) -> list[dict]:
    exp = q.get("expect") or {}
    checks = []
    err = out.get("error")
    if "error" in exp:
        checks.append({"check": f"rejected with “{exp['error']}”", "ok": bool(err) and exp["error"] in err})
        return checks
    checks.append({"check": "runs without error", "ok": not err})
    if err:
        return checks
    plan, res = out.get("plan") or {}, out["result"]
    if "rows_at_least" in exp:
        checks.append({"check": f"at least {exp['rows_at_least']} row(s)", "ok": res["row_count"] >= exp["rows_at_least"]})
    for idx in exp.get("index", []):
        checks.append({"check": f"plan uses index {idx}", "ok": idx in plan.get("indexes", [])})
    for rel in exp.get("no_seq_scan", []):
        checks.append({"check": f"no sequential scan of {rel}", "ok": rel not in plan.get("seq_scans", [])})
    if "stage" in exp:
        want = exp["stage"] if isinstance(exp["stage"], list) else [exp["stage"]]
        checks.append({"check": "winning plan uses " + " or ".join(want),
                       "ok": any(s in plan.get("stages", []) for s in want)})
        checks.append({"check": "no collection scan", "ok": "COLLSCAN" not in plan.get("stages", [])})
    return checks


def run(query_id: str) -> dict:
    q = get(query_id)
    t0 = time.perf_counter()
    out: dict = {"id": q["id"], "category": q["category"], "question": q["question"], "why": q.get("why"),
                 "language": "mongo" if "mongo" in q else "sql", "target": q.get("target", "read"),
                 "code": q.get("sql") or q.get("mongo")}
    try:
        out.update(_run_mongo(q) if "mongo" in q else _run_sql(q))
    except QueryError as e:
        out.update(error=str(e), result=None, plan=None)
    out["checks"] = _check(q, out)
    out["ok"] = all(c["ok"] for c in out["checks"])
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


def run_all() -> dict:
    cat = catalogue()
    results = [run(q["id"]) for q in cat["queries"]]
    return {"categories": cat["categories"], "results": results,
            "summary": {"queries": len(results), "passed": sum(r["ok"] for r in results),
                        "sql": sum(r["language"] == "sql" for r in results),
                        "mongo": sum(r["language"] == "mongo" for r in results)}}
