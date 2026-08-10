-- =============================================================================
-- Shared migration 001 — the app schema: tenant registry, the boundary guard,
-- and the declarative grant matrix.
--
-- Runs ONCE per cluster, as tenant_migrate. Not templated.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION tenant_migrate;

-- Group role for shared reference data. Every tenant role is a member.
--
-- Why a group rather than granting platform_ref directly per tenant: the fan-out
-- runner migrates tenants CONCURRENTLY, and N workers each issuing
-- "GRANT ... ON SCHEMA platform_ref" contend on the same pg_namespace ACL row.
-- Postgres raises "tuple concurrently updated" and the migration fails for an
-- arbitrary subset of tenants. Granting once to a group, and adding tenant roles
-- to the group at provisioning, means per-tenant migration never touches a
-- shared catalogue row.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_ref_reader') THEN
        CREATE ROLE platform_ref_reader NOLOGIN NOBYPASSRLS;
    END IF;
END $$;

-- Every role needs to reach app.assert_schema_owner(). The schema holds nothing
-- else that is world-readable: tenant_registry is protected by table grants
-- (default = none), so USAGE here discloses nothing.
GRANT USAGE ON SCHEMA app TO PUBLIC;


-- --- Tenant registry --------------------------------------------------------
-- Phase 1 keeps this in tenant_db so the migration runner can enumerate schemas
-- without a second connection. ARCHITECTURE.md 4 places the authoritative copy
-- in platform-db; when that exists this becomes a replicated read model.

CREATE TABLE IF NOT EXISTS app.tenant_registry (
    slug            text PRIMARY KEY
                        CHECK (slug ~ '^[a-z][a-z0-9_]{2,31}$'),
    tenant_id       uuid NOT NULL UNIQUE,
    status          text NOT NULL DEFAULT 'PROVISIONING'
                        CHECK (status IN ('PROVISIONING', 'ACTIVE', 'SUSPENDED', 'DEPROVISIONED')),
    bucket          text NOT NULL,
    provisioned_at  timestamptz NOT NULL DEFAULT now(),
    activated_at    timestamptz
);

COMMENT ON TABLE app.tenant_registry IS
    'One row per tenant. Not readable by tenant roles: the list of tenants is '
    'itself disclosure. Read by the migration runner and provisioning only.';

REVOKE ALL ON app.tenant_registry FROM PUBLIC;


-- --- Migration bookkeeping (shared chain) -----------------------------------

CREATE TABLE IF NOT EXISTS app.shared_migration_version (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    checksum    text NOT NULL
);

REVOKE ALL ON app.shared_migration_version FROM PUBLIC;


-- =============================================================================
-- app.assert_schema_owner — the boundary guard (ARCHITECTURE.md 5.3)
--
-- Grants stop a deliberate wrong-schema query. They do NOT stop a provisioning
-- mistake: if t_acme_recon were accidentally granted USAGE on t_globex_silver,
-- the grant layer is silent. This guard catches that.
--
-- It is non-circular by construction. The __schema_identity row is written at
-- provisioning time, independently of the code path that later reads it. So if
-- application code is pointed at globex's schema while intending acme, globex's
-- row says 'globex' and the transaction aborts BEFORE any data is read.
--
-- SECURITY INVOKER (the default) on purpose. If the caller cannot read the
-- identity table, that is itself a correct refusal — the query would have been
-- denied anyway. SECURITY DEFINER here would let the function read schemas the
-- caller cannot, trading a clear failure for a confusing one.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.assert_schema_owner(
    p_schema        text,
    p_expected_slug text
) RETURNS void
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_slug text;
BEGIN
    -- %I quotes the identifier, so p_schema cannot inject.
    EXECUTE format('SELECT tenant_slug FROM %I.__schema_identity', p_schema)
        INTO v_slug;

    IF v_slug IS NULL THEN
        RAISE EXCEPTION
            'tenant boundary violation: schema % has no identity row', p_schema
            USING ERRCODE = '42501';
    END IF;

    IF v_slug <> p_expected_slug THEN
        RAISE EXCEPTION
            'tenant boundary violation: schema % belongs to %, caller expected %',
            p_schema, v_slug, p_expected_slug
            USING ERRCODE = '42501';
    END IF;
END;
$$;

COMMENT ON FUNCTION app.assert_schema_owner(text, text) IS
    'Aborts the transaction unless the named schema self-identifies as the '
    'expected tenant. Raises SQLSTATE 42501, mapped to TenantBoundaryViolation. '
    'Highest value at a trust boundary — call it with a schema name that came '
    'from untrusted input (a Kafka manifest) and a slug from trusted context.';


