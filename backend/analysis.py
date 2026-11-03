"""Reproducible anomaly features and evidence-based correspondence ranking.

Explanations describe observed features. They do not use an LLM or claim causation.
"""

import hashlib
import json
import math
import uuid
from collections import defaultdict

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from fastapi import HTTPException, Query
from backend.config import PROJECT_ROOT, get_settings
from backend.workspace import router, db_connection

FEATURES = [
    "log_old_weight",
    "log_new_weight",
    "log_absolute_delta",
    "relative_delta",
    "added",
    "removed",
]
FEATURE_SQL = """SELECT e.*,n.source_neuron_id,p.source_neuron_id AS partner_source_id FROM diff_event e
    JOIN dataset_neuron n USING(dataset_neuron_id)
    JOIN dataset_neuron p ON p.dataset_neuron_id=e.post_neuron_id
    WHERE e.diff_run_id=%s AND e.entity_type='connection' ORDER BY e.diff_event_id"""


def vector(row):
    old, new = row["old_weight"] or 0, row["new_weight"] or 0
    delta = abs(new - old)
    return [
        math.log1p(old),
        math.log1p(new),
        math.log1p(delta),
        delta / max(old, new, 1),
        float(row["change_type"] == "added"),
        float(row["change_type"] == "removed"),
    ]


def baseline(row):
    old, new = row["old_weight"] or 0, row["new_weight"] or 0
    return abs(new - old) >= 5 and abs(new - old) / max(old, new, 1) >= 0.5


