"""Normal form of every relation, computed from its declared dependencies and
checked against the live database (docs/NORMALISATION.md).

  analyse()           keys, highest normal form, violations, BCNF decomposition
                      (lossless? dependency preserving?) for every relation
  verify(conn)        on the loaded data: every non-key FD holds, every
                      candidate key is enforced by a unique constraint, every
                      constraint named as protecting a redundancy exists, and the
                      catalogue covers exactly the live tables and columns
"""
from __future__ import annotations

import time

from psycopg import sql
from psycopg.rows import dict_row

from backend.design import fd as F
from backend.design.relations import ALL, RELATIONS, qualified

FORMS = ["1NF", "2NF", "3NF", "BCNF"]


def fds_of(rel: dict) -> list[F.FD]:
    out = []
    for text in rel["fds"]:
        lhs, rhs = F.parse(text.replace("*", ALL))
        if ALL in rhs:
            rhs = frozenset(rel["attrs"]) - lhs
        unknown = (lhs | rhs) - set(rel["attrs"])
        if unknown:
            raise ValueError(f"{qualified(rel)}: {text} uses unknown attributes {sorted(unknown)}")
        out.append((lhs, rhs))
    return out


def _names(s) -> list[str]:
    return sorted(s)


def analyse_relation(rel: dict) -> dict:
    fds = fds_of(rel)
    a = F.analyse(rel["attrs"], fds)
    documented = {F.parse(r["fd"]) for r in rel.get("redundancy", [])}
    documented = {(l, frozenset(r)) for l, r in documented}
    violations = []
    for v in a.violations:
        covered = any(v.fd[0] == l and v.fd[1] <= r for l, r in documented)
        violations.append({"form": v.form, "fd": F.fmt(v.fd), "reason": v.reason, "documented": covered})
    out = {
        "relation": qualified(rel), "schema": rel["schema"], "name": rel["name"], "group": rel["group"],
        "purpose": rel["purpose"], "attributes": rel["attrs"],
        "fds": [F.fmt(f) for f in fds], "minimal_cover": [F.fmt(f) for f in a.fds],
        "candidate_keys": [_names(k) for k in a.keys], "prime": _names(a.prime),
        "non_prime": [x for x in rel["attrs"] if x not in a.prime],
        "highest_form": a.highest, "violations": violations,
        "first_nf": {"holds": True, "non_scalar": rel.get("atomic", {})},
        "temporal": rel.get("temporal"), "rules": rel.get("rules", []),
        "redundancy": rel.get("redundancy", []),
        "satisfies": {f: FORMS.index(f) <= FORMS.index(a.highest) for f in FORMS},
    }
    if a.violations:
        parts = F.bcnf_decompose(rel["attrs"], a.fds)
        out["bcnf_decomposition"] = {
            "parts": [{"attributes": [x for x in rel["attrs"] if x in p],
                       "fds": [F.fmt(f) for f in pf],
                       "keys": [_names(k) for k in F.candidate_keys(p, pf)]} for p, pf in parts],
            "lossless": F.lossless(rel["attrs"], [p for p, _ in parts], a.fds),
            "not_preserved": [F.fmt(f) for f in F.preserved([p for p, _ in parts], a.fds)],
        }
    return out


def analyse() -> dict:
    rels = [analyse_relation(r) for r in RELATIONS]
    summary = {f: sum(1 for r in rels if r["highest_form"] == f) for f in FORMS}
    return {
        "relations": rels,
        "summary": {
            "relations": len(rels),
            "by_highest_form": summary,
            "bcnf": sum(1 for r in rels if r["highest_form"] == "BCNF"),
            "controlled_redundancy": [r["relation"] for r in rels if r["violations"]],
            "undocumented_violations": [f"{r['relation']}: {v['fd']}" for r in rels for v in r["violations"]
                                        if not v["documented"]],
        },
    }


