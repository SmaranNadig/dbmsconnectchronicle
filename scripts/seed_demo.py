"""Create a reproducible, labelled synthetic demo without touching public databases.

python scripts/seed_demo.py                         # connectome_demo / connectome_meta_demo
python scripts/seed_demo.py --database connectome_review --mongo-database connectome_meta_review
"""

import argparse
import csv
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def generate_demo():
    import yaml

    fixture = ROOT / "tests" / "fixtures" / "toy"
    output = ROOT / ".dbdata" / "demo"
    shutil.copytree(fixture / "raw", output / "raw", dirs_exist_ok=True)
    shutil.copytree(
        fixture / "schema_mappings", output / "schema_mappings", dirs_exist_ok=True
    )
    catalog = yaml.safe_load((fixture / "catalog.yaml").read_text())
    catalog["datasets"]["chronicle_demo"] = dict(
        name="Synthetic Chronicle demonstration (not biological data)",
        organism="Synthetic graph; no biological interpretation",
        organization="ConnectomeChronicle demo generator",
        description="Deterministic graph for exploration, time travel and anomaly demonstrations; seed 7.",
        releases={f"r{i}": {"release_order": i, "files": []} for i in (1, 2, 3)},
    )
    (output / "catalog.yaml").write_text(
        yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8"
    )
    mapping = (
        (fixture / "schema_mappings" / "toyfly.yaml")
        .read_text()
        .replace("dataset: toyfly", "dataset: chronicle_demo")
    )
    (output / "schema_mappings" / "chronicle_demo.yaml").write_text(
        mapping, encoding="utf-8"
    )
    rng = random.Random(7)
    base = {}
    for neuron in range(1001, 1061):
        group = (neuron - 1001) // 10
        for step in (1, 2, 3, 4):
            partner = 1001 + group * 10 + (neuron - 1001 + step) % 10
            base[neuron, partner] = rng.randint(5, 30)
        partner = 1001 + (neuron - 1001 + 17) % 60
        base[neuron, partner] = rng.randint(2, 10)
    for release in (1, 2, 3):
        directory = output / "raw" / "chronicle_demo" / f"r{release}"
        directory.mkdir(parents=True, exist_ok=True)
        ids = set(range(1001, 1061))
        if release == 2:
            ids.remove(1059)
        if release >= 2:
            ids.add(1061)
        if release == 3:
            ids.add(1062)
        neurons = []
        for n in sorted(ids):
            cell_type = f"Demo-{chr(65 + (n - 1001) // 10)}"
            if n == 1012 and release >= 2:
                cell_type = "Demo-retyped"
            neurons.append([n, cell_type])
        edges = {}
        for i, ((a, b), weight) in enumerate(sorted(base.items())):
            if a not in ids or b not in ids or (release >= 2 and i % 37 == 0):
                continue
            if release >= 2 and i % 4 == 0:
                weight = max(0, weight + (2 if i % 2 == 0 else -2))
            if release >= 2 and i % 41 == 0:
                weight += 250
            if release == 3 and i % 19 == 0:
                weight += 7
            edges[a, b] = weight
        if release >= 2:
            for n in range(1001, 1010):
                edges[1061, n] = 8 + (n % 5)
        if release == 3:
            for n in range(1031, 1037):
                edges[n, 1062] = 12
        for filename, header, rows in [
            ("neurons.csv", ["id", "type"], neurons),
            (
                "edges.csv",
                ["pre", "post", "w"],
                [[a, b, w] for (a, b), w in sorted(edges.items())],
            ),
            (
                "edges_roi.csv",
                ["pre", "post", "roi", "w"],
                [
                    [a, b, ["SLP(R)", "AL(R)", "ME(L)", "EB", "FB"][a % 5], w]
                    for (a, b), w in sorted(edges.items())
                ],
            ),
        ]:
            with (directory / filename).open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(header)
                writer.writerows(rows)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="connectome_demo")
    parser.add_argument("--mongo-database", default="connectome_meta_demo")
    args = parser.parse_args()
    if not args.database.endswith(
        ("_demo", "_review")
    ) or not args.mongo_database.endswith(("_demo", "_review")):
        parser.error(
            "Demo database names must end in _demo or _review; public databases are not seeded"
        )
    output = generate_demo()
    os.environ.update(
        POSTGRES_DB=args.database,
        MONGO_DB=args.mongo_database,
        CC_CATALOG=str(output / "catalog.yaml"),
        CC_RAW_DIR=str(output / "raw"),
        CC_SCHEMA_MAPPING_DIR=str(output / "schema_mappings"),
    )
    import psycopg
    from psycopg import sql
    from backend.config import get_settings
    from backend.db import connect
    from backend.etl.loader import ingest_release
    from backend.etl.regions import load_regions
    from backend.metadata import sync_release, check_consistency
    from backend.distributed.partition import compute
    from backend.distributed.deploy import deploy
    from backend.workspace import db_connection
    from backend.main import create_diff, DiffRequest
    from backend.analysis import score_diff, score_mappings

    settings = get_settings()
    for port in [settings.coordinator_port, *settings.shard_ports]:
        with psycopg.connect(
            settings.pg_dsn(port=port, db="postgres"), autocommit=True
        ) as conn:
            if not conn.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (args.database,)
            ).fetchone():
                conn.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.database))
                )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True
    )
    for dataset, labels in [
        ("chronicle_demo", ["r1", "r2", "r3"]),
        ("toyfly", ["r1", "r2", "r3"]),
        ("toyskip", ["r1"]),
        ("hemibrain", ["t1"]),
        ("flywire", ["t1"]),
    ]:
        for label in labels:
            result = ingest_release(dataset, label)
            if dataset == "chronicle_demo" or (dataset == "toyfly" and label != "r3"):
                load_regions(dataset, label)
            sync_release(dataset, label)
            print(f"{dataset} {label}: {result.status}", flush=True)
        report = check_consistency(dataset)
        if not report["consistent"]:
            raise RuntimeError(report)
        with connect() as conn:
            conn.execute("SELECT assign_source_identity(%s)", (dataset,))
    with connect() as conn:
        conn.execute("SELECT build_type_crosswalk()")
        fw = conn.execute(
            "SELECT release_id FROM release JOIN dataset USING(dataset_id) WHERE code='flywire' AND version_label='t1'"
        ).fetchone()[0]
        hb = conn.execute(
            "SELECT release_id FROM release JOIN dataset USING(dataset_id) WHERE code='hemibrain' AND version_label='t1'"
        ).fetchone()[0]
        conn.execute("SELECT propose_crosswalk_mappings(%s,%s)", (fw, hb))
    for strategy in ("hash", "anatomical", "community"):
        compute(strategy)
        result = deploy(strategy)
        print(f"{strategy}: verified generation {result['batch_id']}", flush=True)
    with db_connection() as conn:
        result = create_diff(
            DiffRequest(dataset="chronicle_demo", release_a="r1", release_b="r2"), conn
        )
    print(score_diff(result["diff_run_id"]), flush=True)
    print(score_mappings(), flush=True)
    print(
        f"Demo ready: PostgreSQL {args.database}, MongoDB {args.mongo_database}",
        flush=True,
    )


if __name__ == "__main__":
    main()