def score_diff(diff_id, progress=lambda p, m: None):
    rng, sample, count = np.random.default_rng(42), [], 0
    with db_connection() as conn:
        run = conn.execute(
            "SELECT * FROM diff_run WHERE diff_run_id=%s AND status='succeeded'",
            (diff_id,),
        ).fetchone()
        if not run:
            raise ValueError("Completed diff not found")
        with conn.cursor(name="training_features") as cursor:
            cursor.execute(FEATURE_SQL, (diff_id,))
            for row in cursor:
                count += 1
                values = vector(row)
                if len(sample) < 20000:
                    sample.append(values)
                else:
                    i = int(rng.integers(count))
                    if i < len(sample):
                        sample[i] = values
    if count == 0:
        raise ValueError("This comparison has no changed connections to score")
    progress(
        30, f"Extracted {count:,} connection changes; training on at most 20,000 rows"
    )
    model = None
    if len(sample) >= 8:
        model = IsolationForest(
            n_estimators=100,
            max_samples=min(256, len(sample)),
            contamination="auto",
            random_state=42,
            n_jobs=1,
        )
        model.fit(np.asarray(sample, dtype=float))
    version = "features-v1-" + uuid.uuid4().hex[:12]
    artifact, artifact_hash = None, None
    if model is not None:
        path = PROJECT_ROOT / "results" / "models" / f"{version}.joblib"
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, path)
        artifact, artifact_hash = (
            str(path.relative_to(PROJECT_ROOT)),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    parameters = dict(
        seed=42,
        training_rows=len(sample),
        scored_rows=count,
        feature_version="1",
        baseline={"absolute_delta_at_least": 5, "relative_delta_at_least": 0.5},
        interpretation="Unsupervised review priority, not biological certainty",
        fallback=None
        if model is not None
        else "Fewer than 8 changes: threshold baseline only",
    )
    with db_connection() as conn:
        mid = conn.execute(
            """INSERT INTO ml_model_run(model_kind,model_version,parameters,feature_schema,diff_run_ids,
            random_seed,artifact_path,artifact_sha256) VALUES (%s,%s,%s,%s,%s,42,%s,%s) RETURNING model_run_id""",
            (
                "isolation_forest" if model is not None else "threshold",
                version,
                json.dumps(parameters),
                json.dumps(FEATURES),
                [diff_id],
                artifact,
                artifact_hash,
            ),
        ).fetchone()["model_run_id"]
        flagged_count = baseline_count = agreement = done = 0
        with conn.cursor(name="scoring_features") as cursor:
            cursor.execute(FEATURE_SQL, (diff_id,))
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                values = np.asarray([vector(r) for r in rows], dtype=float)
                scores = (
                    -model.score_samples(values)
                    if model is not None
                    else values[:, 2] * values[:, 3]
                )
                flags = (
                    model.predict(values) == -1
                    if model is not None
                    else np.asarray([baseline(r) for r in rows])
                )
                inserts = []
                for row, v, score, flag in zip(rows, values, scores, flags):
                    old, new = row["old_weight"] or 0, row["new_weight"] or 0
                    bflag = baseline(row)
                    explanation = (
                        f"{row['source_neuron_id']} → {row['partner_source_id']}: {old} → {new} synapses; "
                        f"absolute change {abs(new - old)}, relative change {v[3]:.0%}; {row['change_type']}. "
                        f"Threshold baseline {'flagged' if bflag else 'did not flag'} this change. "
                        + (
                            "Isolation Forest ranks the feature pattern against this comparison; it does not identify a biological cause."
                            if model is not None
                            else "Small comparison: threshold scoring is used; no Isolation Forest was fitted."
                        )
                    )
                    inserts.append(
                        (
                            mid,
                            row["diff_event_id"],
                            float(score),
                            bool(flag),
                            json.dumps(dict(zip(FEATURES, map(float, v)))),
                            explanation,
                            bflag,
                        )
                    )
                    flagged_count += int(flag)
                    baseline_count += int(bflag)
                    agreement += int(bool(flag) == bflag)
                with conn.cursor() as writer:
                    writer.executemany(
                        """INSERT INTO anomaly_result(model_run_id,diff_event_id,score,is_flagged,score_rank,
                        features,explanation,baseline_flagged) VALUES (%s,%s,%s,%s,1,%s,%s,%s)""",
                        inserts,
                    )
                done += len(rows)
                progress(
                    40 + int(45 * done / count), f"Scored {done:,} of {count:,} changes"
                )
        conn.execute(
            """UPDATE anomaly_result a SET score_rank=r.rank FROM
            (SELECT diff_event_id,row_number() OVER(ORDER BY score DESC,diff_event_id) AS rank
             FROM anomaly_result WHERE model_run_id=%s) r WHERE a.model_run_id=%s AND a.diff_event_id=r.diff_event_id""",
            (mid, mid),
        )
        metrics = dict(
            rows=count,
            model_flagged=flagged_count,
            baseline_flagged=baseline_count,
            baseline_agreement=agreement / count,
            evaluation_status="Unlabelled: agreement is not accuracy",
            label_count=0,
            training_sample_cap=20000,
        )
        conn.execute(
            "UPDATE ml_model_run SET metrics=%s WHERE model_run_id=%s",
            (json.dumps(metrics), mid),
        )
    return {
        "model_run_id": mid,
        "model_kind": "isolation_forest" if model is not None else "threshold",
        "metrics": metrics,
    }


@router.get("/models")
def models():
    with db_connection() as conn:
        return conn.execute(
            "SELECT * FROM ml_model_run ORDER BY model_run_id DESC LIMIT 30"
        ).fetchall()


@router.get("/models/{model_id}/results")
def model_results(
    model_id: int,
    flagged_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    with db_connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM ml_model_run WHERE model_run_id=%s", (model_id,)
        ).fetchone():
            raise HTTPException(404, "model run not found")
        rows = conn.execute(
            """SELECT a.*,e.neuron_source_id,e.post_source_id,e.old_weight,e.new_weight,
            e.weight_delta,e.change_type,e.diff_run_id FROM anomaly_result a JOIN diff_event_detail e USING(diff_event_id)
            WHERE a.model_run_id=%s AND (NOT %s OR a.is_flagged) ORDER BY score_rank LIMIT %s OFFSET %s""",
            (model_id, flagged_only, limit, offset),
        ).fetchall()
        count = conn.execute(
            "SELECT count(*) AS n FROM anomaly_result WHERE model_run_id=%s AND (NOT %s OR is_flagged)",
            (model_id, flagged_only),
        ).fetchone()["n"]
        return dict(rows=rows, total=count, limit=limit, offset=offset)


def cosine(a, b):
    norm = math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))
    return sum(v * b.get(k, 0) for k, v in a.items()) / norm if norm else None


def fingerprint(conn, neuron, release):
    rows = conn.execute(
        """WITH r AS (SELECT release_order FROM release WHERE release_id=%s), edges AS (
        SELECT 'out' AS direction,post_neuron_id AS partner,synapse_count AS weight FROM connection,r
        WHERE pre_neuron_id=%s AND valid_rel @> r.release_order AND upper_inf(tx)
        UNION ALL SELECT 'in',pre_neuron_id,synapse_count FROM connection,r
        WHERE post_neuron_id=%s AND valid_rel @> r.release_order AND upper_inf(tx))
        SELECT e.direction,coalesce(h.annotation_value,t.annotation_value,'untyped') AS type,sum(e.weight) AS weight
        FROM edges e CROSS JOIN r LEFT JOIN annotation h ON h.dataset_neuron_id=e.partner
        AND h.annotation_key='hemibrain_type' AND h.valid_rel @> r.release_order AND upper_inf(h.tx)
        LEFT JOIN annotation t ON t.dataset_neuron_id=e.partner AND t.annotation_key='cell_type'
        AND t.valid_rel @> r.release_order AND upper_inf(t.tx) GROUP BY 1,2""",
        (release, neuron, neuron),
    ).fetchall()
    partners = {}
    for row in rows:
        tokens = [t.strip() for t in row["type"].split(",")]
        for token in tokens:
            key = row["direction"] + ":" + token
            partners[key] = partners.get(key, 0) + float(row["weight"]) / len(tokens)
    regions = {
        r["code"]: r["weight"]
        for r in conn.execute(
            """SELECT l.code,(pre_synapses+post_synapses)::float AS weight
        FROM neuron_region_synapses s JOIN brain_region_label l USING(brain_region_id)
        WHERE dataset_neuron_id=%s AND release_id=%s""",
            (neuron, release),
        )
    }
    return partners, regions