-- =============================================================================
-- app.assert_tenant_context — the transaction preamble.
--
-- One round trip, two independent checks:
--
--   1. The role actually assumed belongs to the intended tenant. Catches a code
--      path that computed the right schema but SET LOCAL ROLE to the wrong
--      tenant's role — which grants alone would not stop, because that role
--      legitimately holds privileges (just on the wrong schemas).
--
--   2. The schema about to be read self-identifies as the intended tenant.
--      Catches provisioning/grant misconfiguration.
--
-- Together these bracket the two ways a transaction can end up on the wrong
-- side of the boundary while every individual grant looks correct.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.assert_tenant_context(
    p_slug   text,
    p_schema text
) RETURNS void
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_user text := current_user;
BEGIN
    IF v_user <> 't_' || p_slug || '_ingest'
       AND v_user <> 't_' || p_slug || '_recon'
       AND v_user <> 't_' || p_slug || '_support'
    THEN
        RAISE EXCEPTION
            'tenant boundary violation: role % is not a role of tenant %',
            v_user, p_slug
            USING ERRCODE = '42501';
    END IF;

    PERFORM app.assert_schema_owner(p_schema, p_slug);
END;
$$;

COMMENT ON FUNCTION app.assert_tenant_context(text, text) IS
    'Transaction preamble. Asserts the assumed role AND the target schema both '
    'belong to the intended tenant. Called by TenantScopedPool on every '
    'transaction; raises SQLSTATE 42501.';


-- =============================================================================
-- app.apply_tenant_grants — the privilege matrix, applied declaratively.
--
-- REVOKE-then-GRANT on purpose: this makes grants self-healing. A grant added
-- by hand (or by a migration that forgot the matrix) is REMOVED on the next
-- run, rather than silently supplemented. That is what lets check_isolation.py
-- treat any deviation as a failure instead of a warning.
--
-- Must be re-run after every migration that creates objects — the runner does
-- this automatically. See ARCHITECTURE.md 5.1 for the matrix this implements.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.apply_tenant_grants(p_slug text) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_bronze  text;
    v_silver  text;
    v_gold    text;
    v_ingest  text;
    v_recon   text;
    v_support text;
    v_schema  text;
    r         record;
BEGIN
    IF p_slug !~ '^[a-z][a-z0-9_]{2,31}$' THEN
        RAISE EXCEPTION 'invalid tenant slug %', p_slug USING ERRCODE = '22023';
    END IF;

    v_bronze  := 't_' || p_slug || '_bronze';
    v_silver  := 't_' || p_slug || '_silver';
    v_gold    := 't_' || p_slug || '_gold';
    v_ingest  := 't_' || p_slug || '_ingest';
    v_recon   := 't_' || p_slug || '_recon';
    v_support := 't_' || p_slug || '_support';

    ---------------------------------------------------------------------------
    -- 1. Revoke everything. Declarative baseline.
    ---------------------------------------------------------------------------
    FOREACH v_schema IN ARRAY ARRAY[v_bronze, v_silver, v_gold] LOOP
        EXECUTE format(
            'REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I, %I, %I',
            v_schema, v_ingest, v_recon, v_support);
        EXECUTE format(
            'REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I, %I, %I',
            v_schema, v_ingest, v_recon, v_support);
        EXECUTE format(
            'REVOKE ALL ON SCHEMA %I FROM %I, %I, %I',
            v_schema, v_ingest, v_recon, v_support);
    END LOOP;

    ---------------------------------------------------------------------------
    -- 2. Schema USAGE.
    --    recon gets USAGE on silver because it must reach the v1_ views that
    --    live there — but it is granted SELECT on NO silver base table, so the
    --    base tables remain unreachable. Two things must go wrong, not one.
    --    recon gets NO usage on bronze at all.
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_gold, v_recon, v_support);

    ---------------------------------------------------------------------------
    -- 3. Bronze — ingest writes, support reads.
    --
    --    Tables named __* are EXCLUDED throughout. They are infrastructure,
    --    not tenant data: __schema_identity anchors the boundary guard, and
    --    __migration_version is the runner's bookkeeping. A role that could
    --    UPDATE __schema_identity could rewrite its own tenant_slug and walk
    --    straight past app.assert_schema_owner — so no tenant role ever gets
    --    more than SELECT on it (step 7), and none gets any grant at all on
    --    __migration_version. Both stay owned by tenant_migrate.
    ---------------------------------------------------------------------------
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_bronze AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO %I',
                       v_bronze, r.relname, v_ingest);
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_bronze, r.relname, v_support);
    END LOOP;

    ---------------------------------------------------------------------------
    -- 4. Silver base tables — ingest writes. recon gets NOTHING here.
    ---------------------------------------------------------------------------
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_silver AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO %I',
                       v_silver, r.relname, v_ingest);
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_silver, r.relname, v_support);
    END LOOP;

    ---------------------------------------------------------------------------
    -- 5. Silver v1_ views — the read contract (ARCHITECTURE.md 2.4).
    --    This is the ONLY silver object recon may touch.
    ---------------------------------------------------------------------------
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_silver AND c.relkind = 'v' AND c.relname LIKE 'v1\_%'
    LOOP
        EXECUTE format('GRANT SELECT ON %I.%I TO %I, %I',
                       v_silver, r.relname, v_recon, v_support);
    END LOOP;

    ---------------------------------------------------------------------------
    -- 6. Gold — recon writes, support reads. ingest gets NOTHING here.
    ---------------------------------------------------------------------------
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_gold AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO %I',
                       v_gold, r.relname, v_recon);
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_gold, r.relname, v_support);
    END LOOP;

    ---------------------------------------------------------------------------
    -- 7. __schema_identity — SELECT only, and only for roles that actually
    --    hold USAGE on that schema (step 2).
    --
    --    Explicit because step 4 gives recon no silver base table, and without
    --    this the guard would fail with a bare permission-denied instead of a
    --    legible boundary violation. Scoped per schema rather than granted to
    --    all three roles everywhere: a grant that is currently inert because
    --    the role lacks USAGE would silently activate if USAGE were ever added.
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_gold, v_recon, v_support);

    ---------------------------------------------------------------------------
    -- 8. Sequences.
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_bronze, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_silver, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_gold, v_recon);

    -- platform_ref access is NOT granted here. It comes from membership of
    -- platform_ref_reader, conferred once at provisioning. See the note on the
    -- group role above: doing it per tenant serialises badly under concurrent
    -- fan-out.
