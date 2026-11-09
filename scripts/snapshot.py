"""Copy a fully loaded ConnectomeChronicle database to another machine.

Loading the public releases from scratch takes a long time. This exports the
coordinator database (pg_dump) and the MongoDB metadata database (mongodump)
into ./snapshot/, with a manifest of checksums and exact row counts, and
imports them on another machine, checking the counts afterwards.

    python scripts/snapshot.py export
        -> snapshot/connectome.dump, snapshot/connectome_meta.archive.gz, snapshot/snapshot.json

    # on the other machine, after `docker compose up -d --wait`, with snapshot/ copied in:
    python scripts/snapshot.py import --with-shards

`import` REPLACES the PostgreSQL and MongoDB databases named in .env. The
shards are not exported: their content is fully determined by the coordinator
(partition assignments are part of the dump), so --with-shards re-deploys and
verifies them (about half a minute per strategy).

The dump tools run inside the containers (cc_coordinator, cc_mongodb), so
nothing besides Docker and this project's venv is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.metadata import COLLECTION  # noqa: E402

PG_CONTAINER = "cc_coordinator"
MONGO_CONTAINER = "cc_mongodb"
OUT = ROOT / "snapshot"
PG_FILE = "connectome.dump"
MONGO_FILE = "connectome_meta.archive.gz"
ROLE_SQL = [ROOT / "backend" / "db" / "sql" / f for f in ("0010_query_console_role.sql", "0012_lab_roles.sql")]
STRATEGIES = ("hash", "anatomical", "community")


def docker() -> str:
    found = shutil.which("docker")
    if found:
        return found
    # Docker Desktop on Windows does not always put docker on PATH for Git Bash
    for candidate in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe",
                      Path(os.environ.get("ProgramFiles", "")) / "Docker/Docker/resources/bin/docker.exe"):
        if candidate.exists():
            return str(candidate)
    sys.exit("docker not found: start Docker Desktop and make sure `docker` is on PATH")


def run(*args: str) -> None:
    r = subprocess.run([docker(), *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"failed: docker {' '.join(a for a in args if 'password' not in a.lower())}\n{r.stderr.strip()}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def counts(pg_db: str, mongo_db: str) -> dict:
    """Exact row count of every table in schema cc, the schema revision and the MongoDB document count."""
    s = get_settings()
    with psycopg.connect(s.pg_dsn(db=pg_db)) as conn:
        tables = [r[0] for r in conn.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                                WHERE n.nspname = 'cc' AND c.relkind = 'r' ORDER BY 1""")]
        rows = {t: conn.execute(f'SELECT count(*) FROM cc."{t}"').fetchone()[0] for t in tables}
        revision = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()[0]
    from pymongo import MongoClient
    with MongoClient(s.mongo_uri) as client:
        docs = client[mongo_db][COLLECTION].count_documents({})
    return {"schema_revision": revision, "postgres_rows": rows, "mongo_documents": docs}


