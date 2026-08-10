-- =============================================================================
-- Cluster bootstrap — run ONCE per cluster, as superuser, CONNECTED TO tenant_db.
--
--   createdb tenant_db     # or CREATE DATABASE tenant_db;
--   psql -d tenant_db -v ON_ERROR_STOP=1 \
--        -v migrate_pw="$TENANT_MIGRATE_PASSWORD" \
--        -v app_pw="$APP_LOGIN_PASSWORD" \
--        -f bootstrap/00_cluster.sql
--
-- Everything after this runs as tenant_migrate (DDL) or app_login (runtime).
-- No application ever connects as superuser.
--
-- ARCHITECTURE.md 5.2 — the two load-bearing settings here:
--   * app_login NOINHERIT : holds membership of every tenant role but NO privilege
--                           until it explicitly SET LOCAL ROLE. Fail-closed.
--   * search_path = ''     : an unqualified table reference raises instead of
--                           silently resolving. Kills the psycopg3 prepared-
--                           statement / search_path hazard (ARCHITECTURE.md 5.4)
--                           structurally rather than by convention.
-- =============================================================================

\set ON_ERROR_STOP on

-- psql interpolates :'var' here (plain statement, not inside a quoted region).
-- It would NOT interpolate inside a $$...$$ block, so the passwords are staged
-- into session GUCs and read back with current_setting() by the DO blocks below.
SELECT set_config('bootstrap.migrate_pw', :'migrate_pw', false);
SELECT set_config('bootstrap.app_pw',     :'app_pw',     false);

-- --- Roles ------------------------------------------------------------------

-- Owns all schemas and performs DDL. CREATEROLE is required because provisioning
-- a tenant creates that tenant's roles. In PG16 CREATEROLE is scoped: the holder
-- may only administer roles it created and cannot grant superuser. Marginal risk
-- is low — a compromised tenant_migrate already owns every tenant schema.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_migrate') THEN
        EXECUTE format('ALTER ROLE tenant_migrate PASSWORD %L',
                       current_setting('bootstrap.migrate_pw'));
    ELSE
        EXECUTE format('CREATE ROLE tenant_migrate LOGIN CREATEROLE NOBYPASSRLS PASSWORD %L',
                       current_setting('bootstrap.migrate_pw'));
    END IF;
END $$;

-- The single runtime login. Owns nothing, inherits nothing.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_login') THEN
        EXECUTE format('ALTER ROLE app_login NOINHERIT NOBYPASSRLS PASSWORD %L',
                       current_setting('bootstrap.app_pw'));
    ELSE
        EXECUTE format('CREATE ROLE app_login LOGIN NOINHERIT NOBYPASSRLS PASSWORD %L',
                       current_setting('bootstrap.app_pw'));
    END IF;
END $$;

SELECT set_config('bootstrap.migrate_pw', '', false);
SELECT set_config('bootstrap.app_pw',     '', false);

-- --- Database ---------------------------------------------------------------

ALTER DATABASE tenant_db OWNER TO tenant_migrate;
REVOKE ALL ON DATABASE tenant_db FROM PUBLIC;
GRANT CONNECT ON DATABASE tenant_db TO app_login, tenant_migrate;

-- Nothing lives in public, ever. Its default CREATE grant to PUBLIC is exactly
-- the kind of ambient privilege this design exists to remove.
DROP SCHEMA IF EXISTS public CASCADE;

-- --- Session defaults -------------------------------------------------------

ALTER ROLE app_login      SET search_path = '';
ALTER ROLE tenant_migrate SET search_path = '';

-- A runaway tenant query must not hold a pooled backend hostage.
ALTER ROLE app_login SET statement_timeout = '120s';
ALTER ROLE app_login SET idle_in_transaction_session_timeout = '60s';