# ----------------------------------------------------------------- live checks
def _fd_sql(schema: str, table: str, lhs, rhs) -> sql.Composed:
    """Rows of `table` that break lhs -> rhs: groups of equal non-NULL lhs with
    more than one distinct rhs. Zero groups = the dependency holds."""
    l = [sql.Identifier(a) for a in sorted(lhs)]
    r = sql.SQL(", ").join(sql.Identifier(a) for a in sorted(rhs))
    rhs_expr = sql.SQL("ROW({})").format(r) if len(rhs) > 1 else r
    return sql.SQL("SELECT count(*) FROM (SELECT 1 FROM {}.{} WHERE {} GROUP BY {} "
                   "HAVING count(DISTINCT {}) > 1 OR count(*) FILTER (WHERE {} IS NULL) "
                   "BETWEEN 1 AND count(*) - 1) bad").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(" AND ").join(sql.SQL("{} IS NOT NULL").format(x) for x in l),
        sql.SQL(", ").join(l), rhs_expr, rhs_expr)


UNIQUE_SQL = """
SELECT array_agg(a.attname::text ORDER BY a.attname) AS cols, c.relname AS index_name,
       con.conname AS constraint_name, i.indisprimary AS primary
FROM pg_index i
JOIN pg_class t ON t.oid = i.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY (i.indkey)
LEFT JOIN pg_constraint con ON con.conindid = i.indexrelid AND con.contype IN ('p', 'u')
WHERE n.nspname = %s AND t.relname = %s AND i.indisunique AND i.indpred IS NULL
GROUP BY c.relname, con.conname, i.indisprimary"""

NAMES_SQL = """
SELECT conname::text FROM pg_constraint con JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = %(s)s AND t.relname = %(t)s
UNION SELECT tgname::text FROM pg_trigger g JOIN pg_class t ON t.oid = g.tgrelid
JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = %(s)s AND t.relname = %(t)s"""

COLUMNS_SQL = """
SELECT c.table_schema, c.table_name, array_agg(c.column_name::text ORDER BY c.ordinal_position)
FROM information_schema.columns c JOIN information_schema.tables t USING (table_schema, table_name)
WHERE c.table_schema IN ('cc', 'lab') AND t.table_type = 'BASE TABLE'
GROUP BY 1, 2"""


def coverage(conn) -> dict:
    live = {f"{s}.{t}": cols for s, t, cols in _rows(conn, COLUMNS_SQL)}
    declared = {qualified(r): r["attrs"] for r in RELATIONS}
    return {
        "missing": sorted(set(live) - set(declared)),          # live table not catalogued
        "extra": sorted(set(declared) - set(live)),            # catalogued table that does not exist
        "column_mismatch": {name: {"live": live[name], "declared": declared[name]}
                            for name in set(live) & set(declared) if live[name] != declared[name]},
        "live_tables": len(live),
    }


def _rows(conn, query, params=None):
    cur = conn.cursor()
    cur.execute(query, params)
    return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]


