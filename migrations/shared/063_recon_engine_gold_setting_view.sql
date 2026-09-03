-- =============================================================================
-- Shared migration 063 — recon_engine's first, deliberate Gold exception.
--
-- docs/adr/0001 states flatly: recon_engine gets nothing from Gold, ever —
-- Gold belongs to inafinplatform/v2. tenant_setting (tenant migration 038)
-- is the first thing that legitimately needs to cross that line: it is
-- portal/api-authored config, served from Gold like every other admin
-- table there, and the engine needs to read it (starting with
-- rcm.director_remuneration.amount_match_tolerance).
--
-- This is a NAMED ALLOWLIST on Gold views, mirroring the v1_rcm_% pattern
-- already used for Silver — not a blanket "recon_engine may read Gold"
-- grant. inafinplatform/v2's own Gold tables (fact_record, workspace_*,
-- gst_document, ...) remain completely unreachable to recon_engine; only a
-- view matching v1_reconciliation_% is swept in, the same way a new
-- v1_rcm_% Silver view never needs a grant-migration of its own.
--
-- CREATE OR REPLACE, not an edit of 062 — 062 is applied. Everything else
-- in apply_tenant_grants is unchanged; see 060/062 for the rest of the
-- matrix and its own history.
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
        'REVOKE ALL ON SCHEMA %I FROM %I, %I, %I',
        v_reconciliation, v_ingest, v_recon, v_support);

    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support, v_recon_engine);
    -- CHANGED in 063: v_recon_engine added here. It needs USAGE on the gold
    -- schema itself to reach v1_reconciliation_tenant_setting — it still
    -- gets no privilege on any BASE TABLE there (enforced below, step 6b).
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I, %I',
                   v_gold, v_recon, v_support, v_recon_engine);
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

    -- NEW in 063 — the named Gold-view allowlist. See header: only
    -- v1_reconciliation_%, never a base table, never anything else in Gold.
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_gold AND c.relkind = 'v' AND c.relname LIKE 'v1\_reconciliation\_%'
    LOOP
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', v_gold, r.relname, v_recon_engine);
    END LOOP;

    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support, v_recon_engine);
    -- CHANGED in 063: v_recon_engine added — it now holds USAGE on v_gold
    -- (above), so without this it would hit a bare permission-denied on the
    -- identity table instead of a legible boundary violation (001_app.sql's
    -- own reasoning for this step, applied to the new grantee).
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I, %I',
                   v_gold, v_recon, v_support, v_recon_engine);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I',
                   v_reconciliation, v_recon_engine);

    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_bronze, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_silver, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_gold, v_recon);
END;
$$;
