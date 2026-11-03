"""release_stats: materialized per-release counts

Revision ID: 0009
Revises: 0008
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0009_release_stats.sql")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW cc.release_stats")
