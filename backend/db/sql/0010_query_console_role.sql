-- Read-only role for the dashboard's SQL console.
--
-- The console runs every statement as cc_reader, inside a READ ONLY
-- transaction with a statement timeout, on a connection that is closed
-- afterwards. cc_reader may read every table, view and function of the
-- project but owns nothing and cannot write, so even a statement that slips
-- past the API's checks cannot change data.
--
-- Roles are cluster-wide (shared by the real and the test database), so the
-- role is created only if it does not exist yet; the grants are per database.
SET search_path = cc, public;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cc_reader') THEN
        CREATE ROLE cc_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END $$;

GRANT USAGE ON SCHEMA cc, er TO cc_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA cc, er TO cc_reader;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA cc, er TO cc_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA cc, er GRANT SELECT ON TABLES TO cc_reader;

-- set_config('role', ...) would let a console statement switch back to the
-- connection's owner role; only superusers keep it.
REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC;