def verify(conn, scan_data: bool = True) -> dict:
    """Check every relation against the live database. With scan_data, every
    dependency whose left side is not enforced unique is tested on the rows."""
    cov = coverage(conn)
    results = []
    for rel in RELATIONS:
        name = qualified(rel)
        if name in cov["extra"]:
            continue
        a = F.analyse(rel["attrs"], fds_of(rel))
        uniques = [(frozenset(cols), idx, con, prim) for cols, idx, con, prim in
                   _rows(conn, UNIQUE_SQL, (rel["schema"], rel["name"]))]
        names = {r[0] for r in _rows(conn, NAMES_SQL, {"s": rel["schema"], "t": rel["name"]})}
        keys = []
        for k in a.keys:
            match = next((u for u in uniques if u[0] == k), None)
            if match:
                keys.append({"key": _names(k), "enforced_by": match[2] or match[1]})
                continue
            # a key that follows from an enforced key plus enforced dependencies, e.g.
            # (first_release_id, source_neuron_id) from (dataset_id, source_neuron_id)
            # and first_release_id -> dataset_id
            via = next((u for u in uniques if u[0] <= F.closure(k, a.fds) and u[0] - k), None)
            keys.append({"key": _names(k), "enforced_by": f"implied by {via[2] or via[1]}" if via else None})
        checks = []
        for lhs, rhs in fds_of(rel):
            unique = next((u for u in uniques if u[0] <= lhs), None)
            item = {"fd": F.fmt((lhs, rhs))}
            if unique:
                item.update(status="enforced", by=unique[2] or unique[1],
                            note="left side contains a unique key, so the dependency cannot be broken")
            elif scan_data:
                t0 = time.perf_counter()
                bad = conn.execute(_fd_sql(rel["schema"], rel["name"], lhs, rhs)).fetchone()
                bad = list(bad.values())[0] if isinstance(bad, dict) else bad[0]
                item.update(status="holds" if bad == 0 else "violated", violating_groups=bad,
                            ms=round((time.perf_counter() - t0) * 1000, 1),
                            sql=_fd_sql(rel["schema"], rel["name"], lhs, rhs).as_string(conn))
            else:
                item.update(status="not_scanned")
            checks.append(item)
        protections = []
        for red in rel.get("redundancy", []):
            for c in red["enforced_by"]:
                protections.append({"fd": red["fd"], "constraint": c,
                                    "exists": c.startswith("GENERATED") or c in names})
        for rule in rel.get("rules", []):
            trig = rule["enforced_by"].split()[0]
            protections.append({"fd": rule["rule"], "constraint": trig, "exists": trig in names})
        if rel.get("temporal"):
            c = rel["temporal"]["enforced_by"]
            protections.append({"fd": rel["temporal"]["key"], "constraint": c, "exists": c in names})
        results.append({"relation": name, "keys": keys, "dependencies": checks, "protections": protections})
    ok = (not cov["missing"] and not cov["column_mismatch"]
          and all(k["enforced_by"] for r in results for k in r["keys"])
          and all(c["status"] in ("enforced", "holds", "not_scanned") for r in results for c in r["dependencies"])
          and all(p["exists"] for r in results for p in r["protections"]))
    return {"ok": ok, "coverage": cov, "relations": results}


# ------------------------------------------------------------- walk-through
# How the source data becomes the relations above, one normal form at a time.
# Each step lists its relations with attributes and FDs; analyse() of every
# step is computed, so the stated keys and normal forms are checked.
WALKTHROUGH = [
    {"form": "UNF", "title": "A release as published: one record per neuron with repeating groups",
     "relations": [{"name": "release_record",
                    "attrs": ["dataset_code", "dataset_name", "version_label", "release_order", "source_neuron_id",
                              "cell_type", "side", "primary_region", "super_region",
                              "{partners: post_neuron_id, synapse_count}"],
                    "fds": [], "note": "partners is a repeating group (a list inside the record): not a relation"}]},
    {"form": "1NF", "title": "Flatten the repeating group: one row per (release, neuron, partner)",
     "relations": [{"name": "connection_row",
                    "attrs": ["dataset_code", "dataset_name", "version_label", "release_order", "source_neuron_id",
                              "cell_type", "side", "primary_region", "super_region", "post_neuron_id",
                              "synapse_count"],
                    "fds": ["dataset_code -> dataset_name",
                            "dataset_code, version_label -> release_order",
                            "dataset_code, release_order -> version_label",
                            "dataset_code, version_label, source_neuron_id -> cell_type, side, primary_region",
                            "primary_region -> super_region",
                            "dataset_code, version_label, source_neuron_id, post_neuron_id -> synapse_count"]}]},
    {"form": "2NF", "title": "Remove partial dependencies on the key (dataset, release, neuron, partner)",
     "relations": [
         {"name": "dataset", "attrs": ["dataset_code", "dataset_name"], "fds": ["dataset_code -> dataset_name"]},
         {"name": "release", "attrs": ["dataset_code", "version_label", "release_order"],
          "fds": ["dataset_code, version_label -> release_order", "dataset_code, release_order -> version_label"]},
         {"name": "neuron_in_release",
          "attrs": ["dataset_code", "version_label", "source_neuron_id", "cell_type", "side", "primary_region",
                    "super_region"],
          "fds": ["dataset_code, version_label, source_neuron_id -> cell_type, side, primary_region",
                  "primary_region -> super_region"]},
         {"name": "connection",
          "attrs": ["dataset_code", "version_label", "source_neuron_id", "post_neuron_id", "synapse_count"],
          "fds": ["dataset_code, version_label, source_neuron_id, post_neuron_id -> synapse_count"]}]},
    {"form": "3NF", "title": "Remove the transitive dependency primary_region → super_region",
     "relations": [
         {"name": "neuron_in_release",
          "attrs": ["dataset_code", "version_label", "source_neuron_id", "cell_type", "side", "primary_region"],
          "fds": ["dataset_code, version_label, source_neuron_id -> cell_type, side, primary_region"]},
         {"name": "neuropil", "attrs": ["primary_region", "super_region"], "fds": ["primary_region -> super_region"],
          "note": "this is the split made in the real schema by migration 0013 (cc.neuropil)"}]},
    {"form": "BCNF", "title": "Surrogate identity: a neuron is first seen in one release",
     "relations": [
         {"name": "neuron_identity (3NF, not BCNF)",
          "attrs": ["dataset_code", "source_neuron_id", "first_release"],
          "fds": ["dataset_code, source_neuron_id -> first_release", "first_release -> dataset_code"],
          "note": "first_release → dataset_code: the left side is not a superkey, but dataset_code is prime, "
                  "so 3NF holds and BCNF does not"},
         {"name": "BCNF option A: release_of", "attrs": ["first_release", "dataset_code"],
          "fds": ["first_release -> dataset_code"]},
         {"name": "BCNF option A: neuron_first_seen", "attrs": ["first_release", "source_neuron_id"], "fds": [],
          "note": "lossless, but (dataset_code, source_neuron_id) → first_release is no longer checked "
                  "inside one relation: two releases of a dataset could both claim the same neuron"}],
     "decision": "cc.dataset_neuron keeps the 3NF form and protects first_release → dataset with the composite "
                 "foreign key (first_release_id, dataset_id) → release, so both dependencies stay enforced. "
                 "This is the classic case where BCNF and dependency preservation cannot both be had."},
]


