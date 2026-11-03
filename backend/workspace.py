"""Release-aware reads and bounded visual exploration of the authoritative store."""

from datetime import datetime, timezone
import csv
import io
import json
import hmac
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from psycopg.rows import dict_row
from backend.db import connect
from backend.revisions import source_revision
from backend.config import get_settings

router = APIRouter(prefix="/api/v1/workspace", tags=["Workspace"])


def db_connection():
    conn = connect()
    conn.row_factory = dict_row
    return conn


def instant(value=None):
    now = datetime.now(timezone.utc)
    if value is None:
        return now
    if value.tzinfo is None:
        raise HTTPException(422, "as_of must include a timezone (for example +00:00)")
    if value > now:
        raise HTTPException(422, "as_of cannot be in the future")
    return value


def release_state(conn, dataset, label=None):
    row = conn.execute(
        """SELECT r.*, d.code AS dataset FROM release r JOIN dataset d USING(dataset_id)
        WHERE d.code=%s AND (%s::text IS NULL OR r.version_label=%s)
          AND r.ingestion_status='loaded' ORDER BY release_order DESC LIMIT 1""",
        (dataset, label, label),
    ).fetchone()
    if not row:
        raise HTTPException(404, "loaded release not found")
    return row


def require_review_token(token=None):
    expected = get_settings().cc_review_token
    if expected and not hmac.compare_digest(token or "", expected):
        raise HTTPException(401, "A valid reviewer token is required")


def search_neurons(
    conn,
    dataset,
    source_id=None,
    cell_type=None,
    release=None,
    as_of=None,
    region=None,
    limit=50,
    offset=0,
):
    rel, ts = release_state(conn, dataset, release), instant(as_of)
    return conn.execute(
        """SELECT dn.dataset_neuron_id, dn.source_neuron_id,
        a.annotation_value AS cell_type, %(label)s::text AS release
        FROM dataset_neuron dn
        JOIN neuron_version nv ON nv.dataset_neuron_id=dn.dataset_neuron_id
             AND nv.valid_rel @> %(ord)s::int AND nv.tx @> %(ts)s
        LEFT JOIN annotation a ON a.dataset_neuron_id=dn.dataset_neuron_id AND a.annotation_key='cell_type'
             AND a.valid_rel @> %(ord)s::int AND a.tx @> %(ts)s
        WHERE dn.dataset_id=%(ds)s AND (%(sid)s::text IS NULL OR dn.source_neuron_id=%(sid)s)
          AND (%(ct)s::text IS NULL OR a.annotation_value=%(ct)s)
          AND (%(region)s::text IS NULL OR EXISTS (SELECT FROM neuron_region_synapses s
               JOIN brain_region_label l USING(brain_region_id)
               WHERE s.dataset_neuron_id=dn.dataset_neuron_id AND s.release_id=%(rid)s AND l.code=%(region)s))
        ORDER BY dn.dataset_neuron_id LIMIT %(limit)s OFFSET %(offset)s""",
        dict(
            ds=rel["dataset_id"],
            ord=rel["release_order"],
            rid=rel["release_id"],
            label=rel["version_label"],
            sid=source_id or None,
            ct=cell_type or None,
            ts=ts,
            region=region or None,
            limit=limit,
            offset=offset,
        ),
    ).fetchall()


@router.get("/context")
def context():
    with db_connection() as conn:
        releases = conn.execute("""SELECT d.code AS dataset, d.name, r.release_id, r.version_label,
            r.release_order, r.loaded_at FROM release r JOIN dataset d USING(dataset_id)
            WHERE ingestion_status='loaded' ORDER BY d.code,r.release_order""").fetchall()
        regions = conn.execute("""SELECT DISTINCT s.release_id, l.code, l.name FROM neuron_region_synapses s
            JOIN brain_region_label l USING(brain_region_id) ORDER BY s.release_id,l.code""").fetchall()
        return {
            "releases": releases,
            "regions": regions,
            "data_revision": source_revision(conn),
            "explanations": "Evidence templates and model features; no external API",
            "review_token_required": bool(get_settings().cc_review_token),
        }


