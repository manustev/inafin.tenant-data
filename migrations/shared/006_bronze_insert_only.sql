-- =============================================================================
-- Shared migration 006 — Bronze becomes INSERT-only.
--
-- Bronze records FACT: what arrived, byte-exact, when, from whom. Silver records
-- JUDGEMENT: whether it parsed, whether it superseded something, whether a human
-- needs to look at it. Until now `artefact_ledger.status`, `promoted_batch_id`
-- and `error_detail` put judgement in Bronze, and promotion issued an UPDATE
-- against the evidentiary record.
--
-- This replaces the privilege matrix so `ingest` holds SELECT + INSERT on Bronze
-- and nothing else. The property is then enforced by GRANT rather than by
-- convention: code CANNOT mutate a Bronze row, whatever a future author intends.
-- That is the same reasoning that makes GRANT the tenant boundary rather than a
-- WHERE clause — a control the application cannot forget to apply.
--
-- DELETE goes too. Bronze objects are under Object Lock COMPLIANCE for 2190 days
-- (CGST s.36), so a deletable ledger row could only ever produce an index that
-- disagrees with the store it indexes.
--
-- Everything else in the matrix is unchanged; see 001_app.sql for the reasoning
-- on each step. This function is CREATE OR REPLACE'd wholesale because a partial
-- redefinition would leave two sources of truth for the matrix.
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
    -- 2. Schema USAGE. Unchanged.
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', v_gold, v_recon, v_support);

    ---------------------------------------------------------------------------
    -- 3. Bronze — ingest APPENDS, support reads. CHANGED in 006.
    --
    --    SELECT + INSERT only. No UPDATE, no DELETE, for any runtime role.
    --    An artefact row is written once, when the bytes land, and is never
    --    revised: it is the index over an immutable, Object-Locked object, and
    --    an index that can drift from the thing it indexes is worse than no
    --    index. Disposition (promoted / quarantined / superseded) lives in
    --    Silver, where the parsing that produces it happens.
    --
    --    __* tables remain excluded — see 001_app.sql step 3.
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
    -- 7. __schema_identity — SELECT only, scoped per schema. Unchanged.
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_bronze, v_ingest, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I, %I',
                   v_silver, v_ingest, v_recon, v_support);
    EXECUTE format('GRANT SELECT ON %I.__schema_identity TO %I, %I',
                   v_gold, v_recon, v_support);

    ---------------------------------------------------------------------------
    -- 8. Sequences. Unchanged.
    ---------------------------------------------------------------------------
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_bronze, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_silver, v_ingest);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO %I', v_gold, v_recon);

    -- platform_ref access is NOT granted here. It comes from membership of
    -- platform_ref_reader, conferred once at provisioning (ARCHITECTURE.md 5.8).
END;
$$;
