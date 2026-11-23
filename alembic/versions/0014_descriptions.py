"""Table and column descriptions (COMMENT ON), read by the data dictionary."""

import sys
from pathlib import Path
from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sql_migration import run_sql_file

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    run_sql_file("0014_descriptions.sql")


def downgrade():
    op.execute("""DO $$
    DECLARE r record;
    BEGIN
        FOR r IN SELECT c.oid::regclass AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'cc' AND c.relkind = 'r' LOOP
            EXECUTE format('COMMENT ON TABLE %s IS NULL', r.t);
        END LOOP;
        FOR r IN SELECT a.attrelid::regclass AS t, a.attname FROM pg_attribute a
                 JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                 JOIN pg_description d ON d.objoid = a.attrelid AND d.objsubid = a.attnum
                 WHERE n.nspname = 'cc' AND c.relkind = 'r' LOOP
            EXECUTE format('COMMENT ON COLUMN %s.%I IS NULL', r.t, r.attname);
        END LOOP;
    END $$""")
