"""cc_reader: read-only role for the SQL console

Revision ID: 0010
Revises: 0009
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0010_query_console_role.sql")


def downgrade() -> None:
    op.execute("""GRANT EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) TO PUBLIC;
        ALTER DEFAULT PRIVILEGES IN SCHEMA cc, er REVOKE SELECT ON TABLES FROM cc_reader;
        REVOKE ALL ON ALL TABLES IN SCHEMA cc, er FROM cc_reader;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cc, er FROM cc_reader;
        REVOKE USAGE ON SCHEMA cc, er FROM cc_reader""")
    # the role itself is cluster-wide and may still be used by another database
