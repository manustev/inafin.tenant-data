-- =============================================================================
-- Shared migration 060 — inafin-reconciliation-engine's own tenant schema and
-- role. docs/adr/0001-recon-engine-dedicated-schema.md records the decision
-- and why: the engine is a new, independent consumer (not inafinplatform/v2,
-- which is being treated as legacy), and it needs isolation from v2/inafin-
-- api's existing t_<slug>_gold tables that a shared "recon" role cannot give
-- it — apply_tenant_grants' Gold sweep (below, step 6, unchanged) grants
-- EVERY table in {{gold}} to whichever role holds t_<slug>_recon, so a second
-- independent writer sharing that role would read/write the first one's
-- tables. A separate schema with its own role sidesteps that rather than
-- teaching the Gold sweep to be table-scoped.
--
-- CREATE OR REPLACE FUNCTION, not an edit of 001_app.sql — 001 is applied and
-- pinned. Both function bodies are reproduced in full because Postgres
-- replaces a function's entire body, not a diff of it.
--
-- Deliberately NOT included: GRANT CREATE ON SCHEMA ... TO t_<slug>_recon_
-- engine. The engine team owns the table DDL inside its own schema, but that
-- grant is held back until production readiness is a deliberate, separate
-- decision (Steve, 2026-09-03) — see the ADR. Until it is added, the schema
-- exists and is reachable (USAGE) but no role can CREATE a table in it.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- app.provision_tenant_schemas — adds the recon_engine role and the
-- reconciliation schema. The bronze/silver/gold loop already iterates a
-- layer array generically (schema name + __schema_identity row, both derived
-- from the layer name) — 'reconciliation' slots into it unchanged, it does
-- not need its own bespoke block.
-- -----------------------------------------------------------------------------
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

    FOREACH v_role IN ARRAY ARRAY[
        't_' || p_slug || '_ingest',
        't_' || p_slug || '_recon',
        't_' || p_slug || '_support',
        't_' || p_slug || '_recon_engine'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOBYPASSRLS', v_role);
        END IF;

        EXECUTE format('GRANT %I TO app_login WITH INHERIT FALSE', v_role);
        EXECUTE format('GRANT platform_ref_reader TO %I', v_role);
    END LOOP;

    FOREACH v_layer IN ARRAY ARRAY['bronze', 'silver', 'gold', 'reconciliation'] LOOP
        v_schema := 't_' || p_slug || '_' || v_layer;

        EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION tenant_migrate',
                       v_schema);

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

    EXECUTE format($ddl$
        CREATE TABLE IF NOT EXISTS %I.__migration_version (
            filename   text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now(),
            checksum   text NOT NULL
        )$ddl$, 't_' || p_slug || '_silver');
END;
$$;


