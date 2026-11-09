"""cc_tryout and cc_lab: write targets of the dashboard

Revision ID: 0012
Revises: 0011
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from backend.lab import lab_password  # noqa: E402

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0012_lab_roles.sql")
    # hex digest, so it can be embedded without quoting issues
    op.execute(f"ALTER ROLE cc_lab PASSWORD '{lab_password()}'")


def downgrade() -> None:
    op.execute("""DROP SCHEMA IF EXISTS lab CASCADE;
        ALTER DEFAULT PRIVILEGES IN SCHEMA cc REVOKE ALL ON TABLES FROM cc_tryout;
        ALTER DEFAULT PRIVILEGES IN SCHEMA cc REVOKE ALL ON SEQUENCES FROM cc_tryout;
        ALTER DEFAULT PRIVILEGES IN SCHEMA cc, er REVOKE ALL ON TABLES FROM cc_lab;
        REVOKE ALL ON ALL TABLES IN SCHEMA cc FROM cc_tryout;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA cc FROM cc_tryout;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cc FROM cc_tryout;
        REVOKE USAGE ON SCHEMA cc FROM cc_tryout;
        REVOKE ALL ON ALL TABLES IN SCHEMA cc, er FROM cc_lab;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cc, er FROM cc_lab;
        REVOKE USAGE ON SCHEMA cc, er FROM cc_lab""")
    # the roles themselves are cluster-wide and may still be used by another database
