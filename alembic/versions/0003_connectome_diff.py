"""ConnectomeDiff: set-based temporal diff function and detail view

Revision ID: 0003
Revises: 0002
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0003_connectome_diff.sql")


def downgrade() -> None:
    op.execute("""
        DROP VIEW cc.diff_event_detail;
        DROP FUNCTION cc.run_temporal_diff(integer, integer, timestamptz);
    """)