-- -----------------------------------------------------------------------------
-- app.apply_tenant_grants — recon_engine's grant surface.
--
-- The v1_ view grant is an ALLOWLIST, not the blanket "every v1_ view" recon/
-- support get — the request doc is explicit that the engine reads only
-- APPROVED views, not the full Silver read surface. Two sources feed the
-- allowlist, both rule-based rather than a single hand-maintained list of
-- every view name:
--   1. Any view named v1_rcm_%  — the engine's own dedicated contracts
--      (TD-RCM-001 etc.). New ones need no change here, same as how a new
--      doc type under an existing dispatch mechanism needs no code change.
--   2. A fixed, small set of PRE-EXISTING general-purpose contracts the
--      request doc names explicitly as already sufficient ("Existing
--      contracts consumed without requested schema change") — these are
--      named on purpose, not swept in, because widening what an external
--      engine can read must be a deliberate act, not a side effect of some
--      other team adding an unrelated v1_ view later.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.apply_tenant_grants(p_slug text) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_bronze        text;
    v_silver        text;
    v_gold          text;
    v_reconciliation text;
    v_ingest        text;
    v_recon         text;
    v_support       text;
    v_recon_engine  text;
    v_schema        text;
    r               record;
    -- See header: pre-existing general-purpose v1_ views the request doc
    -- names as already-approved for inafin-reconciliation-engine. Widen this
    -- only on an explicit, reviewed ask — it is the isolation boundary the
    -- request doc itself asked for.
    v_recon_engine_extra_views CONSTANT text[] := ARRAY[
        'v1_ingest_batch',
        'v1_purchase_register',
        'v1_purchase_invoice',
        'v1_purchase_invoice_line',
        'v1_payment_register',
        'v1_rcm_register',
        'v1_foreign_currency_payment_register',
        'v1_gstr_2b',
        'v1_gstr_2b_line',
        'v1_gstr_2b_itc_summary',
        'v1_gstr_3b',
        'v1_gstr_3b_itc_detail',
        'v1_gstr_3b_inward_supply',
        'v1_narrative_contract'
    ];
BEGIN
    IF p_slug !~ '^[a-z][a-z0-9_]{2,31}$' THEN
        RAISE EXCEPTION 'invalid tenant slug %', p_slug USING ERRCODE = '22023';
    END IF;

    v_bronze         := 't_' || p_slug || '_bronze';
    v_silver         := 't_' || p_slug || '_silver';
    v_gold           := 't_' || p_slug || '_gold';
    v_reconciliation := 't_' || p_slug || '_reconciliation';
    v_ingest         := 't_' || p_slug || '_ingest';
    v_recon          := 't_' || p_slug || '_recon';
    v_support        := 't_' || p_slug || '_support';
    v_recon_engine   := 't_' || p_slug || '_recon_engine';

    ---------------------------------------------------------------------------
    -- 1. Revoke everything. Declarative baseline. recon_engine is included in
    --    bronze/silver/gold's revoke (it must never hold anything there), and
    --    ingest/recon/support are revoked from the reconciliation schema (it
    --    belongs to recon_engine alone).
    ---------------------------------------------------------------------------
    FOREACH v_schema IN ARRAY ARRAY[v_bronze, v_silver, v_gold] LOOP
        EXECUTE format(
            'REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I, %I, %I, %I',
            v_schema, v_ingest, v_recon, v_support, v_recon_engine);
        EXECUTE format(
            'REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I, %I, %I, %I',
            v_schema, v_ingest, v_recon, v_support, v_recon_engine);
        EXECUTE format(
            'REVOKE ALL ON SCHEMA %I FROM %I, %I, %I, %I',
            v_schema, v_ingest, v_recon, v_support, v_recon_engine);
    END LOOP;

    EXECUTE format(
        'REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I, %I, %I',
        v_reconciliation, v_ingest, v_recon, v_support);
    EXECUTE format(
        'REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I, %I, %I',
        v_reconciliation, v_ingest, v_recon, v_support);
    EXECUTE format(
        'REVOKE ALL ON SCHEMA %I FROM %I, %I, %I',
        v_reconciliation, v_ingest, v_recon, v_support);

    ---------------------------------------------------------------------------
    -- 2. Schema USAGE.
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support, v_recon_engine);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_gold, v_recon, v_support);
    -- USAGE only — no CREATE. See header: the CREATE grant is a deliberate,
    -- separate, not-yet-made decision (docs/adr/0001).
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', v_reconciliation, v_recon_engine);

    ---------------------------------------------------------------------------
    -- 3. Bronze — ingest APPENDS, support reads. SELECT + INSERT only, no
    --    UPDATE/DELETE — carried forward from migration 006 (Bronze became
    --    INSERT-only there); this CREATE OR REPLACE must not regress it.
    ---------------------------------------------------------------------------
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_bronze AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT ON %I.%I TO %I',
                       v_bronze, r.relname, v_ingest);
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_bronze, r.relname, v_support);
    END LOOP;

    ---------------------------------------------------------------------------
    -- 4. Silver base tables — ingest writes. recon/recon_engine get NOTHING
    --    here.
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
    -- 5. Silver v1_ views — the read contract (ARCHITECTURE.md 2.4). recon and
    --    support get every v1_ view, as before. recon_engine gets only the
    --    allowlisted subset (see header and step 5b).
    ---------------------------------------------------------------------------
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_silver AND c.relkind = 'v' AND c.relname LIKE 'v1\_%'
    LOOP
        EXECUTE format('GRANT SELECT ON %I.%I TO %I, %I',
                       v_silver, r.relname, v_recon, v_support);
    END LOOP;

    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_silver AND c.relkind = 'v'
          AND (c.relname LIKE 'v1\_rcm\_%' OR c.relname = ANY (v_recon_engine_extra_views))
    LOOP
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_silver, r.relname, v_recon_engine);
    END LOOP;

    ---------------------------------------------------------------------------
    -- 6. Gold — recon writes, support reads. ingest and recon_engine get
    --    NOTHING here — Gold is inafinplatform/v2's, not this engine's.
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
    -- 6b. Reconciliation — recon_engine's own schema. Full DML on whatever
    --     tables the engine's own migrations create there. No other tenant
    --     role is granted anything here (see step 1).
    ---------------------------------------------------------------------------
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_reconciliation AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO %I',
                       v_reconciliation, r.relname, v_recon_engine);
    END LOOP;

    ---------------------------------------------------------------------------
    -- 7. __schema_identity — SELECT only, and only for roles that actually
    --    hold USAGE on that schema (step 2).
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support, v_recon_engine);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_gold, v_recon, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I',
                   v_reconciliation, v_recon_engine);

    ---------------------------------------------------------------------------
    -- 8. Sequences.
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_bronze, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_silver, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_gold, v_recon);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I',
                   v_reconciliation, v_recon_engine);
END;
$$;


-- -----------------------------------------------------------------------------
-- app.assert_tenant_context — recognise recon_engine as a legitimate tenant
-- role. Unchanged otherwise; see 001_app.sql for the full rationale.
-- -----------------------------------------------------------------------------
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
       AND v_user <> 't_' || p_slug || '_recon_engine'
    THEN
        RAISE EXCEPTION
            'tenant boundary violation: role % is not a role of tenant %',
            v_user, p_slug
            USING ERRCODE = '42501';
    END IF;

    PERFORM app.assert_schema_owner(p_schema, p_slug);
END;
$$;


-- -----------------------------------------------------------------------------
-- Backfill existing tenants. provision_tenant_schemas is only ever called by
-- src/provisioning/service.py at initial tenant creation — unlike
-- apply_tenant_grants, the migration runner does not re-run it for tenants
-- that already exist. It is documented idempotent for exactly this situation
-- ("safe to re-run against a partially provisioned tenant"), so every
-- already-registered tenant is walked here to pick up the new role and
-- schema without a separate reprovisioning step.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT slug, tenant_id FROM app.tenant_registry LOOP
        PERFORM app.provision_tenant_schemas(r.slug, r.tenant_id);
    END LOOP;
END $$;
