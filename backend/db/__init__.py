"""PostgreSQL access. Raw SQL through psycopg 3; search_path is set to cc."""
from __future__ import annotations

import psycopg

from backend.config import get_settings


def connect(port: int | None = None, autocommit: bool = False) -> psycopg.Connection:
    dsn = get_settings().pg_dsn(port=port)
    return psycopg.connect(dsn, autocommit=autocommit, options="-c search_path=cc,public")
