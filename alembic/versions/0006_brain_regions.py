"""brain regions: shared vocabulary and per-release neuron region synapse counts

Revision ID: 0006
Revises: 0005
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0006_brain_regions.sql")


def downgrade() -> None:
    op.execute("DROP VIEW cc.brain_region_label, cc.neuron_primary_region; DROP TABLE cc.neuron_region_synapses; DELETE FROM cc.brain_region; ALTER TABLE cc.brain_region DROP COLUMN name")
