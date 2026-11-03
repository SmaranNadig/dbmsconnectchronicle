"""Per-release brain-region synapse counts (cc.neuron_region_synapses).

Reads the release's region-split connection file (schema mapping section
`region_connections`), maps every source region code to the shared
brain_region vocabulary, and aggregates outgoing and incoming synapses per
(neuron, region) in SQL. One transaction per release: an unknown region code
or a source neuron missing from dataset_neuron fails the whole release and
leaves earlier results in place. Re-running replaces that release's rows.
"""
from __future__ import annotations

import logging
import re
import time

import pandas as pd

from backend.db import connect
from backend.etl.adapters import ValidationError, _chunks, get_adapter
from backend.etl.loader import _copy_frame, _provenance, _source_file
from backend.etl.sources import find_source

log = logging.getLogger("cc.regions")


def load_regions(dataset: str, release: str) -> dict:
    adapter = get_adapter(dataset, release)
    spec = adapter.mapping.get("region_connections")
    if not spec:
        raise ValidationError(f"{dataset}: schema mapping has no region_connections section")
    ref = find_source(adapter.release_dir, spec["file"])
    if ref is None:
        raise ValidationError(f"{spec['file']} not found in {adapter.release_dir}")
    pattern = re.compile(spec["code_pattern"])
    rename = spec.get("rename", {})
    t0 = time.perf_counter()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT r.release_id, r.dataset_id, r.ingestion_status,
                              (SELECT max(run_id) FROM ingestion_run i
                               WHERE i.release_id = r.release_id AND i.status = 'succeeded')
                       FROM release r JOIN dataset d USING (dataset_id)
                       WHERE d.code = %s AND r.version_label = %s""", (dataset, release))
        row = cur.fetchone()
        if row is None or row[2] != "loaded":
            raise ValidationError(f"{dataset} {release}: load the release before its regions")
        rel_id, ds_id, _, run_id = row

        cur.execute("""CREATE TEMP TABLE stg_region (pre text NOT NULL, post text NOT NULL, region text NOT NULL,
                       weight integer NOT NULL) ON COMMIT DROP""")
        rows = 0
        for ch in _chunks(ref, [spec["pre_column"], spec["post_column"], spec["region_column"],
                                spec["weight_column"]]):
            df = pd.DataFrame({"pre": adapter.normalize_id(ch[spec["pre_column"]]),
                               "post": adapter.normalize_id(ch[spec["post_column"]]),
                               "region": ch[spec["region_column"]].str.strip(),
                               "weight": ch[spec["weight_column"]].str.strip()})
            rows += _copy_frame(cur, "stg_region", ["pre", "post", "region", "weight"], df)

        # region codes -> shared vocabulary, recorded as aliases
        cur.execute("SELECT DISTINCT region FROM stg_region")
        aliases = {}
        for (code,) in cur.fetchall():
            m = pattern.match(code)
            if not m:
                raise ValidationError(f"region code {code!r} does not match {spec['code_pattern']}")
            canonical = rename.get(m["code"], m["code"])
            cur.execute("""SELECT brain_region_id FROM brain_region
                           WHERE canonical_code = %s AND hemisphere IS NOT DISTINCT FROM %s""",
                        (canonical, m["side"]))
            hit = cur.fetchone()
            if hit is None:
                raise ValidationError(f"region code {code!r} -> {canonical}/{m['side']} is not in brain_region")
            aliases[code] = hit[0]
        cur.executemany("""INSERT INTO brain_region_alias (dataset_id, source_code, brain_region_id)
                           VALUES (%s, %s, %s) ON CONFLICT (dataset_id, source_code)
                           DO UPDATE SET brain_region_id = EXCLUDED.brain_region_id""",
                        [(ds_id, c, r) for c, r in aliases.items()])

        cur.execute("""SELECT count(*) FROM stg_region s
                       WHERE NOT EXISTS (SELECT 1 FROM dataset_neuron dn
                                         WHERE dn.dataset_id = %(ds)s AND dn.source_neuron_id = s.pre)
                          OR NOT EXISTS (SELECT 1 FROM dataset_neuron dn
                                         WHERE dn.dataset_id = %(ds)s AND dn.source_neuron_id = s.post)""",
                    {"ds": ds_id})
        unknown = cur.fetchone()[0]
        if unknown:
            raise ValidationError(f"{unknown} region rows reference neurons that are not in dataset_neuron")

        sf = _source_file(cur, rel_id, ref, rows)
        prov = _provenance(cur, ds_id, rel_id, sf, run_id, "aggregate_region_synapses",
                           {k: spec[k] for k in ("pre_column", "post_column", "region_column", "weight_column")}
                           | {"rename": rename})
        cur.execute("DELETE FROM neuron_region_synapses WHERE release_id = %s", (rel_id,))
        cur.execute("""
            INSERT INTO neuron_region_synapses (release_id, dataset_id, dataset_neuron_id, brain_region_id,
                                                pre_synapses, post_synapses, provenance_id)
            WITH side AS (
                SELECT pre AS sid, a.brain_region_id, weight AS pre_w, 0 AS post_w FROM stg_region s
                JOIN brain_region_alias a ON a.dataset_id = %(ds)s AND a.source_code = s.region
                UNION ALL
                SELECT post, a.brain_region_id, 0, weight FROM stg_region s
                JOIN brain_region_alias a ON a.dataset_id = %(ds)s AND a.source_code = s.region)
            SELECT %(rel)s, %(ds)s, dn.dataset_neuron_id, side.brain_region_id,
                   sum(pre_w), sum(post_w), %(prov)s
            FROM side JOIN dataset_neuron dn ON dn.dataset_id = %(ds)s AND dn.source_neuron_id = side.sid
            GROUP BY dn.dataset_neuron_id, side.brain_region_id
            HAVING sum(pre_w) + sum(post_w) > 0""", {"ds": ds_id, "rel": rel_id, "prov": prov})
        written = cur.rowcount
        conn.commit()
    stats = {"dataset": dataset, "release": release, "source_rows": rows, "region_codes": len(aliases),
             "neuron_region_rows": written, "seconds": round(time.perf_counter() - t0, 1)}
    log.info("%s", stats)
    return stats