SIDE_NOTE = ("side is NULL: hemibrain publishes no side column (the side only appears as the suffix of "
             "`instance`, e.g. LHCENT3_R), and nothing is invented here")


def walkthrough(conn=None) -> list[dict]:
    """The UNF → BCNF steps with computed keys and normal forms; with a
    connection, each relation also carries the real rows of one neuron
    (walkthrough_sample)."""
    sample = walkthrough_sample(conn) if conn is not None else None
    out = []
    for step in WALKTHROUGH:
        rels = []
        for r in step["relations"]:
            item = {**r}
            if r["fds"]:
                fds = [F.parse(t) for t in r["fds"]]
                a = F.analyse(r["attrs"], fds)
                item.update(keys=[_names(k) for k in a.keys], highest_form=a.highest,
                            violations=[{"form": v.form, "fd": F.fmt(v.fd), "reason": v.reason} for v in a.violations])
            elif step["form"] != "UNF":
                item.update(keys=[r["attrs"]], highest_form="BCNF", violations=[])
            if sample:
                item["rows"] = sample["rows"].get(step["form"], {}).get(r["name"])
            rels.append(item)
        entry = {**step, "relations": rels}
        if sample:
            entry["sample"] = {k: sample[k] for k in ("neuron", "release", "partners_total", "partners_shown")}
            if step["form"] == "UNF" and any(None in row for rr in sample["rows"]["UNF"].values() for row in rr):
                entry["sample"]["note"] = SIDE_NOTE
        out.append(entry)
    return out


