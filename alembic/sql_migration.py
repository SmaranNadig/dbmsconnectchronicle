"""Helper used by revisions to execute a SQL file from backend/db/sql."""
from pathlib import Path

from alembic import op

SQL_DIR = Path(__file__).resolve().parent.parent / "backend" / "db" / "sql"


def run_sql_file(name: str) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    # executed on the raw psycopg connection with no parameters, so '%' in
    # RAISE messages, $$-quoted function bodies and ::casts reach PostgreSQL verbatim
    raw = op.get_bind().connection.driver_connection
    raw.execute(sql)
    raw.execute("SET search_path = public")
