-- =============================================================================
-- Shared migration 062 — stop apply_tenant_grants from touching table-level
-- privileges inside t_<slug>_reconciliation.
--
-- Broke make migrate on the live shared cluster, 2026-09-03: the engine team
-- has already exercised the CREATE grant from migration 061 and created
-- their own tables (engine_schema_migration, analysis_run,
-- evaluation_context_snapshot, evaluation_plan_snapshot,
-- capability_execution, evidence_record, assessment, outbox_event) — owned
-- by t_<slug>_recon_engine, not tenant_migrate. apply_tenant_grants runs AS
-- tenant_migrate, and Postgres requires the object owner (or GRANT OPTION)
-- to REVOKE/GRANT on it — tenant_migrate has neither on a table it did not
-- create, so the REVOKE ALL ON ALL TABLES IN SCHEMA t_<slug>_reconciliation
-- statement (060's step 1, and 060's step 6b's GRANT loop) fails outright
-- the moment any such table exists.
--
-- This is a real, structural consequence of docs/adr/0001's "the engine
-- owns table DDL inside its own schema" — not a bug to patch around, a
-- design fact to stop fighting. apply_tenant_grants' table-level
-- self-healing sweep only ever makes sense for schemas THIS repo owns every
-- object in; t_<slug>_reconciliation stopped being one of those the moment
-- CREATE was granted. What still needs asserting is schema-level: USAGE +
-- CREATE to recon_engine, and NOTHING to ingest/recon/support — and that is
-- controlled by SCHEMA ownership (tenant_migrate owns the schema itself,
-- via CREATE SCHEMA ... AUTHORIZATION tenant_migrate in
-- provision_tenant_schemas), which is unaffected by who owns the tables
-- inside it. That grant is idempotent and kept.
--
-- recon_engine's own tables are governed entirely by recon_engine's own
-- ownership from here — the same non-involvement this repo already has in
-- inafinplatform/v2's Gold table shapes.
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

    -- CHANGED in 062: v_reconciliation removed from this loop's schema
    -- array — it was never in it (see 060); this array is bronze/silver/gold
    -- only and is unaffected. Kept for clarity that this is deliberate, not
    -- an oversight.
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

    -- REMOVED in 062: the table/sequence-level REVOKE ALL ... IN SCHEMA
    -- v_reconciliation FROM ingest/recon/support block that 060 had here.
    -- See header — tenant_migrate cannot revoke on a table it does not own,
    -- and ingest/recon/support were never granted anything there to begin
    -- with (they hold no USAGE on this schema — see below), so there is
    -- nothing for this repo to defensively revoke.
    EXECUTE format(
        'REVOKE ALL ON SCHEMA %I FROM %I, %I, %I',
        v_reconciliation, v_ingest, v_recon, v_support);

    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support, v_recon_engine);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_gold, v_recon, v_support);
    -- Schema-level grant only — controlled by SCHEMA ownership
    -- (tenant_migrate), unaffected by table ownership inside it. Idempotent.
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

    -- REMOVED in 062: the "GRANT DML on every reconciliation table to
    -- recon_engine" loop 060 had here. Redundant now the engine owns those
    -- tables (ownership already confers full privileges), and actively
    -- breaks the moment a table owned by recon_engine exists, for the same
    -- reason as the removed REVOKE above.

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
    -- REMOVED in 062: the "GRANT USAGE on every reconciliation sequence to
    -- recon_engine" statement — same reason, and same redundancy: sequences
    -- created by recon_engine's own DDL are theirs by ownership already.
END;
$$;
