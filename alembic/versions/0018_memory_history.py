"""Query shared protocols and enforce append-only learning history."""

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file

revision = "0018"
down_revision = "0017"
branch_labels = depends_on = None


def upgrade():
    run_sql_file("0018_memory_history.sql")


def downgrade():
    op.execute(
        """
        DROP VIEW cc.memory_reversal, cc.memory_paired_probes, cc.memory_wiring;
        DROP FUNCTION cc.memory_state_at(bigint, integer);
        DROP FUNCTION cc.memory_validate_insert() CASCADE;
        DROP FUNCTION cc.memory_append_only() CASCADE;
        ALTER TABLE cc.memory_trial DROP CONSTRAINT memory_trial_state_hash_check,
            DROP CONSTRAINT memory_trial_check;
        ALTER TABLE cc.memory_branch DROP CONSTRAINT memory_branch_check;
        ALTER TABLE cc.memory_snapshot DROP CONSTRAINT memory_snapshot_digest_check;
        ALTER TABLE cc.memory_run ADD COLUMN protocol jsonb;
        UPDATE cc.memory_run r SET protocol = jsonb_build_object(
            'channels', r.channels,
            'odors', (SELECT jsonb_agg(s.activity ORDER BY s.odor)
                      FROM cc.memory_stimulus s WHERE s.run_id = r.run_id),
            'events', (SELECT jsonb_agg(jsonb_build_object(
                       'phase', s.phase, 'presented', s.presented, 'left', s.left_position,
                       'unpaired', s.unpaired, 'draw', s.draw, 'noise', s.noise) ORDER BY s.step)
                       FROM cc.memory_schedule s WHERE s.run_id = r.run_id)
        );
        ALTER TABLE cc.memory_run ALTER COLUMN protocol SET NOT NULL;
        ALTER TABLE cc.memory_run DROP COLUMN channels;
        DROP TABLE cc.memory_schedule, cc.memory_stimulus;
        COMMENT ON TABLE cc.memory_run IS 'One versioned odor protocol and its visual replay reference.';
    """
    )
