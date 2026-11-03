"""Interactive workspace, model evidence, metadata integrity and deployments."""

import sys
from pathlib import Path
from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    run_sql_file("0011_workspace.sql")


def downgrade():
    op.execute("""ALTER TABLE cc.shard_deployment DROP COLUMN batch_id;
        DROP TABLE cc.active_deployment, cc.deployment_batch, cc.workspace_job, cc.metadata_record_digest;
        ALTER TABLE cc.anomaly_result DROP COLUMN features, DROP COLUMN explanation, DROP COLUMN baseline_flagged;
        ALTER TABLE cc.ml_model_run DROP COLUMN metrics;
        ALTER TABLE cc.neuron_mapping DROP COLUMN score_details""")
