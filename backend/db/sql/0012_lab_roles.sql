-- Roles for the dashboard's write targets (see DATA_EDITOR_PLAN.md).
--
-- cc_tryout  "trial on the real tables": may read and write the cc tables,
--            but is only ever used through SET LOCAL ROLE inside a
--            transaction that the API always rolls back, with the statement
--            sent as a single prepared statement (so it cannot COMMIT).
-- cc_lab     "lab": a real login role (so a script cannot RESET ROLE back to
--            the administrator). It owns schema lab, which the API builds
--            from a subset of the real data, and can only read cc and er.
--            Its password is set by the migration's Python part.
--
-- Roles are cluster-wide (shared by the real and the test database), so they
-- are created only if missing; grants are per database.
SET search_path = cc, public;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cc_tryout') THEN
        CREATE ROLE cc_tryout NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cc_lab') THEN
        CREATE ROLE cc_lab LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
            CONNECTION LIMIT 8;
    END IF;
END $$;

ALTER ROLE cc_lab SET statement_timeout = '10s';
ALTER ROLE cc_lab SET search_path = lab, cc, public;

GRANT USAGE ON SCHEMA cc TO cc_tryout;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cc TO cc_tryout;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA cc TO cc_tryout;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA cc TO cc_tryout;
ALTER DEFAULT PRIVILEGES IN SCHEMA cc GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cc_tryout;
ALTER DEFAULT PRIVILEGES IN SCHEMA cc GRANT USAGE ON SEQUENCES TO cc_tryout;

GRANT USAGE ON SCHEMA cc, er TO cc_lab;
GRANT SELECT ON ALL TABLES IN SCHEMA cc, er TO cc_lab;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA cc, er TO cc_lab;
ALTER DEFAULT PRIVILEGES IN SCHEMA cc, er GRANT SELECT ON TABLES TO cc_lab;
