"""Import a right-hemisphere, gamma5-associated olfactory circuit from verified FlyWire 783 rows."""

import csv
import gzip
import hashlib
import json
import re

from psycopg.types.json import Jsonb
from backend.config import RAW_DIR
from backend.db import connect


def freeze():
    folder = RAW_DIR / "flywire/783"
    files = ("classification", "consolidated_cell_types", "connections")
    paths = {name: folder / f"{name}.csv.gz" for name in files}
    hashes = {}
    for name, path in paths.items():
        with path.open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()

    def rows(name):
        with gzip.open(paths[name], "rt", encoding="utf-8", newline="") as stream:
            yield from csv.DictReader(stream)

    right = {r["root_id"] for r in rows("classification") if r["side"] == "right"}
    # MBON01 has contralateral dendrites. Select its arbor by the edge neuropil.
    types = {r["root_id"]: r["primary_type"] for r in rows("consolidated_cell_types")}
    inputs, outputs, teaching = [], [], []
    pn = {root for root, typ in types.items() if re.fullmatch(r"[A-Z]+\d+[a-z]*_(?:ad|l)PN", typ)}
    for line, r in enumerate(rows("connections"), 2):
        a, b = r["pre_root_id"], r["post_root_id"]
        if r["neuropil"] == "MB_CA_R" and a in right and b in right and a in pn and types.get(b) == "KCg-m":
            inputs.append((a, b, types[a], int(r["syn_count"]), line))
        elif r["neuropil"] == "MB_ML_R" and a in right and types.get(b) == "MBON01":
            if types.get(a) == "KCg-m":
                outputs.append((a, b, types[a], int(r["syn_count"]), line))
            if types.get(a) == "PAM01":
                teaching.append((a, b, types[a], int(r["syn_count"]), line))
    reachable = {r[1] for r in inputs} & {r[0] for r in outputs}
    selected = (
        [("input", *r) for r in inputs if r[1] in reachable]
        + [("output", *r) for r in outputs if r[0] in reachable]
        + [("teaching", *r) for r in teaching]
    )
    if len(reachable) < 12 or not teaching:
        raise ValueError("Insufficient right calyx to medial lobe connectivity")
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        release = conn.execute(
            """SELECT release_id,dataset_id,release_order FROM release JOIN dataset USING(dataset_id)
            WHERE code='flywire' AND version_label='783' AND ingestion_status='loaded'"""
        ).fetchone()
        if not release:
            raise ValueError("Load FlyWire 783 first")
        rid, did, order = release
        for name, sha in hashes.items():
            archive = f"flywire/783/{name}.csv.gz"
            if not conn.execute(
                """SELECT 1 FROM source_file
                WHERE release_id=%s AND archive_path=%s AND member_path='' AND sha256=%s""",
                (rid, archive, sha),
            ).fetchone():
                raise ValueError(f"{name} checksum differs from the loaded release manifest")
        conn.execute(
            "CREATE TEMP TABLE chosen(kind text,a text,b text,typ text,w int,line int) ON COMMIT DROP"
        )
        with conn.cursor().copy("COPY chosen FROM STDIN") as cp:
            for r in selected:
                cp.write_row(r)
        conn.execute("ANALYZE chosen")
        edges = conn.execute(
            """SELECT c.connection_id,k.kind,k.typ,k.w,k.line FROM chosen k
            JOIN dataset_neuron a ON a.dataset_id=%s AND a.source_neuron_id=k.a
            JOIN dataset_neuron b ON b.dataset_id=a.dataset_id AND b.source_neuron_id=k.b
            JOIN connection c ON c.pre_neuron_id=a.dataset_neuron_id AND c.post_neuron_id=b.dataset_neuron_id
            JOIN provenance p ON p.provenance_id=c.provenance_id
            WHERE c.valid_rel @> %s::int AND upper_inf(c.tx) AND c.synapse_count>=k.w
            AND (p.release_id<>%s OR k.line=ANY(c.source_lines))
            ORDER BY c.connection_id""",
            (did, order, rid),
        ).fetchall()
        if len(edges) != len(selected):
            raise ValueError("Regional edge does not match the release-valid connection")
        digest = hashlib.sha256(json.dumps([rid, hashes, edges], sort_keys=True).encode()).hexdigest()
        snap = conn.execute(
            """INSERT INTO memory_snapshot(release_id,digest,sources) VALUES(%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING snapshot_id""",
            (rid, digest, Jsonb(hashes)),
        ).fetchone()
        if snap:
            with conn.cursor().copy(
                "COPY memory_edge(snapshot_id,connection_id,pathway,channel,regional_weight,source_line) FROM STDIN"
            ) as cp:
                for edge in edges:
                    cp.write_row((snap[0], *edge))
    print(f"Verified {len(reachable)} Kenyon cells, {len(edges)} regional connections, digest {digest}")


if __name__ == "__main__":
    freeze()
