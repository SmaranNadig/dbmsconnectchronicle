"""Validate frozen edge evidence and compare paired outcomes."""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    run_sql_file("0016_circuit_integrity.sql")


def downgrade():
    op.execute("DROP VIEW cc.circuit_paired_diff")
    op.execute("DROP TRIGGER circuit_edge_source ON cc.circuit_edge")
    op.execute("DROP FUNCTION cc.circuit_edge_source_check()")
    op.execute("ALTER TABLE cc.circuit_edge DROP CONSTRAINT circuit_edge_snapshot_id_connection_id_key")
    for table in ("circuit_snapshot", "circuit_node", "circuit_run"):
        op.execute(f"COMMENT ON TABLE cc.{table} IS NULL")

