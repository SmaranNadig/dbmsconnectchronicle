"""Normalisation: decompose brain_region and ml_model_run.diff_run_ids, enforce dependencies."""

import sys
from pathlib import Path
from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    run_sql_file("0013_normalisation.sql")


def downgrade():
    op.execute("""SET search_path = cc, public;
        ALTER TABLE dataset_neuron DROP CONSTRAINT dataset_neuron_metadata_doc_id_key;
        ALTER TABLE partition_assignment DROP CONSTRAINT partition_assignment_group_one_shard;
        ALTER TABLE shard_deployment DROP CONSTRAINT shard_deployment_batch_strategy_fkey;
        ALTER TABLE active_deployment DROP CONSTRAINT IF EXISTS active_deployment_batch_strategy_fkey,
                                      DROP CONSTRAINT IF EXISTS active_deployment_batch_key;
        ALTER TABLE deployment_batch DROP CONSTRAINT deployment_batch_id_strategy_key;
        ALTER TABLE provenance DROP CONSTRAINT provenance_source_file_release_fkey,
                               DROP CONSTRAINT provenance_run_release_fkey;
        ALTER TABLE source_file DROP CONSTRAINT source_file_id_release_key;
        ALTER TABLE ingestion_run DROP CONSTRAINT ingestion_run_id_release_key;
        ALTER TABLE ml_model_run ADD COLUMN diff_run_ids bigint[] NOT NULL DEFAULT '{}';
        UPDATE ml_model_run m SET diff_run_ids = coalesce(
            (SELECT array_agg(diff_run_id ORDER BY diff_run_id) FROM ml_model_input i
             WHERE i.model_run_id = m.model_run_id), '{}');
        DROP TABLE ml_model_input;
        ALTER TABLE brain_region ADD COLUMN super_region text
            CHECK (super_region IN ('optic', 'central', 'vnc', 'other'));
        UPDATE brain_region b SET super_region = n.super_region FROM neuropil n
            WHERE n.canonical_code = b.canonical_code;
        CREATE OR REPLACE VIEW brain_region_label AS
        SELECT brain_region_id, canonical_code || coalesce('_' || hemisphere, '') AS code, name, super_region
        FROM brain_region;
        ALTER TABLE brain_region DROP CONSTRAINT brain_region_neuropil_fkey;
        DROP TABLE neuropil""")