# ------------------------------------------------- walkthrough on real rows
SAMPLE_SQL = """
WITH n AS (
    SELECT dn.dataset_neuron_id, dn.source_neuron_id, d.code AS dataset_code, d.name AS dataset_name,
           r.version_label, r.release_order, r.release_id, fr.version_label AS first_release
    FROM dataset_neuron dn JOIN dataset d USING (dataset_id)
    JOIN release r ON r.dataset_id = d.dataset_id
    JOIN release fr ON fr.release_id = dn.first_release_id
    WHERE d.code = %(dataset)s AND dn.source_neuron_id = %(source)s AND r.version_label = %(release)s)
SELECT n.*,
       (SELECT a.annotation_value FROM annotation a WHERE a.dataset_neuron_id = n.dataset_neuron_id
         AND a.annotation_key = 'cell_type' AND a.valid_rel @> n.release_order::int AND upper_inf(a.tx)) AS cell_type,
       (SELECT a.annotation_value FROM annotation a WHERE a.dataset_neuron_id = n.dataset_neuron_id
         AND a.annotation_key = 'side' AND a.valid_rel @> n.release_order::int AND upper_inf(a.tx)) AS side,
       b.code AS primary_region, b.super_region
FROM n
LEFT JOIN neuron_primary_region p ON p.dataset_neuron_id = n.dataset_neuron_id AND p.release_id = n.release_id
LEFT JOIN brain_region_label b ON b.brain_region_id = p.brain_region_id"""

PARTNERS_SQL = """
SELECT post.source_neuron_id AS post_neuron_id, c.synapse_count, count(*) OVER () AS partners
FROM connection c JOIN dataset_neuron post ON post.dataset_neuron_id = c.post_neuron_id
WHERE c.pre_neuron_id = %s AND c.valid_rel @> %s::int AND upper_inf(c.tx)
ORDER BY c.synapse_count DESC, post.source_neuron_id
LIMIT %s"""


def walkthrough_sample(conn, dataset: str = "hemibrain", source: str = "487144598", release: str = "v1.2",
                       partners: int = 3) -> dict | None:
    """The walkthrough's relations filled with one real neuron's values from
    the loaded data: the record as published (UNF), its flattened rows (1NF),
    and the rows of each decomposed relation. Returns None when the neuron is
    not loaded (e.g. on the test fixtures)."""
    cur = conn.cursor(row_factory=dict_row)
    n = cur.execute(SAMPLE_SQL, {"dataset": dataset, "source": source, "release": release}).fetchone()
    if n is None:
        return None
    ps = cur.execute(PARTNERS_SQL, (n["dataset_neuron_id"], n["release_order"], partners)).fetchall()
    total = ps[0]["partners"] if ps else 0
    neuron = {k: n[k] for k in ("dataset_code", "dataset_name", "version_label", "release_order", "source_neuron_id",
                                 "cell_type", "side", "primary_region", "super_region")}
    pick = lambda row, cols: [row[c] for c in cols]
    w = {s["form"]: {r["name"]: r["attrs"] for r in s["relations"]} for s in WALKTHROUGH}
    partner_list = "; ".join(f"{p['post_neuron_id']}: {p['synapse_count']}" for p in ps)
    flat = [{**neuron, "post_neuron_id": p["post_neuron_id"], "synapse_count": p["synapse_count"]} for p in ps]
    rows = {
        "UNF": {"release_record": [pick(neuron, w["UNF"]["release_record"][:-1])
                                   + [f"[{partner_list}; … {total} partners]"]]},
        "1NF": {"connection_row": [pick(f, w["1NF"]["connection_row"]) for f in flat]},
        "2NF": {"dataset": [pick(neuron, w["2NF"]["dataset"])],
                "release": [pick(neuron, w["2NF"]["release"])],
                "neuron_in_release": [pick(neuron, w["2NF"]["neuron_in_release"])],
                "connection": [pick(f, w["2NF"]["connection"]) for f in flat]},
        "3NF": {"neuron_in_release": [pick(neuron, w["3NF"]["neuron_in_release"])],
                "neuropil": [pick(neuron, w["3NF"]["neuropil"])]},
        "BCNF": {"neuron_identity (3NF, not BCNF)": [[n["dataset_code"], n["source_neuron_id"], n["first_release"]]]},
    }
    return {"neuron": f"{dataset} {source}", "release": release, "partners_total": total,
            "partners_shown": len(ps), "rows": rows,
            "repeated": {"neuron_facts_per_partner": len(neuron) - 1, "copies_in_1nf": total}}
