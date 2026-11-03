"""entity resolution: source identity, type crosswalk, candidate mappings, review

Revision ID: 0004
Revises: 0003
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file  # noqa: E402

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0004_entity_resolution.sql")


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION cc.review_mapping(bigint, text, text);
        DROP FUNCTION cc.propose_crosswalk_mappings(integer, integer, integer);
        DROP FUNCTION cc.build_type_crosswalk();
        DROP FUNCTION cc.assign_source_identity(text);
        DROP INDEX cc.canonical_neuron_label_key;
    """)
