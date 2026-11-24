"""Versioned odor learning, branches and checkpoint replay."""

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file

revision = "0017"
down_revision = "0016"
branch_labels = depends_on = None


def upgrade():
    run_sql_file("0017_memory.sql")
    op.execute("COMMENT ON TABLE cc.memory_snapshot IS 'A checksummed olfactory pathway in one release.'")
    op.execute(
        "COMMENT ON TABLE cc.memory_edge IS 'One regional source row linked to its versioned connection.'"
    )
    op.execute(
        "COMMENT ON TABLE cc.memory_run IS 'One versioned odor protocol and its visual replay reference.'"
    )
    op.execute(
        "COMMENT ON TABLE cc.memory_branch IS 'A learning intervention starting from a saved parent trial.'"
    )
    op.execute(
        "COMMENT ON TABLE cc.memory_trial IS 'An ordered encounter and its post-encounter state digest.'"
    )
    op.execute(
        "COMMENT ON TABLE cc.memory_change IS 'The new effective gain for one changed anatomical edge.'"
    )
    op.execute(
        "COMMENT ON TABLE cc.memory_checkpoint IS 'A complete model state at a committed encounter.'"
    )


def downgrade():
    op.execute("DROP VIEW cc.memory_divergence, cc.memory_preference")
    op.execute(
        "DROP TABLE cc.memory_change, cc.memory_checkpoint, cc.memory_trial, cc.memory_branch, cc.memory_run, cc.memory_edge, cc.memory_snapshot"
    )
