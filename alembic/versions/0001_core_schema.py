"""core relational schema: sources, provenance, bitemporal facts, mappings, diff, partitions, ML

Revision ID: 0001
Revises:
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0001_core_schema.sql")


def downgrade() -> None:
    op.execute("DROP SCHEMA cc CASCADE")
