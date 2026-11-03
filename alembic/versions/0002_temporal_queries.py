"""temporal and provenance query functions and views

Revision ID: 0002
Revises: 0001
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0002_temporal_queries.sql")


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION cc.trace_provenance(text, bigint);
        DROP FUNCTION cc.neuron_as_of(bigint, timestamptz);
        DROP FUNCTION cc.annotation_history(bigint);
        DROP FUNCTION cc.connections_at_release(bigint, integer, timestamptz, integer);
        DROP FUNCTION cc.neuron_at_release(bigint, integer, timestamptz);
        DROP FUNCTION cc.find_neuron(text, text);
        DROP VIEW cc.release_overview;
        DROP VIEW cc.connection_history;
        DROP FUNCTION cc.release_label(smallint, integer);
    """)
