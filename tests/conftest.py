"""Test setup: throw-away databases (`connectome_test` on the coordinator,
`connectome_meta_test` in MongoDB) and the synthetic fixture catalog. The real
`connectome` and `connectome_meta` databases are never touched."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "toy"
TEST_DB = "connectome_test"
TEST_MONGO_DB = "connectome_meta_test"

# must be set before backend.config is imported anywhere
os.environ["POSTGRES_DB"] = TEST_DB
os.environ["MONGO_DB"] = TEST_MONGO_DB
os.environ["CC_CATALOG"] = str(FIX / "catalog.yaml")
os.environ["CC_SCHEMA_MAPPING_DIR"] = str(FIX / "schema_mappings")
os.environ["CC_RAW_DIR"] = str(FIX / "raw")
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
import pytest  # noqa: E402

from backend.config import get_settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def test_database():
    s = get_settings()
    with psycopg.connect(s.pg_dsn(db="postgres"), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    for port in s.shard_ports:       # shard schemas of the tests live in their own database too
        with psycopg.connect(s.pg_dsn(port=port, db="postgres"), autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {TEST_DB}")
    from backend.metadata import mongo_db
    mdb = mongo_db()
    assert mdb.name == TEST_MONGO_DB, f"refusing to drop MongoDB database {mdb.name}"
    mdb.client.drop_database(TEST_MONGO_DB)
    mdb.client.drop_database(f"{TEST_MONGO_DB}_lab")    # the Data editor's copy (backend/lab.py)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True,
                   env={**os.environ}, capture_output=True)
    yield


@pytest.fixture
def db():
    from backend.db import connect
    with connect() as conn:
        yield conn
        conn.rollback()


@pytest.fixture(scope="session")
def toyfly_loaded(test_database):
    """Load r1..r3 once; record loaded_at of each release (transaction time)."""
    from backend.db import connect
    from backend.etl.loader import ingest_release
    for r in ("r1", "r2", "r3"):
        assert ingest_release("toyfly", r).status == "loaded"
    with connect() as conn:
        rows = conn.execute("""SELECT version_label, release_id, loaded_at FROM release
                               JOIN dataset USING (dataset_id) WHERE code = 'toyfly'""").fetchall()
    return {label: {"id": rid, "loaded_at": ts} for label, rid, ts in rows}


@pytest.fixture(scope="session")
def toyskip_loaded(test_database):
    """Result of the first (and only) load of the toyskip fixture."""
    from backend.etl.loader import ingest_release
    return ingest_release("toyskip", "r1")
