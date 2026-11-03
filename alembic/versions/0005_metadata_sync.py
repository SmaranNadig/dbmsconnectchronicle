"""metadata_sync: record of MongoDB source-record synchronisation per release

Revision ID: 0005
Revises: 0004
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0005_metadata_sync.sql")


def downgrade() -> None:
    op.execute("DROP TABLE cc.metadata_sync")