@router.get("/examples")
def examples():
    """Adapt teaching examples to real identifiers from the connected database."""
    from backend.config import PROJECT_ROOT
    from backend.metadata import mongo_db, COLLECTION

    data = json.loads(
        (PROJECT_ROOT / "frontend/public/examples.json").read_text(encoding="utf-8")
    )
    with db_connection() as conn:
        ds = conn.execute("""SELECT d.code,count(*) AS n FROM dataset d JOIN release r USING(dataset_id)
            WHERE r.ingestion_status='loaded' GROUP BY d.code ORDER BY n DESC,d.code LIMIT 1""").fetchone()
        if not ds:
            return data
        rs = conn.execute(
            """SELECT version_label FROM release JOIN dataset USING(dataset_id)
            WHERE code=%s AND ingestion_status='loaded' ORDER BY release_order""",
            (ds["code"],),
        ).fetchall()
        neuron = conn.execute(
            """SELECT dataset_neuron_id,source_neuron_id FROM dataset_neuron JOIN dataset USING(dataset_id)
            WHERE code=%s ORDER BY dataset_neuron_id LIMIT 1""",
            (ds["code"],),
        ).fetchone()
    if neuron:
        for example in data["sql"]:
            query = (
                example["sql"]
                .replace("hemibrain:", ds["code"] + ":")
                .replace("'hemibrain'", f"'{ds['code']}'")
            )
            query = query.replace(
                "1688868386", neuron["source_neuron_id"].replace("'", "''")
            )
            for old, new in [
                ("v1.0", rs[0]["version_label"]),
                ("v1.1", rs[min(1, len(rs) - 1)]["version_label"]),
                ("v1.2", rs[-1]["version_label"]),
            ]:
                query = query.replace(old, new)
                example["title"] = example["title"].replace(old, new)
            example["sql"] = query.replace(
                "HAVING count(*) >= 50", "HAVING count(*) >= 1"
            )
            example["title"] = example["title"].replace("hemibrain", "selected dataset")
    try:
        sample = mongo_db()[COLLECTION].find_one({}, sort=[("_id", 1)])
        alternate = (
            mongo_db()[COLLECTION].find_one(
                {"dataset": {"$ne": sample["dataset"]}}, sort=[("_id", 1)]
            )
            if sample
            else None
        )
    except Exception:
        sample = None
    if sample:
        for example in data["mongo"]:
            if example["id"] == "m-one":
                example["filter"] = {"_id": sample["_id"]}
            if example["id"] == "m-flywire":
                other = alternate or sample
                example["filter"] = {"_id": other["_id"]}
                example["title"] = f"Source-specific document: {other['dataset']}"
            if example["id"] == "m-project":
                example["filter"] = {"dataset": sample["dataset"]}
                example["projection"] = {
                    "dataset": 1,
                    "source_neuron_id": 1,
                    **{
                        f"releases.{rid}.release": 1
                        for rid in sample.get("releases", {})
                    },
                }
            if example["id"] == "m-type":
                example["title"] = (
                    "Source type counts by release (where the type field exists)"
                )
                example["pipeline"][0] = {"$match": {"dataset": sample["dataset"]}}
                example["pipeline"][4] = {
                    "$match": {"r.v.records.fields.type": {"$exists": True}}
                }
    return data


@router.get("/neurons/{neuron_id}/graph")
def graph(
    neuron_id: int,
    release: str,
    as_of: datetime | None = None,
    hops: int = Query(1, ge=1, le=2),
    min_weight: int = Query(0, ge=0),
    max_nodes: int = Query(40, ge=2, le=80),
    max_edges: int = Query(80, ge=1, le=160),
    direction: Literal["both", "in", "out"] = "both",
):
    with db_connection() as conn:
        root = conn.execute(
            """SELECT dn.*,d.code AS dataset FROM dataset_neuron dn
            JOIN dataset d USING(dataset_id) WHERE dataset_neuron_id=%s""",
            (neuron_id,),
        ).fetchone()
        if not root:
            raise HTTPException(404, "neuron not found")
        rel, ts = release_state(conn, root["dataset"], release), instant(as_of)
        ids, frontier, edges, truncated = {neuron_id}, [neuron_id], {}, False
        for _ in range(hops):
            if not frontier:
                break
            if len(edges) >= max_edges:
                truncated = True
                break
            predicate = {
                "out": "c.pre_neuron_id=ANY(%(ids)s)",
                "in": "c.post_neuron_id=ANY(%(ids)s)",
                "both": "(c.pre_neuron_id=ANY(%(ids)s) OR c.post_neuron_id=ANY(%(ids)s))",
            }[direction]
            rows = conn.execute(
                f"""SELECT c.connection_id,c.pre_neuron_id AS source,c.post_neuron_id AS target,
                c.synapse_count AS weight FROM connection c WHERE c.dataset_id=%(ds)s
                AND {predicate} AND c.valid_rel @> %(ord)s::int AND c.tx @> %(ts)s
                AND c.synapse_count >= %(w)s ORDER BY c.synapse_count DESC,c.connection_id LIMIT %(lim)s""",
                dict(
                    ids=frontier,
                    ds=rel["dataset_id"],
                    ord=rel["release_order"],
                    ts=ts,
                    w=min_weight,
                    lim=max_edges + 1,
                ),
            ).fetchall()
            next_ids = set()
            truncated |= len(rows) > max_edges
            for row in rows:
                new = {row["source"], row["target"]} - ids
                if len(ids | new) > max_nodes or (
                    len(edges) >= max_edges and row["connection_id"] not in edges
                ):
                    truncated = True
                    continue
                ids |= new
                next_ids |= new
                edges[row["connection_id"]] = row
            frontier = sorted(next_ids)
        nodes = conn.execute(
            """SELECT dn.dataset_neuron_id AS id,dn.source_neuron_id AS label,
            s.present,s.annotations FROM dataset_neuron dn,
            LATERAL neuron_at_release(dn.dataset_neuron_id,%s,%s) s
            WHERE dn.dataset_neuron_id=ANY(%s) ORDER BY dn.dataset_neuron_id""",
            (rel["release_id"], ts, sorted(ids)),
        ).fetchall()
        return {
            "root": neuron_id,
            "release": release,
            "as_of": ts,
            "nodes": nodes,
            "edges": list(edges.values()),
            "truncated": truncated,
            "limits": {"nodes": max_nodes, "edges": max_edges, "hops": hops},
            "region_note": "Region aggregates are release-derived, not transaction-time versioned.",
        }


