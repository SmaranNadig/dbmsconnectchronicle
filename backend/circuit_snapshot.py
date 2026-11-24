"""Freeze the release-783 right-eye circuit. Run with python -m backend.circuit_snapshot."""
import argparse
import csv
import gzip
import hashlib
import json

from backend.config import RAW_DIR
from backend.db import connect

URL = "https://storage.googleapis.com/flywire-data/codex/data/fafb/783/column_assignment.csv.gz"
SHA = "bdf4ce7f62cc63493d53eefad3816ff2dfd08b190e97b35a492e0e453df2f0f6"
INPUT = ("L1", "L2")
MID = ("Mi1", "Tm3", "Mi4", "Mi9", "Tm1", "Tm2", "Tm4", "Tm9")
OUTPUT = tuple(f"T{i}{s}" for i in (4, 5) for s in "abcd")


def load_wiring(conn, snapshot_id):
    nodes = conn.execute("""SELECT neuron_id,cell_type,p,q FROM circuit_node
        WHERE snapshot_id=%s ORDER BY neuron_id""", (snapshot_id,)).fetchall()
    edges = conn.execute("""SELECT pre_id,post_id,weight FROM circuit_edge
        WHERE snapshot_id=%s ORDER BY pre_id,post_id""", (snapshot_id,)).fetchall()
    return nodes, edges


def freeze(radius=4):
    path = RAW_DIR / "flywire/783/column_assignment.csv.gz"
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != SHA:
        raise ValueError(f"Place the checksum-matching file from {URL} at {path}")
    with gzip.open(path, "rt") as source:
        selected = [(r["root_id"], r["type"], int(r["p"]), int(r["q"]))
                    for r in csv.DictReader(source) if r["hemisphere"] == "right"
                    and r["type"] in INPUT + MID + OUTPUT
                    and max(abs(int(r["p"])), abs(int(r["q"]))) <= radius]
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        release = conn.execute("""SELECT release_id, dataset_id, release_order FROM release
            JOIN dataset USING(dataset_id) WHERE code='flywire' AND version_label='783'
            AND ingestion_status='loaded'""").fetchone()
        if not release:
            raise ValueError("Load FlyWire 783 on the coordinator first")
        rid, dataset, order = release
        conn.execute("CREATE TEMP TABLE coords(root text PRIMARY KEY, typ text, p int, q int) ON COMMIT DROP")
        with conn.cursor().copy("COPY coords FROM STDIN") as copy:
            for row in selected:
                copy.write_row(row)
        conn.execute("""CREATE TEMP TABLE patch ON COMMIT DROP AS SELECT n.dataset_neuron_id id,
            c.typ, c.p, c.q FROM coords c JOIN dataset_neuron n ON n.source_neuron_id=c.root
            JOIN annotation a ON a.dataset_neuron_id=n.dataset_neuron_id AND a.annotation_key='cell_type'
            AND a.annotation_value=c.typ AND a.valid_rel @> %s::int AND upper_inf(a.tx)
            WHERE n.dataset_id=%s""", (order, dataset))
        conn.execute("CREATE UNIQUE INDEX ON patch(id)")
        conn.execute("ANALYZE patch")
        nodes = conn.execute("SELECT * FROM patch ORDER BY id").fetchall()
        edges = conn.execute("""SELECT u.id, v.id, e.synapse_count, e.connection_id
            FROM patch u JOIN connection e ON e.pre_neuron_id=u.id JOIN patch v ON v.id=e.post_neuron_id
            WHERE e.dataset_id=%s AND e.valid_rel @> %s::int AND upper_inf(e.tx) AND e.synapse_count>0
            AND ((u.typ=ANY(%s) AND v.typ=ANY(%s)) OR (u.typ=ANY(%s) AND v.typ=ANY(%s)))
            ORDER BY u.id,v.id""", (dataset, order, list(INPUT), list(MID), list(MID), list(OUTPUT))).fetchall()
        if not edges or {n[1] for n in nodes} != set(INPUT + MID + OUTPUT):
            raise ValueError("Missing required cell types or edges in this release")
        digest = hashlib.sha256(json.dumps([rid, SHA, radius, nodes, edges]).encode()).hexdigest()
        row = conn.execute("""INSERT INTO circuit_snapshot(release_id,digest,column_sha256,radius)
            VALUES (%s,%s,%s,%s) ON CONFLICT (digest) DO NOTHING RETURNING snapshot_id""",
            (rid, digest, SHA, radius)).fetchone()
        if row:
            for table, fields, rows in [("circuit_node","neuron_id,cell_type,p,q",nodes),
                                        ("circuit_edge","pre_id,post_id,weight,connection_id",edges)]:
                with conn.cursor().copy(f"COPY {table}(snapshot_id,{fields}) FROM STDIN") as copy:
                    for record in rows:
                        copy.write_row((row[0], *record))
    print(f"Radius {radius}: {len(nodes)}/{len(selected)} annotated cells, {len(edges)} edges, SHA256 {digest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--radius", type=int, choices=range(2, 7), default=4)
    freeze(parser.parse_args().radius)

