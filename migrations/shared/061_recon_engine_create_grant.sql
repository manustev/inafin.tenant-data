-- =============================================================================
-- Shared migration 061 — grant CREATE on t_<slug>_reconciliation to
-- t_<slug>_recon_engine.
--
-- Reverses the explicit hold-back in shared migration 060 / docs/adr/0001.
-- Steve, 2026-09-03: the engine team owns their own table DDL inside their
-- own schema now, not gated on a later production-readiness step. This is
-- still the first precedent in this repo for a tenant-scoped role holding
-- CREATE — every other role only ever gets USAGE plus object-level grants,
-- because tenant-data owns all other DDL. It stays safe because it is
-- confined to one schema that already cannot reach Bronze, Silver base
-- tables, Gold, or any other tenant (shared migration 060's isolation
-- checks are unaffected — CREATE inside their own sandboxed schema changes
-- who can run CREATE TABLE, not what any role can reach).
--
-- CREATE OR REPLACE, not an edit of 060 — 060 is applied. Everything else in
-- apply_tenant_grants is unchanged from 060; see that file for the full
-- matrix and its own history (which itself corrected a stale copy of 001's
-- bronze grant — that fix is preserved here unchanged).
-- =============================================================================

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

    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support, v_recon_engine);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_gold, v_recon, v_support);
    -- CHANGED in 061: USAGE + CREATE. See header.
    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO %I', v_reconciliation, v_recon_engine);

    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_bronze AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT ON %I.%I TO %I',
                       v_bronze, r.relname, v_ingest);
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_bronze, r.relname, v_support);
    END LOOP;

    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_silver AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO %I',
                       v_silver, r.relname, v_ingest);
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_silver, r.relname, v_support);
    END LOOP;

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

    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_gold AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO %I',
                       v_gold, r.relname, v_recon);
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_gold, r.relname, v_support);
    END LOOP;

    -- recon_engine now creates its own tables here — this sweep still grants
    -- DML on whatever exists at grant time, which matters for a table it
    -- created before some LATER migration re-runs apply_tenant_grants.
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_reconciliation AND c.relkind = 'r' AND c.relname NOT LIKE '\_\_%'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO %I',
                       v_reconciliation, r.relname, v_recon_engine);
    END LOOP;

    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support, v_recon_engine);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_gold, v_recon, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I',
                   v_reconciliation, v_recon_engine);

    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_bronze, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_silver, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_gold, v_recon);
    -- Grants USAGE on sequences that exist NOW; a sequence created later by
    -- recon_engine's own DDL is theirs by ownership already (CREATE makes
    -- them the creator), so no further grant is needed for their own future
    -- objects the way it is for tenant-data-owned schemas.
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I',
                   v_reconciliation, v_recon_engine);
END;
$$;