def explain_event(event):
    subject = event["neuron_source_id"]
    if event["entity_type"] == "connection":
        old, new = event["old_weight"] or 0, event["new_weight"] or 0
        return f"Connection {subject} → {event['post_source_id']}: {old} → {new} synapses ({new - old:+d}); {event['change_type']} between {event['release_a']} and {event['release_b']}. This describes a reconstruction record change."
    if event["entity_type"] == "annotation":
        return f"Neuron {subject}: {event['annotation_key']} changed from {event['old_value'] or 'absent'} to {event['new_value'] or 'absent'}."
    return f"Source neuron {subject} was {event['change_type']} in the release records. Identifier churn can account for apparent additions or removals."


def event_page(
    conn, diff_id, entity_type=None, change_type=None, q=None, limit=50, offset=0
):
    params = dict(
        run=diff_id,
        entity=entity_type or None,
        change=change_type or None,
        q=q or None,
        limit=limit,
        offset=offset,
    )
    where = """e.diff_run_id=%(run)s AND (%(entity)s::text IS NULL OR e.entity_type=%(entity)s)
        AND (%(change)s::text IS NULL OR e.change_type=%(change)s)
        AND (%(q)s::text IS NULL OR e.neuron_source_id=%(q)s OR e.post_source_id=%(q)s)"""
    total = conn.execute(
        f"SELECT count(*) AS n FROM diff_event_detail e WHERE {where}", params
    ).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT e.*, de.dataset_neuron_id,de.post_neuron_id FROM diff_event_detail e
        JOIN diff_event de USING(diff_event_id) WHERE {where}
        ORDER BY abs(e.weight_delta) DESC,e.diff_event_id LIMIT %(limit)s OFFSET %(offset)s""",
        params,
    ).fetchall()
    for row in rows:
        row["explanation"] = explain_event(row)
    return {"total": total, "rows": rows, "limit": limit, "offset": offset}


@router.get("/diffs/{diff_id}/events")
def events(
    diff_id: int,
    entity_type: Literal["neuron", "connection", "annotation"] | None = None,
    change_type: Literal["added", "removed", "modified"] | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    with db_connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM diff_run WHERE diff_run_id=%s", (diff_id,)
        ).fetchone():
            raise HTTPException(404, "diff not found")
        return event_page(conn, diff_id, entity_type, change_type, q, limit, offset)


@router.get("/events/{event_id}")
def event_detail(event_id: int):
    with db_connection() as conn:
        row = conn.execute(
            """SELECT e.*,de.dataset_neuron_id,de.post_neuron_id FROM diff_event_detail e
            JOIN diff_event de USING(diff_event_id) WHERE diff_event_id=%s""",
            (event_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "event not found")
        table = {
            "neuron": "neuron_version",
            "connection": "connection",
            "annotation": "annotation",
        }[row["entity_type"]]
        row["provenance"] = {
            side: conn.execute(
                "SELECT trace_provenance(%s,%s) AS trace", (table, row[key])
            ).fetchone()["trace"]
            for side, key in [("before", "record_a_id"), ("after", "record_b_id")]
            if row[key] is not None
        }
        row["explanation"] = explain_event(row)
        return row


@router.get("/diffs/{diff_id}/export")
def export(
    diff_id: int,
    format: Literal["csv", "json"] = "csv",
    entity_type: str | None = None,
    change_type: str | None = None,
    q: str | None = None,
):
    with db_connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM diff_run WHERE diff_run_id=%s", (diff_id,)
        ).fetchone():
            raise HTTPException(404, "diff not found")
        page = event_page(conn, diff_id, entity_type, change_type, q, 10000, 0)
    page["truncated"] = page["total"] > len(page["rows"])
    if format == "json":
        return page
    output = io.StringIO()
    columns = [
        "diff_event_id",
        "entity_type",
        "change_type",
        "dataset",
        "release_a",
        "release_b",
        "neuron_source_id",
        "post_source_id",
        "old_weight",
        "new_weight",
        "weight_delta",
        "annotation_key",
        "old_value",
        "new_value",
        "explanation",
    ]
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in page["rows"]:
        # Avoid spreadsheet formula execution when an annotation begins with an operator.
        writer.writerow(
            {
                k: "'" + v
                if isinstance(v, str)
                and v.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
                else v
                for k, v in row.items()
            }
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="connectomediff-{diff_id}.csv"',
            "X-Export-Truncated": str(page["truncated"]).lower(),
            "X-Export-Total": str(page["total"]),
        },
    )
