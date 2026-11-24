"""Versioned visual circuit experiments."""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    run_sql_file("0015_circuit.sql")


def downgrade():
    op.execute("DROP VIEW cc.circuit_results")
    op.execute("DROP TABLE cc.circuit_trial, cc.circuit_run, cc.circuit_edge, cc.circuit_node, cc.circuit_snapshot")