END;
$$;


-- =============================================================================
-- app.provision_tenant_schemas — schemas, roles, identity. One transaction.
--
-- DDL is transactional in Postgres, so a failure here leaves nothing behind.
-- The caller (src/provisioning) wraps this with bucket creation and registry
-- bookkeeping, which cannot join the transaction — see PHASE1-PLAN.md 4.3 for
-- the ordering rule (bucket first: inert without schemas, whereas schemas
-- without a bucket would accept ingests that have nowhere to land).
--
-- Idempotent: safe to re-run against a partially provisioned tenant.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.provision_tenant_schemas(
    p_slug      text,
    p_tenant_id uuid
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_schema text;
    v_role   text;
    v_layer  text;
BEGIN
    IF p_slug !~ '^[a-z][a-z0-9_]{2,31}$' THEN
        RAISE EXCEPTION 'invalid tenant slug %', p_slug USING ERRCODE = '22023';
    END IF;

    -- Roles first: NOLOGIN (only ever reached via SET LOCAL ROLE from app_login)
    -- and NOBYPASSRLS (belt and braces — nothing here uses RLS, but a role that
    -- could bypass it would be a latent hole if RLS is ever added).
    FOREACH v_role IN ARRAY ARRAY[
        't_' || p_slug || '_ingest',
        't_' || p_slug || '_recon',
        't_' || p_slug || '_support'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOBYPASSRLS', v_role);
        END IF;

        -- WITH INHERIT FALSE makes the fail-closed property independent of
        -- app_login's rolinherit attribute: even if someone later ALTERs
        -- app_login to INHERIT, membership still confers nothing without an
        -- explicit SET ROLE. (PG16+.)
        EXECUTE format('GRANT %I TO app_login WITH INHERIT FALSE', v_role);

        -- Tenant roles are INHERIT (unlike app_login), so membership of the
        -- group confers platform_ref SELECT as soon as the role is assumed.
        EXECUTE format('GRANT platform_ref_reader TO %I', v_role);
    END LOOP;

    FOREACH v_layer IN ARRAY ARRAY['bronze', 'silver', 'gold'] LOOP
        v_schema := 't_' || p_slug || '_' || v_layer;

        EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION tenant_migrate',
                       v_schema);

        -- The singleton pattern guarantees at most one identity row, which
        -- matters: assert_schema_owner does SELECT ... INTO, and with multiple
        -- rows that would silently take an arbitrary one.
        EXECUTE format($ddl$
            CREATE TABLE IF NOT EXISTS %I.__schema_identity (
                singleton      boolean PRIMARY KEY DEFAULT true CHECK (singleton),
                tenant_slug    text NOT NULL,
                tenant_id      uuid NOT NULL,
                layer          text NOT NULL,
                provisioned_at timestamptz NOT NULL DEFAULT now()
            )$ddl$, v_schema);

        EXECUTE format($dml$
            INSERT INTO %I.__schema_identity (tenant_slug, tenant_id, layer)
            VALUES (%L, %L, %L)
            ON CONFLICT (singleton) DO UPDATE
               SET tenant_slug = EXCLUDED.tenant_slug,
                   tenant_id   = EXCLUDED.tenant_id,
                   layer       = EXCLUDED.layer
            $dml$, v_schema, p_slug, p_tenant_id, v_layer);
    END LOOP;

    -- Per-tenant migration bookkeeping lives in the silver schema.
    EXECUTE format($ddl$
        CREATE TABLE IF NOT EXISTS %I.__migration_version (
            filename   text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now(),
            checksum   text NOT NULL
        )$ddl$, 't_' || p_slug || '_silver');
END;
$$;
