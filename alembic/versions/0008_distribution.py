"""distribution: shards, strategies, deployments, partition quality

Revision ID: 0008
Revises: 0007
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0008_distribution.sql")


def downgrade() -> None:
    op.execute("DROP FUNCTION cc.partition_quality(text); DROP VIEW cc.neuron_latest_region; DROP TABLE cc.shard_deployment; ALTER TABLE cc.partition_assignment DROP COLUMN group_key; DROP INDEX cc.partition_assignment_shard_idx; DELETE FROM cc.partition_assignment; DELETE FROM cc.partition_strategy; DELETE FROM cc.shard")