def score_mappings(limit=5000):
    cache, groups, scored = {}, defaultdict(list), 0
    with db_connection() as conn:
        rows = conn.execute(
            """SELECT m.*,p.release_id AS source_release,(m.evidence->>'hemibrain_release')::int AS target_release,
            (SELECT min(h.dataset_neuron_id) FROM neuron_mapping h WHERE h.canonical_neuron_id=m.canonical_neuron_id
             AND h.method='source_identity' AND h.dataset_neuron_id<>m.dataset_neuron_id) AS target_neuron
            FROM neuron_mapping m JOIN provenance p USING(provenance_id)
            WHERE m.method='published_type_crosswalk' AND m.canonical_neuron_id IS NOT NULL
            ORDER BY m.mapping_id LIMIT %s""",
            (limit,),
        ).fetchall()
        for row in rows:
            if not row["target_neuron"] or not row["target_release"]:
                continue
            vectors = []
            for key in [
                (row["dataset_neuron_id"], row["source_release"]),
                (row["target_neuron"], row["target_release"]),
            ]:
                if key not in cache:
                    cache[key] = fingerprint(conn, *key)
                vectors.append(cache[key])
            c, r = (
                cosine(vectors[0][0], vectors[1][0]),
                cosine(vectors[0][1], vectors[1][1]),
            )
            available = [(v, w) for v, w in [(c, 0.65), (r, 0.25)] if v is not None]
            score = (
                sum(v * w for v, w in available) / sum(w for _, w in available)
                if available
                else None
            )
            details = dict(
                version="connectivity-region-v1",
                rank_score=score,
                connectivity_cosine=c,
                region_cosine=r,
                feature_coverage=sum(w for _, w in available) / 0.9,
                source_release=row["source_release"],
                target_release=row["target_release"],
                target_neuron=row["target_neuron"],
                relationship="cross-specimen cell-type correspondence",
                explanation="Weighted cosine similarity of typed incoming/outgoing partners and region synapse profiles. A ranking score, not a calibrated probability."
                if available
                else "Insufficient connectivity/region evidence; type-and-side candidate only.",
            )
            conn.execute(
                "UPDATE neuron_mapping SET score_details=%s WHERE mapping_id=%s",
                (json.dumps(details), row["mapping_id"]),
            )
            groups[row["dataset_neuron_id"]].append({**row, "score": score})
            scored += 1
        eligible = [
            g
            for g in groups.values()
            if any(r["review_status"] == "approved" for r in g)
        ]
        top1 = sum(
            max(
                g,
                key=lambda r: (
                    r["score"] if r["score"] is not None else -1,
                    -r["mapping_id"],
                ),
            )["review_status"]
            == "approved"
            for g in eligible
        )
        baseline_top1 = sum(
            max(g, key=lambda r: (r["confidence"] or 0, -r["mapping_id"]))[
                "review_status"
            ]
            == "approved"
            for g in eligible
        )
    return dict(
        scored_candidates=scored,
        candidate_cap=limit,
        reviewed_queries=len(eligible),
        approved_top1_retrieval=top1 / len(eligible) if eligible else None,
        type_baseline_top1_retrieval=baseline_top1 / len(eligible)
        if eligible
        else None,
        evaluation_status="Retrospective reviewed subset; not held-out generalization"
        if eligible
        else "No approved reference matches: accuracy cannot be estimated",
        model="Deterministic connectivity/region baseline; no learned embeddings or GraphSAGE",
    )


@router.get("/matching")
def matching(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    with db_connection() as conn:
        rows = conn.execute(
            """SELECT m.*,d.code AS dataset,dn.source_neuron_id,c.label AS target_label
            FROM neuron_mapping m JOIN dataset_neuron dn USING(dataset_neuron_id) JOIN dataset d USING(dataset_id)
            LEFT JOIN canonical_neuron c USING(canonical_neuron_id)
            WHERE m.method<>'source_identity' AND (%s::text IS NULL OR m.review_status=%s)
            ORDER BY (m.score_details->>'rank_score')::float DESC NULLS LAST,m.mapping_id LIMIT %s OFFSET %s""",
            (status, status, limit, offset),
        ).fetchall()
        return {
            "rows": rows,
            "review_token_required": bool(get_settings().cc_review_token),
        }


@router.get("/metadata/{dataset}/check")
def metadata_check(dataset: str):
    from backend.metadata import check_consistency

    return check_consistency(dataset)