# ------------------------------------------------------------------ export
def export(_args) -> None:
    s = get_settings()
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    print(f"counting rows in {s.postgres_db} / {s.mongo_db} ...")
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "postgres_db": s.postgres_db, "mongo_db": s.mongo_db, **counts(s.postgres_db, s.mongo_db)}

    print("pg_dump (compressed custom format) ...")
    run("exec", PG_CONTAINER, "pg_dump", "-U", s.postgres_user, "-d", s.postgres_db, "-Fc", "-Z", "6",
        "-f", f"/tmp/{PG_FILE}")
    run("cp", f"{PG_CONTAINER}:/tmp/{PG_FILE}", str(OUT / PG_FILE))
    run("exec", PG_CONTAINER, "rm", "-f", f"/tmp/{PG_FILE}")

    print("mongodump ...")
    run("exec", MONGO_CONTAINER, "mongodump", "--username", s.mongo_user, "--password", s.mongo_password,
        "--authenticationDatabase", "admin", "--db", s.mongo_db, "--gzip", f"--archive=/tmp/{MONGO_FILE}", "--quiet")
    run("cp", f"{MONGO_CONTAINER}:/tmp/{MONGO_FILE}", str(OUT / MONGO_FILE))
    run("exec", MONGO_CONTAINER, "rm", "-f", f"/tmp/{MONGO_FILE}")

    manifest["files"] = {name: {"bytes": (OUT / name).stat().st_size, "sha256": sha256(OUT / name)}
                         for name in (PG_FILE, MONGO_FILE)}
    (OUT / "snapshot.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name, f in manifest["files"].items():
        print(f"  {name}: {f['bytes'] / 2**20:,.0f} MB")
    print(f"done in {time.time() - t0:.0f} s -> {OUT}  (send the whole folder)")


# ------------------------------------------------------------------ import
def import_(args) -> None:
    s = get_settings()
    pg_db = args.pg_db or s.postgres_db
    mongo_db = args.mongo_db or s.mongo_db
    manifest = json.loads((OUT / "snapshot.json").read_text(encoding="utf-8"))
    t0 = time.time()

    print("checking file checksums ...")
    for name, f in manifest["files"].items():
        if sha256(OUT / name) != f["sha256"]:
            sys.exit(f"{name} is damaged or incomplete (checksum differs); copy it again")

    print(f"PostgreSQL: recreating database {pg_db} ...")
    with psycopg.connect(s.pg_dsn(db="postgres"), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{pg_db}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{pg_db}"')
    run("cp", str(OUT / PG_FILE), f"{PG_CONTAINER}:/tmp/{PG_FILE}")
    print("pg_restore (parallel, then indexes and constraints) ...")
    # --no-owner/--no-acl: the objects belong to whoever runs the import, so the
    # user name in .env does not have to match the exporting machine's
    r = subprocess.run([docker(), "exec", PG_CONTAINER, "pg_restore", "-U", s.postgres_user, "-d", pg_db,
                        "--no-owner", "--no-acl", "-j", "4", f"/tmp/{PG_FILE}"], capture_output=True, text=True)
    run("exec", PG_CONTAINER, "rm", "-f", f"/tmp/{PG_FILE}")
    if r.returncode != 0:
        sys.exit(f"pg_restore reported errors:\n{r.stderr.strip()[-3000:]}")
    # grants for the dashboard's console roles (skipped by --no-acl). The Data
    # editor's lab schema must belong to cc_lab, so it is dropped here and
    # rebuilt from the restored data on first use (backend/lab.py).
    from backend.lab import lab_password
    with psycopg.connect(s.pg_dsn(db=pg_db), autocommit=True) as conn:
        for f in ROLE_SQL:
            conn.execute(f.read_text(encoding="utf-8"))
        conn.execute(f"ALTER ROLE cc_lab PASSWORD '{lab_password()}'")
        conn.execute("DROP SCHEMA IF EXISTS lab CASCADE")

    print(f"MongoDB: restoring into {mongo_db} ...")
    run("cp", str(OUT / MONGO_FILE), f"{MONGO_CONTAINER}:/tmp/{MONGO_FILE}")
    run("exec", MONGO_CONTAINER, "mongorestore", "--username", s.mongo_user, "--password", s.mongo_password,
        "--authenticationDatabase", "admin", "--drop", "--gzip", f"--archive=/tmp/{MONGO_FILE}",
        f"--nsFrom={manifest['mongo_db']}.*", f"--nsTo={mongo_db}.*", "--quiet")
    run("exec", MONGO_CONTAINER, "rm", "-f", f"/tmp/{MONGO_FILE}")

    print("comparing row counts with the exporting machine ...")
    got = counts(pg_db, mongo_db)
    bad = [f"{t}: expected {n:,}, got {got['postgres_rows'].get(t)}"
           for t, n in manifest["postgres_rows"].items() if got["postgres_rows"].get(t) != n]
    if got["mongo_documents"] != manifest["mongo_documents"]:
        bad.append(f"MongoDB documents: expected {manifest['mongo_documents']:,}, got {got['mongo_documents']:,}")
    if got["schema_revision"] != manifest["schema_revision"]:
        bad.append(f"schema revision: expected {manifest['schema_revision']}, got {got['schema_revision']}")
    if bad:
        sys.exit("restored data does not match:\n  " + "\n  ".join(bad))
    print(f"  {len(manifest['postgres_rows'])} tables, {sum(manifest['postgres_rows'].values()):,} rows and "
          f"{manifest['mongo_documents']:,} documents match (schema revision {got['schema_revision']})")

    if args.with_shards:
        env = {**os.environ, "POSTGRES_DB": pg_db}
        for strategy in STRATEGIES:
            print(f"deploying strategy {strategy} to the 3 shards ...")
            r = subprocess.run([sys.executable, "-m", "backend.cli", "deploy", "--strategy", strategy],
                               cwd=ROOT, env=env)
            if r.returncode != 0:
                sys.exit(f"deploy {strategy} failed")
    else:
        print("shards not deployed; run with --with-shards, or: python -m backend.cli deploy --strategy <name>")
    print(f"done in {time.time() - t0:.0f} s")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export", help="write snapshot/ from the databases named in .env").set_defaults(fn=export)
    imp = sub.add_parser("import", help="replace the databases named in .env with snapshot/")
    imp.add_argument("--with-shards", action="store_true", help="also deploy and verify the 3 sharding strategies")
    imp.add_argument("--pg-db", help="restore into this PostgreSQL database instead (e.g. for a trial run)")
    imp.add_argument("--mongo-db", help="restore into this MongoDB database instead")
    imp.set_defaults(fn=import_)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
