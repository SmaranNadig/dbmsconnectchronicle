"""Alembic environment. Migrations are plain SQL files in backend/db/sql,
executed verbatim so the schema stays readable as SQL."""
from __future__ import annotations

import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import get_settings  # noqa: E402

url = get_settings().pg_dsn().replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, version_table_schema="public")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode not supported; run against a database")
run_migrations_online()
