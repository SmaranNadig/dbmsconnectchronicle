"""ConnectomeChronicle command line.

    python -m backend.cli inspect  --dataset hemibrain --release v1.2
    python -m backend.cli ingest   --dataset hemibrain --release v1.0
    python -m backend.cli status
    python -m backend.cli diff     --dataset hemibrain --from v1.1 --to v1.2
    python -m backend.cli neuron   --dataset hemibrain --id 200326126
    python -m backend.cli metadata sync  --dataset flywire --release 783
    python -m backend.cli metadata check --dataset flywire
    python -m backend.cli regions  --dataset hemibrain --release v1.2
    python -m backend.cli partition --strategy community     # compute shard owners
    python -m backend.cli deploy    --strategy community     # copy to the shards and verify
    python -m backend.cli bench                               # routed queries vs coordinator
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from backend.db import connect
from backend.etl.adapters import ValidationError, get_adapter
from backend.etl.loader import ingest_release


def _print_rows(cur) -> None:
    cols = [d.name for d in cur.description]
    rows = cur.fetchall()
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print("  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))


def cmd_inspect(a) -> int:
    print(json.dumps(get_adapter(a.dataset, a.release, a.path).inspect_schema(), indent=2))
    return 0


def cmd_ingest(a) -> int:
    res = ingest_release(a.dataset, a.release, a.path)
    print(f"{res.dataset} {res.release}: {res.status} (run {res.run_id})")
    if res.stats:
        print(json.dumps(res.stats, indent=2))
    return 0


def cmd_status(a) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT dataset, version_label, release_order, ingestion_status, neurons, connections, loaded_at
                       FROM release_overview ORDER BY dataset, release_order""")
        _print_rows(cur)
        print()
        cur.execute("""SELECT r.run_id, d.code, rl.version_label, r.status, r.started_at::timestamp(0),
                              r.finished_at - r.started_at AS duration, left(r.error_message, 80) AS error
                       FROM ingestion_run r JOIN release rl USING (release_id) JOIN dataset d USING (dataset_id)
                       ORDER BY r.run_id""")
        _print_rows(cur)
    return 0


def _release_id(cur, dataset: str, label: str) -> int:
    cur.execute("""SELECT release_id FROM release JOIN dataset USING (dataset_id)
                   WHERE code = %s AND version_label = %s""", (dataset, label))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"unknown release {dataset} {label}")
    return row[0]


def cmd_diff(a) -> int:
    with connect() as conn, conn.cursor() as cur:
        ra, rb = _release_id(cur, a.dataset, a.from_), _release_id(cur, a.dataset, a.to)
        cur.execute("SELECT run_temporal_diff(%s, %s, coalesce(%s::timestamptz, now()))", (ra, rb, a.as_of))
        run = cur.fetchone()[0]
        conn.commit()
        cur.execute("SELECT diff_run_id, as_of, finished_at - started_at AS duration, stats FROM diff_run "
                    "WHERE diff_run_id = %s", (run,))
        _print_rows(cur)
    return 0


def cmd_neuron(a) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT find_neuron(%s, %s)", (a.dataset, a.id))
        nid = cur.fetchone()[0]
        if nid is None:
            raise SystemExit("neuron not found")
        cur.execute("""SELECT r.version_label, n.present, n.annotations
                       FROM release r JOIN dataset d USING (dataset_id),
                            LATERAL neuron_at_release(%s, r.release_id) n
                       WHERE d.code = %s AND r.ingestion_status = 'loaded' ORDER BY r.release_order""",
                    (nid, a.dataset))
        _print_rows(cur)
        print()
        cur.execute("SELECT * FROM annotation_history(%s)", (nid,))
        _print_rows(cur)
    return 0


def cmd_metadata(a) -> int:
    from backend import metadata
    if a.action == "sync":
        if not a.release:
            raise SystemExit("metadata sync needs --release")
        print(json.dumps(metadata.sync_release(a.dataset, a.release), indent=2, default=str))
        return 0
    report = metadata.check_consistency(a.dataset)
    print(json.dumps(report, indent=2))
    return 0 if report["consistent"] else 1


def cmd_regions(a) -> int:
    from backend.etl.regions import load_regions
    print(json.dumps(load_regions(a.dataset, a.release), indent=2))
    return 0


def cmd_partition(a) -> int:
    from backend.distributed.partition import compute
    print(json.dumps(compute(a.strategy), indent=2))
    return 0


def cmd_deploy(a) -> int:
    from backend.distributed.deploy import deploy
    print(json.dumps(deploy(a.strategy), indent=2, default=str))
    return 0


def cmd_bench(a) -> int:
    from backend.distributed.bench import run
    res = run(neurons=a.neurons, repeat=a.repeat)
    for kind, per in res["results"].items():
        for target, r in per.items():
            print(f"{kind:16} {target:12} p50 {r['p50_ms']:9.2f} ms  p95 {r['p95_ms']:9.2f} ms  "
                  f"shards {r['mean_shards_touched']:4}  rows {r['mean_rows']}")
    print(f"correctness: {res['correctness']}  ->  {res['file']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(prog="backend.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("inspect", "ingest", "validate", "regions"):
        p = sub.add_parser(name)
        p.add_argument("--dataset", required=True)
        p.add_argument("--release", required=True)
        p.add_argument("--path", type=Path, default=None, help="release directory (default data/raw/<ds>/<rel>)")
    sub.add_parser("status")
    p = sub.add_parser("diff")
    p.add_argument("--dataset", required=True)
    p.add_argument("--from", dest="from_", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--as-of", default=None, help="transaction time to read both releases at")
    p = sub.add_parser("neuron")
    p.add_argument("--dataset", required=True)
    p.add_argument("--id", required=True, help="original source identifier")
    p = sub.add_parser("metadata", help="MongoDB source records")
    p.add_argument("action", choices=["sync", "check"])
    p.add_argument("--dataset", required=True)
    p.add_argument("--release", default=None)
    for name in ("partition", "deploy"):
        p = sub.add_parser(name)
        p.add_argument("--strategy", required=True, choices=["hash", "anatomical", "community"])
    p = sub.add_parser("bench")
    p.add_argument("--neurons", type=int, default=25, help="sampled neurons per dataset")
    p.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args(argv)
    handlers = {"inspect": cmd_inspect, "validate": cmd_inspect, "ingest": cmd_ingest, "status": cmd_status,
                "diff": cmd_diff, "neuron": cmd_neuron, "metadata": cmd_metadata, "regions": cmd_regions,
                "partition": cmd_partition, "deploy": cmd_deploy, "bench": cmd_bench}
    try:
        return handlers[a.cmd](a)
    except ValidationError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
