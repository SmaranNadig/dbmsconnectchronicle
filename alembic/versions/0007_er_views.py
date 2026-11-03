"""er schema: the ER diagram as views over the physical tables

Revision ID: 0007
Revises: 0006
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0007_er_views.sql")


def downgrade() -> None:
    op.execute("DROP SCHEMA er CASCADE; DROP INDEX cc.neuron_region_synapses_neuron_idx; ALTER TABLE cc.release DROP COLUMN description; ALTER TABLE cc.dataset DROP COLUMN organization, DROP COLUMN repository, DROP COLUMN data_formats")
