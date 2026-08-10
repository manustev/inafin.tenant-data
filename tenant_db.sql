--
-- PostgreSQL database dump
--

\restrict nkHd10lQvRk5EvOxJRJ40gDpJzgaTGu9RYZHqzrea1nR01B9n0Ptj3HiEfwtcgT

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: app; Type: SCHEMA; Schema: -; Owner: tenant_migrate
--

CREATE SCHEMA app;


ALTER SCHEMA app OWNER TO tenant_migrate;

--
-- Name: platform_ref; Type: SCHEMA; Schema: -; Owner: tenant_migrate
--

CREATE SCHEMA platform_ref;


ALTER SCHEMA platform_ref OWNER TO tenant_migrate;

--
-- Name: t_acme_bronze; Type: SCHEMA; Schema: -; Owner: tenant_migrate
--

CREATE SCHEMA t_acme_bronze;


ALTER SCHEMA t_acme_bronze OWNER TO tenant_migrate;

--
-- Name: t_acme_gold; Type: SCHEMA; Schema: -; Owner: tenant_migrate
--

CREATE SCHEMA t_acme_gold;


ALTER SCHEMA t_acme_gold OWNER TO tenant_migrate;

--
-- Name: t_acme_silver; Type: SCHEMA; Schema: -; Owner: tenant_migrate
--

CREATE SCHEMA t_acme_silver;


ALTER SCHEMA t_acme_silver OWNER TO tenant_migrate;

--
-- Name: t_globex_bronze; Type: SCHEMA; Schema: -; Owner: tenant_migrate
--

CREATE SCHEMA t_globex_bronze;


ALTER SCHEMA t_globex_bronze OWNER TO tenant_migrate;

--
-- Name: t_globex_gold; Type: SCHEMA; Schema: -; Owner: tenant_migrate
--

CREATE SCHEMA t_globex_gold;


ALTER SCHEMA t_globex_gold OWNER TO tenant_migrate;

--
-- Name: t_globex_silver; Type: SCHEMA; Schema: -; Owner: tenant_migrate
--

CREATE SCHEMA t_globex_silver;


ALTER SCHEMA t_globex_silver OWNER TO tenant_migrate;

--
-- Name: gstin; Type: DOMAIN; Schema: platform_ref; Owner: tenant_migrate
--

CREATE DOMAIN platform_ref.gstin AS text
	CONSTRAINT gstin_check CHECK ((VALUE ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$'::text));


ALTER DOMAIN platform_ref.gstin OWNER TO tenant_migrate;

--
-- Name: DOMAIN gstin; Type: COMMENT; Schema: platform_ref; Owner: tenant_migrate
--

COMMENT ON DOMAIN platform_ref.gstin IS 'GSTIN with the embedded PAN enforced. Position 14 is not pinned to Z: TDS, TCS and UIN registrations are valid counterparties.';


--
-- Name: money_inr; Type: DOMAIN; Schema: platform_ref; Owner: tenant_migrate
--

CREATE DOMAIN platform_ref.money_inr AS numeric(18,2);


ALTER DOMAIN platform_ref.money_inr OWNER TO tenant_migrate;

--
-- Name: DOMAIN money_inr; Type: COMMENT; Schema: platform_ref; Owner: tenant_migrate
--

COMMENT ON DOMAIN platform_ref.money_inr IS 'Rupee amount, 2dp. Deliberately unsigned-agnostic — credit notes and reversals are negative.';


--
-- Name: qty; Type: DOMAIN; Schema: platform_ref; Owner: tenant_migrate
--

CREATE DOMAIN platform_ref.qty AS numeric(18,3);


ALTER DOMAIN platform_ref.qty OWNER TO tenant_migrate;

--
-- Name: DOMAIN qty; Type: COMMENT; Schema: platform_ref; Owner: tenant_migrate
--

COMMENT ON DOMAIN platform_ref.qty IS 'Quantity, 3dp. Matches transaction_line.quantity.';


--
-- Name: tax_rate; Type: DOMAIN; Schema: platform_ref; Owner: tenant_migrate
--

CREATE DOMAIN platform_ref.tax_rate AS numeric(6,3)
	CONSTRAINT tax_rate_check CHECK (((VALUE >= (0)::numeric) AND (VALUE <= (100)::numeric)));


ALTER DOMAIN platform_ref.tax_rate OWNER TO tenant_migrate;

--
-- Name: DOMAIN tax_rate; Type: COMMENT; Schema: platform_ref; Owner: tenant_migrate
--

COMMENT ON DOMAIN platform_ref.tax_rate IS 'Percentage rate, 0-100, 3dp — matches meta.tax_rate in the reference schema. Structural only: WHICH rate is correct for an HSN is the corpus'' answer, not this column''s.';


--
-- Name: apply_tenant_grants(text); Type: FUNCTION; Schema: app; Owner: tenant_migrate
--

CREATE FUNCTION app.apply_tenant_grants(p_slug text) RETURNS void
    LANGUAGE plpgsql
    AS $_$
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
$_$;


ALTER FUNCTION app.apply_tenant_grants(p_slug text) OWNER TO tenant_migrate;

--
-- Name: assert_schema_owner(text, text); Type: FUNCTION; Schema: app; Owner: tenant_migrate
--

CREATE FUNCTION app.assert_schema_owner(p_schema text, p_expected_slug text) RETURNS void
    LANGUAGE plpgsql STABLE
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


ALTER FUNCTION app.assert_schema_owner(p_schema text, p_expected_slug text) OWNER TO tenant_migrate;

--
-- Name: FUNCTION assert_schema_owner(p_schema text, p_expected_slug text); Type: COMMENT; Schema: app; Owner: tenant_migrate
--

COMMENT ON FUNCTION app.assert_schema_owner(p_schema text, p_expected_slug text) IS 'Aborts the transaction unless the named schema self-identifies as the expected tenant. Raises SQLSTATE 42501, mapped to TenantBoundaryViolation. Highest value at a trust boundary — call it with a schema name that came from untrusted input (a Kafka manifest) and a slug from trusted context.';


--
-- Name: assert_tenant_context(text, text); Type: FUNCTION; Schema: app; Owner: tenant_migrate
--

CREATE FUNCTION app.assert_tenant_context(p_slug text, p_schema text) RETURNS void
    LANGUAGE plpgsql STABLE
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


ALTER FUNCTION app.assert_tenant_context(p_slug text, p_schema text) OWNER TO tenant_migrate;

--
-- Name: FUNCTION assert_tenant_context(p_slug text, p_schema text); Type: COMMENT; Schema: app; Owner: tenant_migrate
--

COMMENT ON FUNCTION app.assert_tenant_context(p_slug text, p_schema text) IS 'Transaction preamble. Asserts the assumed role AND the target schema both belong to the intended tenant. Called by TenantScopedPool on every transaction; raises SQLSTATE 42501.';


--
-- Name: provision_tenant_schemas(text, uuid); Type: FUNCTION; Schema: app; Owner: tenant_migrate
--

CREATE FUNCTION app.provision_tenant_schemas(p_slug text, p_tenant_id uuid) RETURNS void
    LANGUAGE plpgsql
    AS $_$
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
$_$;


ALTER FUNCTION app.provision_tenant_schemas(p_slug text, p_tenant_id uuid) OWNER TO tenant_migrate;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: shared_migration_version; Type: TABLE; Schema: app; Owner: tenant_migrate
--

CREATE TABLE app.shared_migration_version (
    filename text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    checksum text NOT NULL
);


ALTER TABLE app.shared_migration_version OWNER TO tenant_migrate;

--
-- Name: tenant_registry; Type: TABLE; Schema: app; Owner: tenant_migrate
--

CREATE TABLE app.tenant_registry (
    slug text NOT NULL,
    tenant_id uuid NOT NULL,
    status text DEFAULT 'PROVISIONING'::text NOT NULL,
    bucket text NOT NULL,
    provisioned_at timestamp with time zone DEFAULT now() NOT NULL,
    activated_at timestamp with time zone,
    CONSTRAINT tenant_registry_slug_check CHECK ((slug ~ '^[a-z][a-z0-9_]{2,31}$'::text)),
    CONSTRAINT tenant_registry_status_check CHECK ((status = ANY (ARRAY['PROVISIONING'::text, 'ACTIVE'::text, 'SUSPENDED'::text, 'DEPROVISIONED'::text])))
);


ALTER TABLE app.tenant_registry OWNER TO tenant_migrate;

--
-- Name: TABLE tenant_registry; Type: COMMENT; Schema: app; Owner: tenant_migrate
--

COMMENT ON TABLE app.tenant_registry IS 'One row per tenant. Not readable by tenant roles: the list of tenants is itself disclosure. Read by the migration runner and provisioning only.';


--
-- Name: document_type; Type: TABLE; Schema: platform_ref; Owner: tenant_migrate
--

CREATE TABLE platform_ref.document_type (
    doc_type_code text NOT NULL,
    doc_type_code_vt text DEFAULT 'Document_Type'::text NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    grp text NOT NULL,
    obligation text NOT NULL,
    mode text NOT NULL,
    sections text[] NOT NULL,
    stream text,
    stream_vt text DEFAULT 'Source_Stream'::text NOT NULL,
    archetype smallint,
    silver_storage text NOT NULL,
    source_system text NOT NULL,
    in_scope boolean NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    refresh_cadence text,
    field_contract text DEFAULT ''::text NOT NULL,
    table_name text,
    CONSTRAINT document_type_archetype_check CHECK (((archetype >= 1) AND (archetype <= 8))),
    CONSTRAINT document_type_cadence_scope_ck CHECK (((in_scope AND (refresh_cadence IS NOT NULL)) OR ((NOT in_scope) AND (refresh_cadence IS NULL)))),
    CONSTRAINT document_type_cadence_values_ck CHECK ((refresh_cadence = ANY (ARRAY['ONE_TIME'::text, 'PERIODIC'::text, 'CONTINUOUS'::text]))),
    CONSTRAINT document_type_category_check CHECK ((category = ANY (ARRAY['A'::text, 'B'::text, 'C'::text, 'D'::text]))),
    CONSTRAINT document_type_doc_type_code_vt_check CHECK ((doc_type_code_vt = 'Document_Type'::text)),
    CONSTRAINT document_type_field_contract_scope_ck CHECK (((field_contract = ''::text) OR in_scope)),
    CONSTRAINT document_type_mode_check CHECK ((mode = ANY (ARRAY['BOTH'::text, 'FORENSIC'::text, 'OPERATIONAL'::text]))),
    CONSTRAINT document_type_obligation_check CHECK ((obligation = ANY (ARRAY['MANDATORY'::text, 'CONDITIONAL'::text]))),
    CONSTRAINT document_type_scope_ck CHECK (((in_scope AND (stream IS NOT NULL) AND (archetype IS NOT NULL) AND (silver_storage <> 'NONE'::text)) OR ((NOT in_scope) AND (stream IS NULL) AND (archetype IS NULL) AND (silver_storage = 'NONE'::text)))),
    CONSTRAINT document_type_silver_storage_check CHECK ((silver_storage = ANY (ARRAY['STRUCTURED'::text, 'HYBRID'::text, 'NONE'::text]))),
    CONSTRAINT document_type_stream_vt_check CHECK ((stream_vt = 'Source_Stream'::text)),
    CONSTRAINT document_type_table_name_format_ck CHECK (((table_name IS NULL) OR (table_name ~ '^[a-z][a-z0-9_]{0,49}$'::text))),
    CONSTRAINT document_type_table_name_scope_ck CHECK (((table_name IS NULL) OR in_scope))
);


ALTER TABLE platform_ref.document_type OWNER TO tenant_migrate;

--
-- Name: TABLE document_type; Type: COMMENT; Schema: platform_ref; Owner: tenant_migrate
--

COMMENT ON TABLE platform_ref.document_type IS 'Doc 4 Section 2 as data. One row per distinct ingestible document type. Adding a document type is an INSERT; adding an archetype is a schema change. If adding the Nth instrument requires the latter, the archetype abstraction has failed (ARCHITECTURE.md 6).';


--
-- Name: document_type_ref; Type: TABLE; Schema: platform_ref; Owner: tenant_migrate
--

CREATE TABLE platform_ref.document_type_ref (
    register_ref text NOT NULL,
    doc_type_code text NOT NULL,
    is_canonical boolean NOT NULL
);


ALTER TABLE platform_ref.document_type_ref OWNER TO tenant_migrate;

--
-- Name: TABLE document_type_ref; Type: COMMENT; Schema: platform_ref; Owner: tenant_migrate
--

COMMENT ON TABLE platform_ref.document_type_ref IS 'Doc 4 register refs resolved to document types. Every ref in the register appears here, including aliases and out-of-scope corpus rows, so no ref the CA team quotes is unresolvable.';


--
-- Name: universal_master; Type: TABLE; Schema: platform_ref; Owner: tenant_migrate
--

CREATE TABLE platform_ref.universal_master (
    value_type text NOT NULL,
    value text NOT NULL,
    description text NOT NULL,
    from_date date DEFAULT '1900-01-01'::date NOT NULL,
    to_date date DEFAULT '9999-12-31'::date NOT NULL,
    creation_date timestamp with time zone DEFAULT now() NOT NULL,
    last_update_date timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT 'platform'::text NOT NULL,
    updated_by text DEFAULT 'platform'::text NOT NULL,
    attribute_1 text,
    attribute_2 text,
    attribute_3 text,
    attribute_4 text,
    attribute_5 text,
    attribute_6 text,
    attribute_7 text,
    attribute_8 text,
    attribute_9 text,
    attribute_10 text,
    attribute_11 text,
    attribute_12 text,
    attribute_13 text,
    attribute_14 text,
    attribute_15 text,
    CONSTRAINT universal_master_window CHECK ((to_date > from_date))
);


ALTER TABLE platform_ref.universal_master OWNER TO tenant_migrate;

--
-- Name: TABLE universal_master; Type: COMMENT; Schema: platform_ref; Owner: tenant_migrate
--

COMMENT ON TABLE platform_ref.universal_master IS 'Category B Pattern 1. Every categorical value in the platform. Adding a reference type or a value is a data entry, never a schema change. Note that an FK proves existence, not temporal validity — from_date/to_date must still be resolved as-of the relevant date by the caller.';


--
-- Name: __schema_identity; Type: TABLE; Schema: t_acme_bronze; Owner: tenant_migrate
--

CREATE TABLE t_acme_bronze.__schema_identity (
    singleton boolean DEFAULT true NOT NULL,
    tenant_slug text NOT NULL,
    tenant_id uuid NOT NULL,
    layer text NOT NULL,
    provisioned_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT __schema_identity_singleton_check CHECK (singleton)
);


ALTER TABLE t_acme_bronze.__schema_identity OWNER TO tenant_migrate;

--
-- Name: artefact_ledger; Type: TABLE; Schema: t_acme_bronze; Owner: tenant_migrate
--

CREATE TABLE t_acme_bronze.artefact_ledger (
    ingest_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    declared_document_type text NOT NULL,
    declared_document_type_vt text DEFAULT 'Document_Type'::text NOT NULL,
    source_stream text NOT NULL,
    source_stream_vt text DEFAULT 'Source_Stream'::text NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    content_hash bytea NOT NULL,
    object_key text NOT NULL,
    object_bucket text NOT NULL,
    size_bytes bigint NOT NULL,
    original_filename text,
    received_from text NOT NULL,
    CONSTRAINT artefact_ledger_declared_document_type_vt_check CHECK ((declared_document_type_vt = 'Document_Type'::text)),
    CONSTRAINT artefact_ledger_size_bytes_check CHECK ((size_bytes >= 0)),
    CONSTRAINT artefact_ledger_source_stream_vt_check CHECK ((source_stream_vt = 'Source_Stream'::text))
);


ALTER TABLE t_acme_bronze.artefact_ledger OWNER TO tenant_migrate;

--
-- Name: __schema_identity; Type: TABLE; Schema: t_acme_gold; Owner: tenant_migrate
--

CREATE TABLE t_acme_gold.__schema_identity (
    singleton boolean DEFAULT true NOT NULL,
    tenant_slug text NOT NULL,
    tenant_id uuid NOT NULL,
    layer text NOT NULL,
    provisioned_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT __schema_identity_singleton_check CHECK (singleton)
);


ALTER TABLE t_acme_gold.__schema_identity OWNER TO tenant_migrate;

--
-- Name: batch_execution; Type: TABLE; Schema: t_acme_gold; Owner: tenant_migrate
--

CREATE TABLE t_acme_gold.batch_execution (
    batch_id uuid NOT NULL,
    rule_catalog_version text NOT NULL,
    corpus_version text NOT NULL,
    tenant_pack_version text NOT NULL,
    executed_at timestamp with time zone DEFAULT now() NOT NULL,
    row_count integer NOT NULL,
    CONSTRAINT batch_execution_row_count_check CHECK ((row_count >= 0))
);


ALTER TABLE t_acme_gold.batch_execution OWNER TO tenant_migrate;

--
-- Name: consumer_watermark; Type: TABLE; Schema: t_acme_gold; Owner: tenant_migrate
--

CREATE TABLE t_acme_gold.consumer_watermark (
    consumer_name text NOT NULL,
    document_type text NOT NULL,
    last_ready_at timestamp with time zone NOT NULL,
    last_batch_id uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE t_acme_gold.consumer_watermark OWNER TO tenant_migrate;

--
-- Name: fact_record; Type: TABLE; Schema: t_acme_gold; Owner: tenant_migrate
--

CREATE TABLE t_acme_gold.fact_record (
    fact_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    invoice_id uuid NOT NULL,
    rule_id text NOT NULL,
    fact_type text NOT NULL,
    severity text NOT NULL,
    detail text NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    rule_catalog_version text NOT NULL,
    corpus_version text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fact_record_severity_check CHECK ((severity = ANY (ARRAY['INFO'::text, 'WARN'::text, 'CRITICAL'::text])))
);


ALTER TABLE t_acme_gold.fact_record OWNER TO tenant_migrate;

--
-- Name: __migration_version; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.__migration_version (
    filename text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    checksum text NOT NULL
);


ALTER TABLE t_acme_silver.__migration_version OWNER TO tenant_migrate;

--
-- Name: __schema_identity; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.__schema_identity (
    singleton boolean DEFAULT true NOT NULL,
    tenant_slug text NOT NULL,
    tenant_id uuid NOT NULL,
    layer text NOT NULL,
    provisioned_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT __schema_identity_singleton_check CHECK (singleton)
);


ALTER TABLE t_acme_silver.__schema_identity OWNER TO tenant_migrate;

--
-- Name: advance_receipt_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.advance_receipt_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'ADVANCE_RECEIPT_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    receipt_no text,
    receipt_date date NOT NULL,
    amount platform_ref.money_inr NOT NULL,
    customer text,
    customer_gstin platform_ref.gstin,
    supply_type text,
    gst_rate platform_ref.tax_rate,
    gst_paid_on_advance platform_ref.money_inr DEFAULT 0,
    invoice_linkage text,
    is_adjusted boolean DEFAULT false NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT advance_receipt_register_doc_type_code_check CHECK ((doc_type_code = 'ADVANCE_RECEIPT_REGISTER'::text)),
    CONSTRAINT advance_receipt_register_supply_type_check CHECK ((supply_type = ANY (ARRAY['GOODS'::text, 'SERVICE'::text]))),
    CONSTRAINT advance_receipt_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.advance_receipt_register OWNER TO tenant_migrate;

--
-- Name: advance_receipt_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.advance_receipt_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.advance_receipt_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audited_pl_account; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.audited_pl_account (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    fy text NOT NULL,
    doc_type_code text DEFAULT 'AUDITED_PROFIT_AND_LOSS'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    gl_code text,
    gl_account text NOT NULL,
    narration text,
    account_type text,
    amount platform_ref.money_inr NOT NULL,
    dr_cr character(2),
    gst_treatment text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT audited_pl_account_account_type_check CHECK ((account_type = ANY (ARRAY['REVENUE'::text, 'EXPENSE'::text, 'OTHER'::text]))),
    CONSTRAINT audited_pl_account_doc_type_code_check CHECK ((doc_type_code = 'AUDITED_PROFIT_AND_LOSS'::text)),
    CONSTRAINT audited_pl_account_dr_cr_check CHECK ((dr_cr = ANY (ARRAY['DR'::bpchar, 'CR'::bpchar]))),
    CONSTRAINT audited_pl_account_fy_check CHECK ((fy ~ '^[0-9]{4}-[0-9]{2}$'::text)),
    CONSTRAINT audited_pl_account_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.audited_pl_account OWNER TO tenant_migrate;

--
-- Name: audited_pl_account_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.audited_pl_account ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.audited_pl_account_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: balance_sheet; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.balance_sheet (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    fy text NOT NULL,
    doc_type_code text DEFAULT 'BALANCE_SHEET'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    line_item text NOT NULL,
    gl_code text,
    category text,
    amount platform_ref.money_inr NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT balance_sheet_category_check CHECK ((category = ANY (ARRAY['ADVANCE_FROM_CUSTOMER'::text, 'UNBILLED_REVENUE'::text, 'DEFERRED_REVENUE'::text, 'CREDITOR'::text, 'CAPITAL_GOODS'::text, 'OTHER'::text]))),
    CONSTRAINT balance_sheet_doc_type_code_check CHECK ((doc_type_code = 'BALANCE_SHEET'::text)),
    CONSTRAINT balance_sheet_fy_check CHECK ((fy ~ '^[0-9]{4}-[0-9]{2}$'::text)),
    CONSTRAINT balance_sheet_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.balance_sheet OWNER TO tenant_migrate;

--
-- Name: balance_sheet_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.balance_sheet ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.balance_sheet_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bank_statement_inward_forex; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.bank_statement_inward_forex (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'BANK_STATEMENT_INWARD_FX'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    txn_date date NOT NULL,
    amount_fcy numeric(18,2) NOT NULL,
    currency character(3) NOT NULL,
    amount_inr platform_ref.money_inr,
    remitter text,
    ad_bank_ref text,
    export_invoice_ref text,
    firc_no text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT bank_statement_inward_forex_doc_type_code_check CHECK ((doc_type_code = 'BANK_STATEMENT_INWARD_FX'::text)),
    CONSTRAINT bank_statement_inward_forex_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.bank_statement_inward_forex OWNER TO tenant_migrate;

--
-- Name: bank_statement_inward_forex_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.bank_statement_inward_forex ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.bank_statement_inward_forex_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bank_statement_outward; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.bank_statement_outward (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'BANK_STATEMENT_OUTWARD'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    txn_date date NOT NULL,
    amount platform_ref.money_inr NOT NULL,
    beneficiary text,
    beneficiary_account text,
    narration text,
    bank_ref text,
    invoice_ref text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT bank_statement_outward_doc_type_code_check CHECK ((doc_type_code = 'BANK_STATEMENT_OUTWARD'::text)),
    CONSTRAINT bank_statement_outward_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.bank_statement_outward OWNER TO tenant_migrate;

--
-- Name: bank_statement_outward_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.bank_statement_outward ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.bank_statement_outward_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: common_input_service_invoice; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.common_input_service_invoice (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'HO_COMMON_INPUT_SERVICE_INVOICE'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    supplier_gstin platform_ref.gstin,
    service_category text,
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT common_input_service_invoice_doc_type_code_check CHECK ((doc_type_code = 'HO_COMMON_INPUT_SERVICE_INVOICE'::text)),
    CONSTRAINT common_input_service_invoice_service_category_check CHECK ((service_category = ANY (ARRAY['RENT'::text, 'IT'::text, 'INSURANCE'::text, 'AUDIT'::text, 'OTHER'::text]))),
    CONSTRAINT common_input_service_invoice_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.common_input_service_invoice OWNER TO tenant_migrate;

--
-- Name: common_input_service_invoice_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.common_input_service_invoice ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.common_input_service_invoice_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cost_allocation_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.cost_allocation_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'COST_ALLOCATION_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    from_gstin platform_ref.gstin,
    to_gstin platform_ref.gstin,
    allocation_basis text,
    description text,
    amount platform_ref.money_inr NOT NULL,
    has_invoice boolean DEFAULT false NOT NULL,
    invoice_no text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT cost_allocation_register_doc_type_code_check CHECK ((doc_type_code = 'COST_ALLOCATION_REGISTER'::text)),
    CONSTRAINT cost_allocation_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.cost_allocation_register OWNER TO tenant_migrate;

--
-- Name: cost_allocation_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.cost_allocation_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.cost_allocation_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: credit_debit_note_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.credit_debit_note_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'CREDIT_DEBIT_NOTE_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    note_type text NOT NULL,
    note_no text NOT NULL,
    note_date date NOT NULL,
    original_invoice_no text NOT NULL,
    customer_gstin platform_ref.gstin,
    supply_type text,
    reason_code text,
    reason_text text,
    adjusted_taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    cess platform_ref.money_inr DEFAULT 0,
    note_value platform_ref.money_inr,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT credit_debit_note_register_doc_type_code_check CHECK ((doc_type_code = 'CREDIT_DEBIT_NOTE_REGISTER'::text)),
    CONSTRAINT credit_debit_note_register_note_type_check CHECK ((note_type = ANY (ARRAY['CN'::text, 'DN'::text]))),
    CONSTRAINT credit_debit_note_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.credit_debit_note_register OWNER TO tenant_migrate;

--
-- Name: credit_debit_note_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.credit_debit_note_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.credit_debit_note_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: creditor_ageing_report; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.creditor_ageing_report (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'CREDITOR_AGEING_REPORT'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    outstanding_amount platform_ref.money_inr NOT NULL,
    ageing_days integer,
    ageing_bucket text,
    as_at_date date NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT creditor_ageing_report_doc_type_code_check CHECK ((doc_type_code = 'CREDITOR_AGEING_REPORT'::text)),
    CONSTRAINT creditor_ageing_report_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.creditor_ageing_report OWNER TO tenant_migrate;

--
-- Name: creditor_ageing_report_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.creditor_ageing_report ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.creditor_ageing_report_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: entitlement_instrument; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.entitlement_instrument (
    instrument_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    instrument_type text NOT NULL,
    instrument_archetype smallint DEFAULT 3 NOT NULL,
    issuing_authority text NOT NULL,
    issuing_authority_vt text DEFAULT 'Issuing_Authority'::text NOT NULL,
    instrument_number text NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    status_vt text DEFAULT 'Instrument_Status'::text NOT NULL,
    scope_hsn text[],
    scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    obligation jsonb DEFAULT '{}'::jsonb NOT NULL,
    supersedes_instrument_id uuid,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    CONSTRAINT entitlement_instrument_instrument_archetype_check CHECK ((instrument_archetype = 3)),
    CONSTRAINT entitlement_instrument_issuing_authority_vt_check CHECK ((issuing_authority_vt = 'Issuing_Authority'::text)),
    CONSTRAINT entitlement_instrument_scope_hsn_check CHECK (((scope_hsn IS NULL) OR (cardinality(scope_hsn) > 0))),
    CONSTRAINT entitlement_instrument_status_vt_check CHECK ((status_vt = 'Instrument_Status'::text)),
    CONSTRAINT entitlement_instrument_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.entitlement_instrument OWNER TO tenant_migrate;

--
-- Name: TABLE entitlement_instrument; Type: COMMENT; Schema: t_acme_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_acme_silver.entitlement_instrument IS 'Archetype 3 (ARCHITECTURE.md 6). Thirty-three document types collapse here. Adding the thirty-fourth entitlement instrument must be a registry row, not a table — if it needs a schema change, the archetype abstraction has failed.';


--
-- Name: fixed_asset_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.fixed_asset_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'FIXED_ASSET_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    asset_id text NOT NULL,
    description text,
    hsn text,
    purchase_date date,
    purchase_value platform_ref.money_inr,
    itc_availed platform_ref.money_inr DEFAULT 0,
    useful_life_months integer,
    date_of_disposal date,
    disposal_value platform_ref.money_inr,
    disposal_type text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT fixed_asset_register_disposal_type_check CHECK ((disposal_type = ANY (ARRAY['SALE'::text, 'SCRAP'::text, 'TRANSFER'::text, 'WRITE_OFF'::text, 'OTHER'::text]))),
    CONSTRAINT fixed_asset_register_doc_type_code_check CHECK ((doc_type_code = 'FIXED_ASSET_REGISTER'::text)),
    CONSTRAINT fixed_asset_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.fixed_asset_register OWNER TO tenant_migrate;

--
-- Name: fixed_asset_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.fixed_asset_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.fixed_asset_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: foreign_currency_payment_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.foreign_currency_payment_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'FOREIGN_CURRENCY_PAYMENT_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    payment_date date NOT NULL,
    amount_fcy numeric(18,2) NOT NULL,
    currency character(3) NOT NULL,
    amount_inr platform_ref.money_inr,
    vendor text,
    country text,
    purpose text,
    invoice_contract_ref text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT foreign_currency_payment_register_doc_type_code_check CHECK ((doc_type_code = 'FOREIGN_CURRENCY_PAYMENT_REGISTER'::text)),
    CONSTRAINT foreign_currency_payment_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.foreign_currency_payment_register OWNER TO tenant_migrate;

--
-- Name: foreign_currency_payment_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.foreign_currency_payment_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.foreign_currency_payment_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ingest_batch; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.ingest_batch (
    batch_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    document_type text NOT NULL,
    document_type_vt text DEFAULT 'Document_Type'::text NOT NULL,
    source_stream text NOT NULL,
    source_stream_vt text DEFAULT 'Source_Stream'::text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    row_count integer NOT NULL,
    content_hash bytea NOT NULL,
    bronze_manifest_ref text NOT NULL,
    status text DEFAULT 'READY'::text NOT NULL,
    status_vt text DEFAULT 'Batch_Status'::text NOT NULL,
    ready_at timestamp with time zone DEFAULT now() NOT NULL,
    ingest_run_id uuid NOT NULL,
    superseded_by uuid,
    CONSTRAINT ingest_batch_document_type_vt_check CHECK ((document_type_vt = 'Document_Type'::text)),
    CONSTRAINT ingest_batch_period CHECK ((period_end >= period_start)),
    CONSTRAINT ingest_batch_row_count_check CHECK ((row_count >= 0)),
    CONSTRAINT ingest_batch_source_stream_vt_check CHECK ((source_stream_vt = 'Source_Stream'::text)),
    CONSTRAINT ingest_batch_status_vt_check CHECK ((status_vt = 'Batch_Status'::text))
);


ALTER TABLE t_acme_silver.ingest_batch OWNER TO tenant_migrate;

--
-- Name: inter_gstin_transaction_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.inter_gstin_transaction_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'INTER_GSTIN_TRANSACTION_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    from_gstin platform_ref.gstin NOT NULL,
    to_gstin platform_ref.gstin NOT NULL,
    txn_type text,
    txn_date date NOT NULL,
    invoice_no text,
    description text,
    hsn_sac text,
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT inter_gstin_transaction_register_doc_type_code_check CHECK ((doc_type_code = 'INTER_GSTIN_TRANSACTION_REGISTER'::text)),
    CONSTRAINT inter_gstin_transaction_register_txn_type_check CHECK ((txn_type = ANY (ARRAY['STOCK_TRANSFER'::text, 'CROSS_CHARGE'::text, 'CAPITAL_GOOD_TRANSFER'::text, 'OTHER'::text]))),
    CONSTRAINT inter_gstin_transaction_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.inter_gstin_transaction_register OWNER TO tenant_migrate;

--
-- Name: inter_gstin_transaction_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.inter_gstin_transaction_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.inter_gstin_transaction_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: itc_reversal_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.itc_reversal_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'ITC_REVERSAL_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    reversal_type text NOT NULL,
    reversal_basis text,
    cgst_reversed platform_ref.money_inr DEFAULT 0,
    sgst_reversed platform_ref.money_inr DEFAULT 0,
    igst_reversed platform_ref.money_inr DEFAULT 0,
    cess_reversed platform_ref.money_inr DEFAULT 0,
    remarks text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT itc_reversal_register_doc_type_code_check CHECK ((doc_type_code = 'ITC_REVERSAL_REGISTER'::text)),
    CONSTRAINT itc_reversal_register_reversal_type_check CHECK ((reversal_type = ANY (ARRAY['RULE_42'::text, 'RULE_43'::text, 'SEC_17_5'::text, 'OTHER'::text]))),
    CONSTRAINT itc_reversal_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.itc_reversal_register OWNER TO tenant_migrate;

--
-- Name: itc_reversal_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.itc_reversal_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.itc_reversal_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: job_work_dispatch_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.job_work_dispatch_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'JOBWORK_DISPATCH_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    challan_no text,
    date_of_goods_dispatch_to_job_worker date NOT NULL,
    description text,
    hsn text,
    qty platform_ref.qty,
    uom text,
    taxable_value platform_ref.money_inr,
    job_worker_gstin platform_ref.gstin,
    expected_return_date date,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT job_work_dispatch_register_doc_type_code_check CHECK ((doc_type_code = 'JOBWORK_DISPATCH_REGISTER'::text)),
    CONSTRAINT job_work_dispatch_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.job_work_dispatch_register OWNER TO tenant_migrate;

--
-- Name: job_work_dispatch_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.job_work_dispatch_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.job_work_dispatch_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payment_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.payment_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'SUPPLIER_PAYMENT_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    payment_date date NOT NULL,
    amount platform_ref.money_inr NOT NULL,
    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text,
    payment_ref text,
    payment_mode text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT payment_register_doc_type_code_check CHECK ((doc_type_code = 'SUPPLIER_PAYMENT_REGISTER'::text)),
    CONSTRAINT payment_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.payment_register OWNER TO tenant_migrate;

--
-- Name: payment_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.payment_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.payment_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: platform_settlement_report; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.platform_settlement_report (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    report_type text NOT NULL,
    operator text NOT NULL,
    order_no text,
    order_date date,
    invoice_no text,
    invoice_date date,
    buyer_state text,
    taxable_value platform_ref.money_inr,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    tcs platform_ref.money_inr DEFAULT 0,
    marketplace_fee platform_ref.money_inr DEFAULT 0,
    shipping_charges platform_ref.money_inr DEFAULT 0,
    net_payout platform_ref.money_inr,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT platform_settlement_report_doc_type_code_check CHECK ((doc_type_code = ANY (ARRAY['AMAZON_MTR'::text, 'FLIPKART_GTR'::text, 'MARKETPLACE_SETTLEMENT_SSR'::text, 'MARKETPLACE_DISBURSEMENT_DRR'::text, 'MARKETPLACE_RETURNS_VRET'::text, 'FOOD_DELIVERY_PARTNER_REPORT'::text, 'OTHER_MARKETPLACE_SETTLEMENT'::text]))),
    CONSTRAINT platform_settlement_report_report_type_check CHECK ((report_type = ANY (ARRAY['MTR'::text, 'GTR'::text, 'SSR'::text, 'DRR'::text, 'VRET'::text, 'FDP'::text, 'OTHER'::text]))),
    CONSTRAINT platform_settlement_report_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.platform_settlement_report OWNER TO tenant_migrate;

--
-- Name: platform_settlement_report_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.platform_settlement_report ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.platform_settlement_report_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: product_sku_master; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.product_sku_master (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'SKU_MASTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    sku text NOT NULL,
    description text,
    hsn text,
    mrp_applicable boolean DEFAULT false NOT NULL,
    declared_mrp platform_ref.money_inr,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT product_sku_master_doc_type_code_check CHECK ((doc_type_code = 'SKU_MASTER'::text)),
    CONSTRAINT product_sku_master_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.product_sku_master OWNER TO tenant_migrate;

--
-- Name: product_sku_master_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.product_sku_master ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.product_sku_master_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: purchase_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.purchase_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'PURCHASE_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    supplier_gstin platform_ref.gstin,
    invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    hsn_sac text,
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    cess platform_ref.money_inr DEFAULT 0,
    gl_code text NOT NULL,
    cost_centre text NOT NULL,
    itc_eligibility text,
    rcm_flag boolean DEFAULT false NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT purchase_register_doc_type_code_check CHECK ((doc_type_code = 'PURCHASE_REGISTER'::text)),
    CONSTRAINT purchase_register_itc_eligibility_check CHECK ((itc_eligibility = ANY (ARRAY['ELIGIBLE'::text, 'INELIGIBLE'::text, 'BLOCKED'::text, 'PARTIAL'::text]))),
    CONSTRAINT purchase_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.purchase_register OWNER TO tenant_migrate;

--
-- Name: purchase_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.purchase_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.purchase_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: quarantined_artefact; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.quarantined_artefact (
    ingest_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    document_type text NOT NULL,
    document_type_vt text DEFAULT 'Document_Type'::text NOT NULL,
    rejected_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text NOT NULL,
    ingest_run_id uuid,
    CONSTRAINT quarantined_artefact_document_type_vt_check CHECK ((document_type_vt = 'Document_Type'::text))
);


ALTER TABLE t_acme_silver.quarantined_artefact OWNER TO tenant_migrate;

--
-- Name: TABLE quarantined_artefact; Type: COMMENT; Schema: t_acme_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_acme_silver.quarantined_artefact IS 'Artefacts that failed structural validation. The bytes remain in Bronze under Object Lock; this is the record of why they were not promoted.';


--
-- Name: rcm_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.rcm_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'RCM_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    rcm_category text NOT NULL,
    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text,
    invoice_date date,
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    rcm_liability platform_ref.money_inr,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    paid_period date,
    is_paid boolean DEFAULT false NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT rcm_register_doc_type_code_check CHECK ((doc_type_code = 'RCM_REGISTER'::text)),
    CONSTRAINT rcm_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.rcm_register OWNER TO tenant_migrate;

--
-- Name: rcm_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.rcm_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.rcm_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: running_account_contract_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.running_account_contract_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'CONTINUOUS_SUPPLY_CONTRACT_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    contract_ref text NOT NULL,
    customer text,
    customer_gstin platform_ref.gstin,
    billing_frequency text,
    statement_date date,
    delivery_date date,
    amount platform_ref.money_inr,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT running_account_contract_register_doc_type_code_check CHECK ((doc_type_code = 'CONTINUOUS_SUPPLY_CONTRACT_REGISTER'::text)),
    CONSTRAINT running_account_contract_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.running_account_contract_register OWNER TO tenant_migrate;

--
-- Name: running_account_contract_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.running_account_contract_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.running_account_contract_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sales_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.sales_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'SALES_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    document_hash text NOT NULL,
    invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    customer_gstin platform_ref.gstin,
    invoice_type text NOT NULL,
    invoice_type_vt text DEFAULT 'Invoice_Type'::text NOT NULL,
    supply_type text,
    supply_type_vt text DEFAULT 'Supply_Type'::text NOT NULL,
    place_of_supply text,
    reverse_charge boolean DEFAULT false NOT NULL,
    currency character(3) DEFAULT 'INR'::bpchar NOT NULL,
    trade_discount platform_ref.money_inr DEFAULT 0,
    freight platform_ref.money_inr DEFAULT 0,
    packing platform_ref.money_inr DEFAULT 0,
    insurance platform_ref.money_inr DEFAULT 0,
    taxable_value platform_ref.money_inr,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    cess platform_ref.money_inr DEFAULT 0,
    total_value platform_ref.money_inr,
    total_tax platform_ref.money_inr GENERATED ALWAYS AS ((((COALESCE((cgst)::numeric, (0)::numeric) + COALESCE((sgst)::numeric, (0)::numeric)) + COALESCE((igst)::numeric, (0)::numeric)) + COALESCE((cess)::numeric, (0)::numeric))) STORED,
    irn text,
    ewb_no text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT sales_register_doc_type_code_check CHECK ((doc_type_code = 'SALES_REGISTER'::text)),
    CONSTRAINT sales_register_invoice_type_vt_check CHECK ((invoice_type_vt = 'Invoice_Type'::text)),
    CONSTRAINT sales_register_place_of_supply_check CHECK ((place_of_supply ~ '^[0-9]{2}$'::text)),
    CONSTRAINT sales_register_supersession CHECK ((((superseded_at IS NULL) AND (modified_at IS NULL)) OR (superseded_at IS NOT NULL))),
    CONSTRAINT sales_register_supply_type_vt_check CHECK ((supply_type_vt = 'Supply_Type'::text)),
    CONSTRAINT sales_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.sales_register OWNER TO tenant_migrate;

--
-- Name: TABLE sales_register; Type: COMMENT; Schema: t_acme_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_acme_silver.sales_register IS 'A1.01 SALES_REGISTER, reconciled against reference/inafin_a1_schema.sql. One row per invoice. Header totals are stored as supplied and never reconciled against the line sum — the disagreement is a finding.';


--
-- Name: sales_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.sales_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.sales_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sales_register_line; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.sales_register_line (
    id bigint NOT NULL,
    header_id bigint NOT NULL,
    line_number integer NOT NULL,
    row_hash text NOT NULL,
    hsn_sac text,
    description text,
    qty platform_ref.qty,
    uom text,
    unit_price numeric(18,4),
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    cess platform_ref.money_inr DEFAULT 0,
    total_value platform_ref.money_inr,
    total_tax platform_ref.money_inr GENERATED ALWAYS AS ((((COALESCE((cgst)::numeric, (0)::numeric) + COALESCE((sgst)::numeric, (0)::numeric)) + COALESCE((igst)::numeric, (0)::numeric)) + COALESCE((cess)::numeric, (0)::numeric))) STORED,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT sales_register_line_hsn_sac_check CHECK (((hsn_sac IS NULL) OR (hsn_sac ~ '^[0-9]{4,8}$'::text))),
    CONSTRAINT sales_register_line_line_number_check CHECK ((line_number > 0)),
    CONSTRAINT sales_register_line_tax_head CHECK ((((COALESCE((igst)::numeric, (0)::numeric) > (0)::numeric) AND (COALESCE((cgst)::numeric, (0)::numeric) = (0)::numeric) AND (COALESCE((sgst)::numeric, (0)::numeric) = (0)::numeric)) OR (COALESCE((igst)::numeric, (0)::numeric) = (0)::numeric)))
);


ALTER TABLE t_acme_silver.sales_register_line OWNER TO tenant_migrate;

--
-- Name: TABLE sales_register_line; Type: COMMENT; Schema: t_acme_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_acme_silver.sales_register_line IS 'A1.01 SALES_REGISTER lines. Column names follow the reference schema; the header/line split is ours, so the same names appear at both grains.';


--
-- Name: sales_register_line_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.sales_register_line ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.sales_register_line_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: stock_inventory_register; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.stock_inventory_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'STOCK_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    sku text NOT NULL,
    description text,
    hsn text,
    opening_qty platform_ref.qty,
    closing_qty platform_ref.qty,
    write_off_qty platform_ref.qty DEFAULT 0,
    write_off_value platform_ref.money_inr DEFAULT 0,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT stock_inventory_register_doc_type_code_check CHECK ((doc_type_code = 'STOCK_REGISTER'::text)),
    CONSTRAINT stock_inventory_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.stock_inventory_register OWNER TO tenant_migrate;

--
-- Name: stock_inventory_register_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.stock_inventory_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.stock_inventory_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: transaction_document; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.transaction_document (
    doc_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    doc_type text NOT NULL,
    doc_archetype smallint DEFAULT 1 NOT NULL,
    direction text NOT NULL,
    direction_vt text DEFAULT 'Direction'::text NOT NULL,
    doc_number text NOT NULL,
    doc_date date NOT NULL,
    counterparty_gstin text,
    counterparty_name text,
    total_taxable_value numeric(18,2) NOT NULL,
    total_tax_value numeric(18,2) NOT NULL,
    total_value numeric(18,2) NOT NULL,
    currency text DEFAULT 'INR'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    bronze_ingest_id uuid NOT NULL,
    CONSTRAINT transaction_document_counterparty_gstin_check CHECK (((counterparty_gstin IS NULL) OR (counterparty_gstin ~ '^[0-9]{2}[A-Z0-9]{13}$'::text))),
    CONSTRAINT transaction_document_direction_vt_check CHECK ((direction_vt = 'Direction'::text)),
    CONSTRAINT transaction_document_doc_archetype_check CHECK ((doc_archetype = 1)),
    CONSTRAINT transaction_document_totals CHECK ((total_value = (total_taxable_value + total_tax_value))),
    CONSTRAINT transaction_document_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.transaction_document OWNER TO tenant_migrate;

--
-- Name: TABLE transaction_document; Type: COMMENT; Schema: t_acme_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_acme_silver.transaction_document IS 'Archetype 1 (ARCHITECTURE.md 6). Eleven document types collapse here. What varies per type is platform_ref.document_type.field_contract, not a column — if a new transaction type needs a schema change, the archetype has failed.';


--
-- Name: transaction_line; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.transaction_line (
    line_id uuid NOT NULL,
    doc_id uuid NOT NULL,
    line_number integer NOT NULL,
    hsn_sac text NOT NULL,
    description text,
    quantity numeric(18,3) NOT NULL,
    unit_of_measure text,
    unit_price numeric(18,4) NOT NULL,
    taxable_value numeric(18,2) NOT NULL,
    gst_rate numeric(5,2) NOT NULL,
    cgst_amount numeric(18,2) DEFAULT 0 NOT NULL,
    sgst_amount numeric(18,2) DEFAULT 0 NOT NULL,
    igst_amount numeric(18,2) DEFAULT 0 NOT NULL,
    cess_amount numeric(18,2) DEFAULT 0 NOT NULL,
    itc_amount numeric(18,2) DEFAULT 0 NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT transaction_line_gst_rate_check CHECK (((gst_rate >= (0)::numeric) AND (gst_rate <= (100)::numeric))),
    CONSTRAINT transaction_line_hsn_sac_check CHECK ((hsn_sac ~ '^[0-9]{4,8}$'::text)),
    CONSTRAINT transaction_line_line_number_check CHECK ((line_number > 0)),
    CONSTRAINT transaction_line_tax_head CHECK ((((igst_amount > (0)::numeric) AND (cgst_amount = (0)::numeric) AND (sgst_amount = (0)::numeric)) OR (igst_amount = (0)::numeric)))
);


ALTER TABLE t_acme_silver.transaction_line OWNER TO tenant_migrate;

--
-- Name: trial_balance; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.trial_balance (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    fy text NOT NULL,
    doc_type_code text DEFAULT 'TRIAL_BALANCE'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    gl_code text NOT NULL,
    account_description text NOT NULL,
    opening_balance platform_ref.money_inr DEFAULT 0,
    closing_balance platform_ref.money_inr DEFAULT 0,
    dr_cr character(2),
    gst_treatment text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT trial_balance_doc_type_code_check CHECK ((doc_type_code = 'TRIAL_BALANCE'::text)),
    CONSTRAINT trial_balance_dr_cr_check CHECK ((dr_cr = ANY (ARRAY['DR'::bpchar, 'CR'::bpchar]))),
    CONSTRAINT trial_balance_fy_check CHECK ((fy ~ '^[0-9]{4}-[0-9]{2}$'::text)),
    CONSTRAINT trial_balance_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.trial_balance OWNER TO tenant_migrate;

--
-- Name: trial_balance_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.trial_balance ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.trial_balance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: unbilled_revenue_schedule; Type: TABLE; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE TABLE t_acme_silver.unbilled_revenue_schedule (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'UNBILLED_REVENUE_SCHEDULE'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    item_id text,
    description text,
    contract_ref text,
    customer text,
    customer_gstin platform_ref.gstin,
    amount platform_ref.money_inr NOT NULL,
    date_of_service_completion date,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT unbilled_revenue_schedule_doc_type_code_check CHECK ((doc_type_code = 'UNBILLED_REVENUE_SCHEDULE'::text)),
    CONSTRAINT unbilled_revenue_schedule_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_acme_silver.unbilled_revenue_schedule OWNER TO tenant_migrate;

--
-- Name: unbilled_revenue_schedule_id_seq; Type: SEQUENCE; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE t_acme_silver.unbilled_revenue_schedule ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_acme_silver.unbilled_revenue_schedule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: v1_advance_receipt_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_advance_receipt_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    receipt_no,
    receipt_date,
    amount,
    customer,
    customer_gstin,
    supply_type,
    gst_rate,
    gst_paid_on_advance,
    invoice_linkage,
    is_adjusted,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.advance_receipt_register;


ALTER VIEW t_acme_silver.v1_advance_receipt_register OWNER TO tenant_migrate;

--
-- Name: v1_audited_pl_account; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_audited_pl_account AS
 SELECT id,
    entity_id,
    gstin,
    fy,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    gl_code,
    gl_account,
    narration,
    account_type,
    amount,
    dr_cr,
    gst_treatment,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.audited_pl_account;


ALTER VIEW t_acme_silver.v1_audited_pl_account OWNER TO tenant_migrate;

--
-- Name: v1_balance_sheet; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_balance_sheet AS
 SELECT id,
    entity_id,
    gstin,
    fy,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    line_item,
    gl_code,
    category,
    amount,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.balance_sheet;


ALTER VIEW t_acme_silver.v1_balance_sheet OWNER TO tenant_migrate;

--
-- Name: v1_bank_statement_inward_forex; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_bank_statement_inward_forex AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    txn_date,
    amount_fcy,
    currency,
    amount_inr,
    remitter,
    ad_bank_ref,
    export_invoice_ref,
    firc_no,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.bank_statement_inward_forex;


ALTER VIEW t_acme_silver.v1_bank_statement_inward_forex OWNER TO tenant_migrate;

--
-- Name: v1_bank_statement_outward; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_bank_statement_outward AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    txn_date,
    amount,
    beneficiary,
    beneficiary_account,
    narration,
    bank_ref,
    invoice_ref,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.bank_statement_outward;


ALTER VIEW t_acme_silver.v1_bank_statement_outward OWNER TO tenant_migrate;

--
-- Name: v1_common_input_service_invoice; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_common_input_service_invoice AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    invoice_no,
    invoice_date,
    supplier_gstin,
    service_category,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.common_input_service_invoice;


ALTER VIEW t_acme_silver.v1_common_input_service_invoice OWNER TO tenant_migrate;

--
-- Name: v1_cost_allocation_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_cost_allocation_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    from_gstin,
    to_gstin,
    allocation_basis,
    description,
    amount,
    has_invoice,
    invoice_no,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.cost_allocation_register;


ALTER VIEW t_acme_silver.v1_cost_allocation_register OWNER TO tenant_migrate;

--
-- Name: v1_credit_debit_note_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_credit_debit_note_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    note_type,
    note_no,
    note_date,
    original_invoice_no,
    customer_gstin,
    supply_type,
    reason_code,
    reason_text,
    adjusted_taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    cess,
    note_value,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.credit_debit_note_register;


ALTER VIEW t_acme_silver.v1_credit_debit_note_register OWNER TO tenant_migrate;

--
-- Name: v1_creditor_ageing_report; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_creditor_ageing_report AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    supplier_gstin,
    supplier_name,
    invoice_no,
    invoice_date,
    outstanding_amount,
    ageing_days,
    ageing_bucket,
    as_at_date,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.creditor_ageing_report;


ALTER VIEW t_acme_silver.v1_creditor_ageing_report OWNER TO tenant_migrate;

--
-- Name: v1_entitlement_instrument; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_entitlement_instrument AS
 SELECT instrument_id,
    entity_id,
    instrument_type,
    issuing_authority,
    instrument_number,
    valid_from,
    valid_to,
    status,
    scope_hsn,
    scope,
    obligation,
    supersedes_instrument_id,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id
   FROM t_acme_silver.entitlement_instrument;


ALTER VIEW t_acme_silver.v1_entitlement_instrument OWNER TO tenant_migrate;

--
-- Name: v1_fixed_asset_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_fixed_asset_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    asset_id,
    description,
    hsn,
    purchase_date,
    purchase_value,
    itc_availed,
    useful_life_months,
    date_of_disposal,
    disposal_value,
    disposal_type,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.fixed_asset_register;


ALTER VIEW t_acme_silver.v1_fixed_asset_register OWNER TO tenant_migrate;

--
-- Name: v1_foreign_currency_payment_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_foreign_currency_payment_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    payment_date,
    amount_fcy,
    currency,
    amount_inr,
    vendor,
    country,
    purpose,
    invoice_contract_ref,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.foreign_currency_payment_register;


ALTER VIEW t_acme_silver.v1_foreign_currency_payment_register OWNER TO tenant_migrate;

--
-- Name: v1_ingest_batch; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_ingest_batch AS
 SELECT batch_id,
    entity_id,
    document_type,
    source_stream,
    period_start,
    period_end,
    row_count,
    content_hash,
    bronze_manifest_ref,
    status,
    ready_at,
    ingest_run_id,
    superseded_by
   FROM t_acme_silver.ingest_batch;


ALTER VIEW t_acme_silver.v1_ingest_batch OWNER TO tenant_migrate;

--
-- Name: v1_inter_gstin_transaction_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_inter_gstin_transaction_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    from_gstin,
    to_gstin,
    txn_type,
    txn_date,
    invoice_no,
    description,
    hsn_sac,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.inter_gstin_transaction_register;


ALTER VIEW t_acme_silver.v1_inter_gstin_transaction_register OWNER TO tenant_migrate;

--
-- Name: v1_itc_reversal_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_itc_reversal_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    reversal_type,
    reversal_basis,
    cgst_reversed,
    sgst_reversed,
    igst_reversed,
    cess_reversed,
    remarks,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.itc_reversal_register;


ALTER VIEW t_acme_silver.v1_itc_reversal_register OWNER TO tenant_migrate;

--
-- Name: v1_job_work_dispatch_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_job_work_dispatch_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    challan_no,
    date_of_goods_dispatch_to_job_worker,
    description,
    hsn,
    qty,
    uom,
    taxable_value,
    job_worker_gstin,
    expected_return_date,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.job_work_dispatch_register;


ALTER VIEW t_acme_silver.v1_job_work_dispatch_register OWNER TO tenant_migrate;

--
-- Name: v1_payment_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_payment_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    payment_date,
    amount,
    supplier_gstin,
    supplier_name,
    invoice_no,
    payment_ref,
    payment_mode,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.payment_register;


ALTER VIEW t_acme_silver.v1_payment_register OWNER TO tenant_migrate;

--
-- Name: v1_platform_settlement_report; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_platform_settlement_report AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    report_type,
    operator,
    order_no,
    order_date,
    invoice_no,
    invoice_date,
    buyer_state,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    tcs,
    marketplace_fee,
    shipping_charges,
    net_payout,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.platform_settlement_report;


ALTER VIEW t_acme_silver.v1_platform_settlement_report OWNER TO tenant_migrate;

--
-- Name: v1_product_sku_master; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_product_sku_master AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    sku,
    description,
    hsn,
    mrp_applicable,
    declared_mrp,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.product_sku_master;


ALTER VIEW t_acme_silver.v1_product_sku_master OWNER TO tenant_migrate;

--
-- Name: v1_purchase_invoice; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_purchase_invoice AS
 SELECT doc_id AS invoice_id,
    batch_id,
    entity_id,
    doc_number AS invoice_number,
    counterparty_gstin AS supplier_gstin,
    doc_date AS invoice_date,
    total_taxable_value,
    total_tax_value,
    total_value,
    ((attributes ->> 'payment_due_date'::text))::date AS payment_due_date,
    currency,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    bronze_ingest_id
   FROM t_acme_silver.transaction_document
  WHERE (doc_type = 'PURCHASE_REGISTER'::text);


ALTER VIEW t_acme_silver.v1_purchase_invoice OWNER TO tenant_migrate;

--
-- Name: v1_purchase_invoice_line; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_purchase_invoice_line AS
 SELECT l.line_id,
    l.doc_id AS invoice_id,
    d.batch_id,
    l.line_number,
    l.hsn_sac,
    l.description,
    l.quantity,
    l.unit_of_measure,
    l.unit_price,
    l.taxable_value,
    l.gst_rate,
    l.cgst_amount,
    l.sgst_amount,
    l.igst_amount,
    l.itc_amount
   FROM (t_acme_silver.transaction_line l
     JOIN t_acme_silver.transaction_document d ON ((d.doc_id = l.doc_id)))
  WHERE (d.doc_type = 'PURCHASE_REGISTER'::text);


ALTER VIEW t_acme_silver.v1_purchase_invoice_line OWNER TO tenant_migrate;

--
-- Name: v1_purchase_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_purchase_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    supplier_gstin,
    invoice_no,
    invoice_date,
    hsn_sac,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    cess,
    gl_code,
    cost_centre,
    itc_eligibility,
    rcm_flag,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.purchase_register;


ALTER VIEW t_acme_silver.v1_purchase_register OWNER TO tenant_migrate;

--
-- Name: v1_rcm_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_rcm_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    rcm_category,
    supplier_gstin,
    supplier_name,
    invoice_no,
    invoice_date,
    taxable_value,
    gst_rate,
    rcm_liability,
    cgst,
    sgst,
    igst,
    paid_period,
    is_paid,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.rcm_register;


ALTER VIEW t_acme_silver.v1_rcm_register OWNER TO tenant_migrate;

--
-- Name: v1_running_account_contract_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_running_account_contract_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    contract_ref,
    customer,
    customer_gstin,
    billing_frequency,
    statement_date,
    delivery_date,
    amount,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.running_account_contract_register;


ALTER VIEW t_acme_silver.v1_running_account_contract_register OWNER TO tenant_migrate;

--
-- Name: v1_sales_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_sales_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    document_hash,
    invoice_no,
    invoice_date,
    customer_gstin,
    invoice_type,
    supply_type,
    place_of_supply,
    reverse_charge,
    currency,
    trade_discount,
    freight,
    packing,
    insurance,
    taxable_value,
    cgst,
    sgst,
    igst,
    cess,
    total_value,
    total_tax,
    irn,
    ewb_no,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.sales_register;


ALTER VIEW t_acme_silver.v1_sales_register OWNER TO tenant_migrate;

--
-- Name: v1_sales_register_line; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_sales_register_line AS
 SELECT l.id,
    l.header_id,
    h.batch_id,
    h.entity_id,
    l.line_number,
    l.row_hash,
    l.hsn_sac,
    l.description,
    l.qty,
    l.uom,
    l.unit_price,
    l.taxable_value,
    l.gst_rate,
    l.cgst,
    l.sgst,
    l.igst,
    l.cess,
    l.total_value,
    l.total_tax,
    h.superseded_at
   FROM (t_acme_silver.sales_register_line l
     JOIN t_acme_silver.sales_register h ON ((h.id = l.header_id)));


ALTER VIEW t_acme_silver.v1_sales_register_line OWNER TO tenant_migrate;

--
-- Name: v1_stock_inventory_register; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_stock_inventory_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    sku,
    description,
    hsn,
    opening_qty,
    closing_qty,
    write_off_qty,
    write_off_value,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.stock_inventory_register;


ALTER VIEW t_acme_silver.v1_stock_inventory_register OWNER TO tenant_migrate;

--
-- Name: v1_transaction_document; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_transaction_document AS
 SELECT doc_id,
    batch_id,
    entity_id,
    doc_type,
    direction,
    doc_number,
    doc_date,
    counterparty_gstin,
    counterparty_name,
    total_taxable_value,
    total_tax_value,
    total_value,
    currency,
    attributes,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    bronze_ingest_id
   FROM t_acme_silver.transaction_document;


ALTER VIEW t_acme_silver.v1_transaction_document OWNER TO tenant_migrate;

--
-- Name: v1_transaction_line; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_transaction_line AS
 SELECT l.line_id,
    l.doc_id,
    d.batch_id,
    d.doc_type,
    d.direction,
    l.line_number,
    l.hsn_sac,
    l.description,
    l.quantity,
    l.unit_of_measure,
    l.unit_price,
    l.taxable_value,
    l.gst_rate,
    l.cgst_amount,
    l.sgst_amount,
    l.igst_amount,
    l.cess_amount,
    l.itc_amount,
    l.attributes
   FROM (t_acme_silver.transaction_line l
     JOIN t_acme_silver.transaction_document d ON ((d.doc_id = l.doc_id)));


ALTER VIEW t_acme_silver.v1_transaction_line OWNER TO tenant_migrate;

--
-- Name: v1_trial_balance; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_trial_balance AS
 SELECT id,
    entity_id,
    gstin,
    fy,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    gl_code,
    account_description,
    opening_balance,
    closing_balance,
    dr_cr,
    gst_treatment,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.trial_balance;


ALTER VIEW t_acme_silver.v1_trial_balance OWNER TO tenant_migrate;

--
-- Name: v1_unbilled_revenue_schedule; Type: VIEW; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE VIEW t_acme_silver.v1_unbilled_revenue_schedule AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    item_id,
    description,
    contract_ref,
    customer,
    customer_gstin,
    amount,
    date_of_service_completion,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_acme_silver.unbilled_revenue_schedule;


ALTER VIEW t_acme_silver.v1_unbilled_revenue_schedule OWNER TO tenant_migrate;

--
-- Name: __schema_identity; Type: TABLE; Schema: t_globex_bronze; Owner: tenant_migrate
--

CREATE TABLE t_globex_bronze.__schema_identity (
    singleton boolean DEFAULT true NOT NULL,
    tenant_slug text NOT NULL,
    tenant_id uuid NOT NULL,
    layer text NOT NULL,
    provisioned_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT __schema_identity_singleton_check CHECK (singleton)
);


ALTER TABLE t_globex_bronze.__schema_identity OWNER TO tenant_migrate;

--
-- Name: artefact_ledger; Type: TABLE; Schema: t_globex_bronze; Owner: tenant_migrate
--

CREATE TABLE t_globex_bronze.artefact_ledger (
    ingest_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    declared_document_type text NOT NULL,
    declared_document_type_vt text DEFAULT 'Document_Type'::text NOT NULL,
    source_stream text NOT NULL,
    source_stream_vt text DEFAULT 'Source_Stream'::text NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    content_hash bytea NOT NULL,
    object_key text NOT NULL,
    object_bucket text NOT NULL,
    size_bytes bigint NOT NULL,
    original_filename text,
    received_from text NOT NULL,
    CONSTRAINT artefact_ledger_declared_document_type_vt_check CHECK ((declared_document_type_vt = 'Document_Type'::text)),
    CONSTRAINT artefact_ledger_size_bytes_check CHECK ((size_bytes >= 0)),
    CONSTRAINT artefact_ledger_source_stream_vt_check CHECK ((source_stream_vt = 'Source_Stream'::text))
);


ALTER TABLE t_globex_bronze.artefact_ledger OWNER TO tenant_migrate;

--
-- Name: __schema_identity; Type: TABLE; Schema: t_globex_gold; Owner: tenant_migrate
--

CREATE TABLE t_globex_gold.__schema_identity (
    singleton boolean DEFAULT true NOT NULL,
    tenant_slug text NOT NULL,
    tenant_id uuid NOT NULL,
    layer text NOT NULL,
    provisioned_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT __schema_identity_singleton_check CHECK (singleton)
);


ALTER TABLE t_globex_gold.__schema_identity OWNER TO tenant_migrate;

--
-- Name: batch_execution; Type: TABLE; Schema: t_globex_gold; Owner: tenant_migrate
--

CREATE TABLE t_globex_gold.batch_execution (
    batch_id uuid NOT NULL,
    rule_catalog_version text NOT NULL,
    corpus_version text NOT NULL,
    tenant_pack_version text NOT NULL,
    executed_at timestamp with time zone DEFAULT now() NOT NULL,
    row_count integer NOT NULL,
    CONSTRAINT batch_execution_row_count_check CHECK ((row_count >= 0))
);


ALTER TABLE t_globex_gold.batch_execution OWNER TO tenant_migrate;

--
-- Name: consumer_watermark; Type: TABLE; Schema: t_globex_gold; Owner: tenant_migrate
--

CREATE TABLE t_globex_gold.consumer_watermark (
    consumer_name text NOT NULL,
    document_type text NOT NULL,
    last_ready_at timestamp with time zone NOT NULL,
    last_batch_id uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE t_globex_gold.consumer_watermark OWNER TO tenant_migrate;

--
-- Name: fact_record; Type: TABLE; Schema: t_globex_gold; Owner: tenant_migrate
--

CREATE TABLE t_globex_gold.fact_record (
    fact_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    invoice_id uuid NOT NULL,
    rule_id text NOT NULL,
    fact_type text NOT NULL,
    severity text NOT NULL,
    detail text NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    rule_catalog_version text NOT NULL,
    corpus_version text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fact_record_severity_check CHECK ((severity = ANY (ARRAY['INFO'::text, 'WARN'::text, 'CRITICAL'::text])))
);


ALTER TABLE t_globex_gold.fact_record OWNER TO tenant_migrate;

--
-- Name: __migration_version; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.__migration_version (
    filename text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    checksum text NOT NULL
);


ALTER TABLE t_globex_silver.__migration_version OWNER TO tenant_migrate;

--
-- Name: __schema_identity; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.__schema_identity (
    singleton boolean DEFAULT true NOT NULL,
    tenant_slug text NOT NULL,
    tenant_id uuid NOT NULL,
    layer text NOT NULL,
    provisioned_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT __schema_identity_singleton_check CHECK (singleton)
);


ALTER TABLE t_globex_silver.__schema_identity OWNER TO tenant_migrate;

--
-- Name: advance_receipt_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.advance_receipt_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'ADVANCE_RECEIPT_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    receipt_no text,
    receipt_date date NOT NULL,
    amount platform_ref.money_inr NOT NULL,
    customer text,
    customer_gstin platform_ref.gstin,
    supply_type text,
    gst_rate platform_ref.tax_rate,
    gst_paid_on_advance platform_ref.money_inr DEFAULT 0,
    invoice_linkage text,
    is_adjusted boolean DEFAULT false NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT advance_receipt_register_doc_type_code_check CHECK ((doc_type_code = 'ADVANCE_RECEIPT_REGISTER'::text)),
    CONSTRAINT advance_receipt_register_supply_type_check CHECK ((supply_type = ANY (ARRAY['GOODS'::text, 'SERVICE'::text]))),
    CONSTRAINT advance_receipt_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.advance_receipt_register OWNER TO tenant_migrate;

--
-- Name: advance_receipt_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.advance_receipt_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.advance_receipt_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audited_pl_account; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.audited_pl_account (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    fy text NOT NULL,
    doc_type_code text DEFAULT 'AUDITED_PROFIT_AND_LOSS'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    gl_code text,
    gl_account text NOT NULL,
    narration text,
    account_type text,
    amount platform_ref.money_inr NOT NULL,
    dr_cr character(2),
    gst_treatment text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT audited_pl_account_account_type_check CHECK ((account_type = ANY (ARRAY['REVENUE'::text, 'EXPENSE'::text, 'OTHER'::text]))),
    CONSTRAINT audited_pl_account_doc_type_code_check CHECK ((doc_type_code = 'AUDITED_PROFIT_AND_LOSS'::text)),
    CONSTRAINT audited_pl_account_dr_cr_check CHECK ((dr_cr = ANY (ARRAY['DR'::bpchar, 'CR'::bpchar]))),
    CONSTRAINT audited_pl_account_fy_check CHECK ((fy ~ '^[0-9]{4}-[0-9]{2}$'::text)),
    CONSTRAINT audited_pl_account_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.audited_pl_account OWNER TO tenant_migrate;

--
-- Name: audited_pl_account_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.audited_pl_account ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.audited_pl_account_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: balance_sheet; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.balance_sheet (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    fy text NOT NULL,
    doc_type_code text DEFAULT 'BALANCE_SHEET'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    line_item text NOT NULL,
    gl_code text,
    category text,
    amount platform_ref.money_inr NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT balance_sheet_category_check CHECK ((category = ANY (ARRAY['ADVANCE_FROM_CUSTOMER'::text, 'UNBILLED_REVENUE'::text, 'DEFERRED_REVENUE'::text, 'CREDITOR'::text, 'CAPITAL_GOODS'::text, 'OTHER'::text]))),
    CONSTRAINT balance_sheet_doc_type_code_check CHECK ((doc_type_code = 'BALANCE_SHEET'::text)),
    CONSTRAINT balance_sheet_fy_check CHECK ((fy ~ '^[0-9]{4}-[0-9]{2}$'::text)),
    CONSTRAINT balance_sheet_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.balance_sheet OWNER TO tenant_migrate;

--
-- Name: balance_sheet_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.balance_sheet ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.balance_sheet_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bank_statement_inward_forex; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.bank_statement_inward_forex (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'BANK_STATEMENT_INWARD_FX'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    txn_date date NOT NULL,
    amount_fcy numeric(18,2) NOT NULL,
    currency character(3) NOT NULL,
    amount_inr platform_ref.money_inr,
    remitter text,
    ad_bank_ref text,
    export_invoice_ref text,
    firc_no text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT bank_statement_inward_forex_doc_type_code_check CHECK ((doc_type_code = 'BANK_STATEMENT_INWARD_FX'::text)),
    CONSTRAINT bank_statement_inward_forex_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.bank_statement_inward_forex OWNER TO tenant_migrate;

--
-- Name: bank_statement_inward_forex_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.bank_statement_inward_forex ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.bank_statement_inward_forex_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bank_statement_outward; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.bank_statement_outward (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'BANK_STATEMENT_OUTWARD'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    txn_date date NOT NULL,
    amount platform_ref.money_inr NOT NULL,
    beneficiary text,
    beneficiary_account text,
    narration text,
    bank_ref text,
    invoice_ref text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT bank_statement_outward_doc_type_code_check CHECK ((doc_type_code = 'BANK_STATEMENT_OUTWARD'::text)),
    CONSTRAINT bank_statement_outward_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.bank_statement_outward OWNER TO tenant_migrate;

--
-- Name: bank_statement_outward_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.bank_statement_outward ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.bank_statement_outward_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: common_input_service_invoice; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.common_input_service_invoice (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'HO_COMMON_INPUT_SERVICE_INVOICE'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    supplier_gstin platform_ref.gstin,
    service_category text,
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT common_input_service_invoice_doc_type_code_check CHECK ((doc_type_code = 'HO_COMMON_INPUT_SERVICE_INVOICE'::text)),
    CONSTRAINT common_input_service_invoice_service_category_check CHECK ((service_category = ANY (ARRAY['RENT'::text, 'IT'::text, 'INSURANCE'::text, 'AUDIT'::text, 'OTHER'::text]))),
    CONSTRAINT common_input_service_invoice_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.common_input_service_invoice OWNER TO tenant_migrate;

--
-- Name: common_input_service_invoice_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.common_input_service_invoice ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.common_input_service_invoice_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cost_allocation_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.cost_allocation_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'COST_ALLOCATION_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    from_gstin platform_ref.gstin,
    to_gstin platform_ref.gstin,
    allocation_basis text,
    description text,
    amount platform_ref.money_inr NOT NULL,
    has_invoice boolean DEFAULT false NOT NULL,
    invoice_no text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT cost_allocation_register_doc_type_code_check CHECK ((doc_type_code = 'COST_ALLOCATION_REGISTER'::text)),
    CONSTRAINT cost_allocation_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.cost_allocation_register OWNER TO tenant_migrate;

--
-- Name: cost_allocation_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.cost_allocation_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.cost_allocation_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: credit_debit_note_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.credit_debit_note_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'CREDIT_DEBIT_NOTE_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    note_type text NOT NULL,
    note_no text NOT NULL,
    note_date date NOT NULL,
    original_invoice_no text NOT NULL,
    customer_gstin platform_ref.gstin,
    supply_type text,
    reason_code text,
    reason_text text,
    adjusted_taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    cess platform_ref.money_inr DEFAULT 0,
    note_value platform_ref.money_inr,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT credit_debit_note_register_doc_type_code_check CHECK ((doc_type_code = 'CREDIT_DEBIT_NOTE_REGISTER'::text)),
    CONSTRAINT credit_debit_note_register_note_type_check CHECK ((note_type = ANY (ARRAY['CN'::text, 'DN'::text]))),
    CONSTRAINT credit_debit_note_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.credit_debit_note_register OWNER TO tenant_migrate;

--
-- Name: credit_debit_note_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.credit_debit_note_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.credit_debit_note_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: creditor_ageing_report; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.creditor_ageing_report (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'CREDITOR_AGEING_REPORT'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    outstanding_amount platform_ref.money_inr NOT NULL,
    ageing_days integer,
    ageing_bucket text,
    as_at_date date NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT creditor_ageing_report_doc_type_code_check CHECK ((doc_type_code = 'CREDITOR_AGEING_REPORT'::text)),
    CONSTRAINT creditor_ageing_report_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.creditor_ageing_report OWNER TO tenant_migrate;

--
-- Name: creditor_ageing_report_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.creditor_ageing_report ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.creditor_ageing_report_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: entitlement_instrument; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.entitlement_instrument (
    instrument_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    instrument_type text NOT NULL,
    instrument_archetype smallint DEFAULT 3 NOT NULL,
    issuing_authority text NOT NULL,
    issuing_authority_vt text DEFAULT 'Issuing_Authority'::text NOT NULL,
    instrument_number text NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    status_vt text DEFAULT 'Instrument_Status'::text NOT NULL,
    scope_hsn text[],
    scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    obligation jsonb DEFAULT '{}'::jsonb NOT NULL,
    supersedes_instrument_id uuid,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    CONSTRAINT entitlement_instrument_instrument_archetype_check CHECK ((instrument_archetype = 3)),
    CONSTRAINT entitlement_instrument_issuing_authority_vt_check CHECK ((issuing_authority_vt = 'Issuing_Authority'::text)),
    CONSTRAINT entitlement_instrument_scope_hsn_check CHECK (((scope_hsn IS NULL) OR (cardinality(scope_hsn) > 0))),
    CONSTRAINT entitlement_instrument_status_vt_check CHECK ((status_vt = 'Instrument_Status'::text)),
    CONSTRAINT entitlement_instrument_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.entitlement_instrument OWNER TO tenant_migrate;

--
-- Name: TABLE entitlement_instrument; Type: COMMENT; Schema: t_globex_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_globex_silver.entitlement_instrument IS 'Archetype 3 (ARCHITECTURE.md 6). Thirty-three document types collapse here. Adding the thirty-fourth entitlement instrument must be a registry row, not a table — if it needs a schema change, the archetype abstraction has failed.';


--
-- Name: fixed_asset_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.fixed_asset_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'FIXED_ASSET_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    asset_id text NOT NULL,
    description text,
    hsn text,
    purchase_date date,
    purchase_value platform_ref.money_inr,
    itc_availed platform_ref.money_inr DEFAULT 0,
    useful_life_months integer,
    date_of_disposal date,
    disposal_value platform_ref.money_inr,
    disposal_type text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT fixed_asset_register_disposal_type_check CHECK ((disposal_type = ANY (ARRAY['SALE'::text, 'SCRAP'::text, 'TRANSFER'::text, 'WRITE_OFF'::text, 'OTHER'::text]))),
    CONSTRAINT fixed_asset_register_doc_type_code_check CHECK ((doc_type_code = 'FIXED_ASSET_REGISTER'::text)),
    CONSTRAINT fixed_asset_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.fixed_asset_register OWNER TO tenant_migrate;

--
-- Name: fixed_asset_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.fixed_asset_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.fixed_asset_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: foreign_currency_payment_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.foreign_currency_payment_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'FOREIGN_CURRENCY_PAYMENT_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    payment_date date NOT NULL,
    amount_fcy numeric(18,2) NOT NULL,
    currency character(3) NOT NULL,
    amount_inr platform_ref.money_inr,
    vendor text,
    country text,
    purpose text,
    invoice_contract_ref text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT foreign_currency_payment_register_doc_type_code_check CHECK ((doc_type_code = 'FOREIGN_CURRENCY_PAYMENT_REGISTER'::text)),
    CONSTRAINT foreign_currency_payment_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.foreign_currency_payment_register OWNER TO tenant_migrate;

--
-- Name: foreign_currency_payment_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.foreign_currency_payment_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.foreign_currency_payment_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ingest_batch; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.ingest_batch (
    batch_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    document_type text NOT NULL,
    document_type_vt text DEFAULT 'Document_Type'::text NOT NULL,
    source_stream text NOT NULL,
    source_stream_vt text DEFAULT 'Source_Stream'::text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    row_count integer NOT NULL,
    content_hash bytea NOT NULL,
    bronze_manifest_ref text NOT NULL,
    status text DEFAULT 'READY'::text NOT NULL,
    status_vt text DEFAULT 'Batch_Status'::text NOT NULL,
    ready_at timestamp with time zone DEFAULT now() NOT NULL,
    ingest_run_id uuid NOT NULL,
    superseded_by uuid,
    CONSTRAINT ingest_batch_document_type_vt_check CHECK ((document_type_vt = 'Document_Type'::text)),
    CONSTRAINT ingest_batch_period CHECK ((period_end >= period_start)),
    CONSTRAINT ingest_batch_row_count_check CHECK ((row_count >= 0)),
    CONSTRAINT ingest_batch_source_stream_vt_check CHECK ((source_stream_vt = 'Source_Stream'::text)),
    CONSTRAINT ingest_batch_status_vt_check CHECK ((status_vt = 'Batch_Status'::text))
);


ALTER TABLE t_globex_silver.ingest_batch OWNER TO tenant_migrate;

--
-- Name: inter_gstin_transaction_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.inter_gstin_transaction_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'INTER_GSTIN_TRANSACTION_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    from_gstin platform_ref.gstin NOT NULL,
    to_gstin platform_ref.gstin NOT NULL,
    txn_type text,
    txn_date date NOT NULL,
    invoice_no text,
    description text,
    hsn_sac text,
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT inter_gstin_transaction_register_doc_type_code_check CHECK ((doc_type_code = 'INTER_GSTIN_TRANSACTION_REGISTER'::text)),
    CONSTRAINT inter_gstin_transaction_register_txn_type_check CHECK ((txn_type = ANY (ARRAY['STOCK_TRANSFER'::text, 'CROSS_CHARGE'::text, 'CAPITAL_GOOD_TRANSFER'::text, 'OTHER'::text]))),
    CONSTRAINT inter_gstin_transaction_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.inter_gstin_transaction_register OWNER TO tenant_migrate;

--
-- Name: inter_gstin_transaction_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.inter_gstin_transaction_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.inter_gstin_transaction_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: itc_reversal_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.itc_reversal_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'ITC_REVERSAL_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    reversal_type text NOT NULL,
    reversal_basis text,
    cgst_reversed platform_ref.money_inr DEFAULT 0,
    sgst_reversed platform_ref.money_inr DEFAULT 0,
    igst_reversed platform_ref.money_inr DEFAULT 0,
    cess_reversed platform_ref.money_inr DEFAULT 0,
    remarks text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT itc_reversal_register_doc_type_code_check CHECK ((doc_type_code = 'ITC_REVERSAL_REGISTER'::text)),
    CONSTRAINT itc_reversal_register_reversal_type_check CHECK ((reversal_type = ANY (ARRAY['RULE_42'::text, 'RULE_43'::text, 'SEC_17_5'::text, 'OTHER'::text]))),
    CONSTRAINT itc_reversal_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.itc_reversal_register OWNER TO tenant_migrate;

--
-- Name: itc_reversal_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.itc_reversal_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.itc_reversal_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: job_work_dispatch_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.job_work_dispatch_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'JOBWORK_DISPATCH_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    challan_no text,
    date_of_goods_dispatch_to_job_worker date NOT NULL,
    description text,
    hsn text,
    qty platform_ref.qty,
    uom text,
    taxable_value platform_ref.money_inr,
    job_worker_gstin platform_ref.gstin,
    expected_return_date date,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT job_work_dispatch_register_doc_type_code_check CHECK ((doc_type_code = 'JOBWORK_DISPATCH_REGISTER'::text)),
    CONSTRAINT job_work_dispatch_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.job_work_dispatch_register OWNER TO tenant_migrate;

--
-- Name: job_work_dispatch_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.job_work_dispatch_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.job_work_dispatch_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payment_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.payment_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'SUPPLIER_PAYMENT_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    payment_date date NOT NULL,
    amount platform_ref.money_inr NOT NULL,
    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text,
    payment_ref text,
    payment_mode text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT payment_register_doc_type_code_check CHECK ((doc_type_code = 'SUPPLIER_PAYMENT_REGISTER'::text)),
    CONSTRAINT payment_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.payment_register OWNER TO tenant_migrate;

--
-- Name: payment_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.payment_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.payment_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: platform_settlement_report; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.platform_settlement_report (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    report_type text NOT NULL,
    operator text NOT NULL,
    order_no text,
    order_date date,
    invoice_no text,
    invoice_date date,
    buyer_state text,
    taxable_value platform_ref.money_inr,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    tcs platform_ref.money_inr DEFAULT 0,
    marketplace_fee platform_ref.money_inr DEFAULT 0,
    shipping_charges platform_ref.money_inr DEFAULT 0,
    net_payout platform_ref.money_inr,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT platform_settlement_report_doc_type_code_check CHECK ((doc_type_code = ANY (ARRAY['AMAZON_MTR'::text, 'FLIPKART_GTR'::text, 'MARKETPLACE_SETTLEMENT_SSR'::text, 'MARKETPLACE_DISBURSEMENT_DRR'::text, 'MARKETPLACE_RETURNS_VRET'::text, 'FOOD_DELIVERY_PARTNER_REPORT'::text, 'OTHER_MARKETPLACE_SETTLEMENT'::text]))),
    CONSTRAINT platform_settlement_report_report_type_check CHECK ((report_type = ANY (ARRAY['MTR'::text, 'GTR'::text, 'SSR'::text, 'DRR'::text, 'VRET'::text, 'FDP'::text, 'OTHER'::text]))),
    CONSTRAINT platform_settlement_report_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.platform_settlement_report OWNER TO tenant_migrate;

--
-- Name: platform_settlement_report_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.platform_settlement_report ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.platform_settlement_report_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: product_sku_master; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.product_sku_master (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'SKU_MASTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    sku text NOT NULL,
    description text,
    hsn text,
    mrp_applicable boolean DEFAULT false NOT NULL,
    declared_mrp platform_ref.money_inr,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT product_sku_master_doc_type_code_check CHECK ((doc_type_code = 'SKU_MASTER'::text)),
    CONSTRAINT product_sku_master_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.product_sku_master OWNER TO tenant_migrate;

--
-- Name: product_sku_master_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.product_sku_master ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.product_sku_master_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: purchase_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.purchase_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'PURCHASE_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    supplier_gstin platform_ref.gstin,
    invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    hsn_sac text,
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    cess platform_ref.money_inr DEFAULT 0,
    gl_code text NOT NULL,
    cost_centre text NOT NULL,
    itc_eligibility text,
    rcm_flag boolean DEFAULT false NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT purchase_register_doc_type_code_check CHECK ((doc_type_code = 'PURCHASE_REGISTER'::text)),
    CONSTRAINT purchase_register_itc_eligibility_check CHECK ((itc_eligibility = ANY (ARRAY['ELIGIBLE'::text, 'INELIGIBLE'::text, 'BLOCKED'::text, 'PARTIAL'::text]))),
    CONSTRAINT purchase_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.purchase_register OWNER TO tenant_migrate;

--
-- Name: purchase_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.purchase_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.purchase_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: quarantined_artefact; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.quarantined_artefact (
    ingest_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    document_type text NOT NULL,
    document_type_vt text DEFAULT 'Document_Type'::text NOT NULL,
    rejected_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text NOT NULL,
    ingest_run_id uuid,
    CONSTRAINT quarantined_artefact_document_type_vt_check CHECK ((document_type_vt = 'Document_Type'::text))
);


ALTER TABLE t_globex_silver.quarantined_artefact OWNER TO tenant_migrate;

--
-- Name: TABLE quarantined_artefact; Type: COMMENT; Schema: t_globex_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_globex_silver.quarantined_artefact IS 'Artefacts that failed structural validation. The bytes remain in Bronze under Object Lock; this is the record of why they were not promoted.';


--
-- Name: rcm_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.rcm_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'RCM_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    rcm_category text NOT NULL,
    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text,
    invoice_date date,
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    rcm_liability platform_ref.money_inr,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    paid_period date,
    is_paid boolean DEFAULT false NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT rcm_register_doc_type_code_check CHECK ((doc_type_code = 'RCM_REGISTER'::text)),
    CONSTRAINT rcm_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.rcm_register OWNER TO tenant_migrate;

--
-- Name: rcm_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.rcm_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.rcm_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: running_account_contract_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.running_account_contract_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'CONTINUOUS_SUPPLY_CONTRACT_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    contract_ref text NOT NULL,
    customer text,
    customer_gstin platform_ref.gstin,
    billing_frequency text,
    statement_date date,
    delivery_date date,
    amount platform_ref.money_inr,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT running_account_contract_register_doc_type_code_check CHECK ((doc_type_code = 'CONTINUOUS_SUPPLY_CONTRACT_REGISTER'::text)),
    CONSTRAINT running_account_contract_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.running_account_contract_register OWNER TO tenant_migrate;

--
-- Name: running_account_contract_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.running_account_contract_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.running_account_contract_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sales_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.sales_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'SALES_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    document_hash text NOT NULL,
    invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    customer_gstin platform_ref.gstin,
    invoice_type text NOT NULL,
    invoice_type_vt text DEFAULT 'Invoice_Type'::text NOT NULL,
    supply_type text,
    supply_type_vt text DEFAULT 'Supply_Type'::text NOT NULL,
    place_of_supply text,
    reverse_charge boolean DEFAULT false NOT NULL,
    currency character(3) DEFAULT 'INR'::bpchar NOT NULL,
    trade_discount platform_ref.money_inr DEFAULT 0,
    freight platform_ref.money_inr DEFAULT 0,
    packing platform_ref.money_inr DEFAULT 0,
    insurance platform_ref.money_inr DEFAULT 0,
    taxable_value platform_ref.money_inr,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    cess platform_ref.money_inr DEFAULT 0,
    total_value platform_ref.money_inr,
    total_tax platform_ref.money_inr GENERATED ALWAYS AS ((((COALESCE((cgst)::numeric, (0)::numeric) + COALESCE((sgst)::numeric, (0)::numeric)) + COALESCE((igst)::numeric, (0)::numeric)) + COALESCE((cess)::numeric, (0)::numeric))) STORED,
    irn text,
    ewb_no text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT sales_register_doc_type_code_check CHECK ((doc_type_code = 'SALES_REGISTER'::text)),
    CONSTRAINT sales_register_invoice_type_vt_check CHECK ((invoice_type_vt = 'Invoice_Type'::text)),
    CONSTRAINT sales_register_place_of_supply_check CHECK ((place_of_supply ~ '^[0-9]{2}$'::text)),
    CONSTRAINT sales_register_supersession CHECK ((((superseded_at IS NULL) AND (modified_at IS NULL)) OR (superseded_at IS NOT NULL))),
    CONSTRAINT sales_register_supply_type_vt_check CHECK ((supply_type_vt = 'Supply_Type'::text)),
    CONSTRAINT sales_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.sales_register OWNER TO tenant_migrate;

--
-- Name: TABLE sales_register; Type: COMMENT; Schema: t_globex_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_globex_silver.sales_register IS 'A1.01 SALES_REGISTER, reconciled against reference/inafin_a1_schema.sql. One row per invoice. Header totals are stored as supplied and never reconciled against the line sum — the disagreement is a finding.';


--
-- Name: sales_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.sales_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.sales_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sales_register_line; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.sales_register_line (
    id bigint NOT NULL,
    header_id bigint NOT NULL,
    line_number integer NOT NULL,
    row_hash text NOT NULL,
    hsn_sac text,
    description text,
    qty platform_ref.qty,
    uom text,
    unit_price numeric(18,4),
    taxable_value platform_ref.money_inr NOT NULL,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr DEFAULT 0,
    sgst platform_ref.money_inr DEFAULT 0,
    igst platform_ref.money_inr DEFAULT 0,
    cess platform_ref.money_inr DEFAULT 0,
    total_value platform_ref.money_inr,
    total_tax platform_ref.money_inr GENERATED ALWAYS AS ((((COALESCE((cgst)::numeric, (0)::numeric) + COALESCE((sgst)::numeric, (0)::numeric)) + COALESCE((igst)::numeric, (0)::numeric)) + COALESCE((cess)::numeric, (0)::numeric))) STORED,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT sales_register_line_hsn_sac_check CHECK (((hsn_sac IS NULL) OR (hsn_sac ~ '^[0-9]{4,8}$'::text))),
    CONSTRAINT sales_register_line_line_number_check CHECK ((line_number > 0)),
    CONSTRAINT sales_register_line_tax_head CHECK ((((COALESCE((igst)::numeric, (0)::numeric) > (0)::numeric) AND (COALESCE((cgst)::numeric, (0)::numeric) = (0)::numeric) AND (COALESCE((sgst)::numeric, (0)::numeric) = (0)::numeric)) OR (COALESCE((igst)::numeric, (0)::numeric) = (0)::numeric)))
);


ALTER TABLE t_globex_silver.sales_register_line OWNER TO tenant_migrate;

--
-- Name: TABLE sales_register_line; Type: COMMENT; Schema: t_globex_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_globex_silver.sales_register_line IS 'A1.01 SALES_REGISTER lines. Column names follow the reference schema; the header/line split is ours, so the same names appear at both grains.';


--
-- Name: sales_register_line_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.sales_register_line ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.sales_register_line_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: stock_inventory_register; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.stock_inventory_register (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'STOCK_REGISTER'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    sku text NOT NULL,
    description text,
    hsn text,
    opening_qty platform_ref.qty,
    closing_qty platform_ref.qty,
    write_off_qty platform_ref.qty DEFAULT 0,
    write_off_value platform_ref.money_inr DEFAULT 0,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT stock_inventory_register_doc_type_code_check CHECK ((doc_type_code = 'STOCK_REGISTER'::text)),
    CONSTRAINT stock_inventory_register_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.stock_inventory_register OWNER TO tenant_migrate;

--
-- Name: stock_inventory_register_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.stock_inventory_register ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.stock_inventory_register_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: transaction_document; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.transaction_document (
    doc_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    doc_type text NOT NULL,
    doc_archetype smallint DEFAULT 1 NOT NULL,
    direction text NOT NULL,
    direction_vt text DEFAULT 'Direction'::text NOT NULL,
    doc_number text NOT NULL,
    doc_date date NOT NULL,
    counterparty_gstin text,
    counterparty_name text,
    total_taxable_value numeric(18,2) NOT NULL,
    total_tax_value numeric(18,2) NOT NULL,
    total_value numeric(18,2) NOT NULL,
    currency text DEFAULT 'INR'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    bronze_ingest_id uuid NOT NULL,
    CONSTRAINT transaction_document_counterparty_gstin_check CHECK (((counterparty_gstin IS NULL) OR (counterparty_gstin ~ '^[0-9]{2}[A-Z0-9]{13}$'::text))),
    CONSTRAINT transaction_document_direction_vt_check CHECK ((direction_vt = 'Direction'::text)),
    CONSTRAINT transaction_document_doc_archetype_check CHECK ((doc_archetype = 1)),
    CONSTRAINT transaction_document_totals CHECK ((total_value = (total_taxable_value + total_tax_value))),
    CONSTRAINT transaction_document_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.transaction_document OWNER TO tenant_migrate;

--
-- Name: TABLE transaction_document; Type: COMMENT; Schema: t_globex_silver; Owner: tenant_migrate
--

COMMENT ON TABLE t_globex_silver.transaction_document IS 'Archetype 1 (ARCHITECTURE.md 6). Eleven document types collapse here. What varies per type is platform_ref.document_type.field_contract, not a column — if a new transaction type needs a schema change, the archetype has failed.';


--
-- Name: transaction_line; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.transaction_line (
    line_id uuid NOT NULL,
    doc_id uuid NOT NULL,
    line_number integer NOT NULL,
    hsn_sac text NOT NULL,
    description text,
    quantity numeric(18,3) NOT NULL,
    unit_of_measure text,
    unit_price numeric(18,4) NOT NULL,
    taxable_value numeric(18,2) NOT NULL,
    gst_rate numeric(5,2) NOT NULL,
    cgst_amount numeric(18,2) DEFAULT 0 NOT NULL,
    sgst_amount numeric(18,2) DEFAULT 0 NOT NULL,
    igst_amount numeric(18,2) DEFAULT 0 NOT NULL,
    cess_amount numeric(18,2) DEFAULT 0 NOT NULL,
    itc_amount numeric(18,2) DEFAULT 0 NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT transaction_line_gst_rate_check CHECK (((gst_rate >= (0)::numeric) AND (gst_rate <= (100)::numeric))),
    CONSTRAINT transaction_line_hsn_sac_check CHECK ((hsn_sac ~ '^[0-9]{4,8}$'::text)),
    CONSTRAINT transaction_line_line_number_check CHECK ((line_number > 0)),
    CONSTRAINT transaction_line_tax_head CHECK ((((igst_amount > (0)::numeric) AND (cgst_amount = (0)::numeric) AND (sgst_amount = (0)::numeric)) OR (igst_amount = (0)::numeric)))
);


ALTER TABLE t_globex_silver.transaction_line OWNER TO tenant_migrate;

--
-- Name: trial_balance; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.trial_balance (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    fy text NOT NULL,
    doc_type_code text DEFAULT 'TRIAL_BALANCE'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    gl_code text NOT NULL,
    account_description text NOT NULL,
    opening_balance platform_ref.money_inr DEFAULT 0,
    closing_balance platform_ref.money_inr DEFAULT 0,
    dr_cr character(2),
    gst_treatment text,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT trial_balance_doc_type_code_check CHECK ((doc_type_code = 'TRIAL_BALANCE'::text)),
    CONSTRAINT trial_balance_dr_cr_check CHECK ((dr_cr = ANY (ARRAY['DR'::bpchar, 'CR'::bpchar]))),
    CONSTRAINT trial_balance_fy_check CHECK ((fy ~ '^[0-9]{4}-[0-9]{2}$'::text)),
    CONSTRAINT trial_balance_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.trial_balance OWNER TO tenant_migrate;

--
-- Name: trial_balance_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.trial_balance ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.trial_balance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: unbilled_revenue_schedule; Type: TABLE; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE TABLE t_globex_silver.unbilled_revenue_schedule (
    id bigint NOT NULL,
    entity_id uuid NOT NULL,
    gstin platform_ref.gstin NOT NULL,
    tax_period date NOT NULL,
    doc_type_code text DEFAULT 'UNBILLED_REVENUE_SCHEDULE'::text NOT NULL,
    batch_id uuid NOT NULL,
    bronze_ingest_id uuid NOT NULL,
    row_hash text NOT NULL,
    item_id text,
    description text,
    contract_ref text,
    customer text,
    customer_gstin platform_ref.gstin,
    amount platform_ref.money_inr NOT NULL,
    date_of_service_completion date,
    valid_from date NOT NULL,
    valid_to date DEFAULT '9999-12-31'::date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT CURRENT_USER NOT NULL,
    modified_at timestamp with time zone,
    modified_by text,
    CONSTRAINT unbilled_revenue_schedule_doc_type_code_check CHECK ((doc_type_code = 'UNBILLED_REVENUE_SCHEDULE'::text)),
    CONSTRAINT unbilled_revenue_schedule_window CHECK ((valid_to > valid_from))
);


ALTER TABLE t_globex_silver.unbilled_revenue_schedule OWNER TO tenant_migrate;

--
-- Name: unbilled_revenue_schedule_id_seq; Type: SEQUENCE; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE t_globex_silver.unbilled_revenue_schedule ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME t_globex_silver.unbilled_revenue_schedule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: v1_advance_receipt_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_advance_receipt_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    receipt_no,
    receipt_date,
    amount,
    customer,
    customer_gstin,
    supply_type,
    gst_rate,
    gst_paid_on_advance,
    invoice_linkage,
    is_adjusted,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.advance_receipt_register;


ALTER VIEW t_globex_silver.v1_advance_receipt_register OWNER TO tenant_migrate;

--
-- Name: v1_audited_pl_account; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_audited_pl_account AS
 SELECT id,
    entity_id,
    gstin,
    fy,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    gl_code,
    gl_account,
    narration,
    account_type,
    amount,
    dr_cr,
    gst_treatment,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.audited_pl_account;


ALTER VIEW t_globex_silver.v1_audited_pl_account OWNER TO tenant_migrate;

--
-- Name: v1_balance_sheet; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_balance_sheet AS
 SELECT id,
    entity_id,
    gstin,
    fy,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    line_item,
    gl_code,
    category,
    amount,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.balance_sheet;


ALTER VIEW t_globex_silver.v1_balance_sheet OWNER TO tenant_migrate;

--
-- Name: v1_bank_statement_inward_forex; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_bank_statement_inward_forex AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    txn_date,
    amount_fcy,
    currency,
    amount_inr,
    remitter,
    ad_bank_ref,
    export_invoice_ref,
    firc_no,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.bank_statement_inward_forex;


ALTER VIEW t_globex_silver.v1_bank_statement_inward_forex OWNER TO tenant_migrate;

--
-- Name: v1_bank_statement_outward; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_bank_statement_outward AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    txn_date,
    amount,
    beneficiary,
    beneficiary_account,
    narration,
    bank_ref,
    invoice_ref,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.bank_statement_outward;


ALTER VIEW t_globex_silver.v1_bank_statement_outward OWNER TO tenant_migrate;

--
-- Name: v1_common_input_service_invoice; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_common_input_service_invoice AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    invoice_no,
    invoice_date,
    supplier_gstin,
    service_category,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.common_input_service_invoice;


ALTER VIEW t_globex_silver.v1_common_input_service_invoice OWNER TO tenant_migrate;

--
-- Name: v1_cost_allocation_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_cost_allocation_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    from_gstin,
    to_gstin,
    allocation_basis,
    description,
    amount,
    has_invoice,
    invoice_no,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.cost_allocation_register;


ALTER VIEW t_globex_silver.v1_cost_allocation_register OWNER TO tenant_migrate;

--
-- Name: v1_credit_debit_note_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_credit_debit_note_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    note_type,
    note_no,
    note_date,
    original_invoice_no,
    customer_gstin,
    supply_type,
    reason_code,
    reason_text,
    adjusted_taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    cess,
    note_value,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.credit_debit_note_register;


ALTER VIEW t_globex_silver.v1_credit_debit_note_register OWNER TO tenant_migrate;

--
-- Name: v1_creditor_ageing_report; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_creditor_ageing_report AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    supplier_gstin,
    supplier_name,
    invoice_no,
    invoice_date,
    outstanding_amount,
    ageing_days,
    ageing_bucket,
    as_at_date,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.creditor_ageing_report;


ALTER VIEW t_globex_silver.v1_creditor_ageing_report OWNER TO tenant_migrate;

--
-- Name: v1_entitlement_instrument; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_entitlement_instrument AS
 SELECT instrument_id,
    entity_id,
    instrument_type,
    issuing_authority,
    instrument_number,
    valid_from,
    valid_to,
    status,
    scope_hsn,
    scope,
    obligation,
    supersedes_instrument_id,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id
   FROM t_globex_silver.entitlement_instrument;


ALTER VIEW t_globex_silver.v1_entitlement_instrument OWNER TO tenant_migrate;

--
-- Name: v1_fixed_asset_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_fixed_asset_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    asset_id,
    description,
    hsn,
    purchase_date,
    purchase_value,
    itc_availed,
    useful_life_months,
    date_of_disposal,
    disposal_value,
    disposal_type,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.fixed_asset_register;


ALTER VIEW t_globex_silver.v1_fixed_asset_register OWNER TO tenant_migrate;

--
-- Name: v1_foreign_currency_payment_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_foreign_currency_payment_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    payment_date,
    amount_fcy,
    currency,
    amount_inr,
    vendor,
    country,
    purpose,
    invoice_contract_ref,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.foreign_currency_payment_register;


ALTER VIEW t_globex_silver.v1_foreign_currency_payment_register OWNER TO tenant_migrate;

--
-- Name: v1_ingest_batch; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_ingest_batch AS
 SELECT batch_id,
    entity_id,
    document_type,
    source_stream,
    period_start,
    period_end,
    row_count,
    content_hash,
    bronze_manifest_ref,
    status,
    ready_at,
    ingest_run_id,
    superseded_by
   FROM t_globex_silver.ingest_batch;


ALTER VIEW t_globex_silver.v1_ingest_batch OWNER TO tenant_migrate;

--
-- Name: v1_inter_gstin_transaction_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_inter_gstin_transaction_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    from_gstin,
    to_gstin,
    txn_type,
    txn_date,
    invoice_no,
    description,
    hsn_sac,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.inter_gstin_transaction_register;


ALTER VIEW t_globex_silver.v1_inter_gstin_transaction_register OWNER TO tenant_migrate;

--
-- Name: v1_itc_reversal_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_itc_reversal_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    reversal_type,
    reversal_basis,
    cgst_reversed,
    sgst_reversed,
    igst_reversed,
    cess_reversed,
    remarks,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.itc_reversal_register;


ALTER VIEW t_globex_silver.v1_itc_reversal_register OWNER TO tenant_migrate;

--
-- Name: v1_job_work_dispatch_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_job_work_dispatch_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    challan_no,
    date_of_goods_dispatch_to_job_worker,
    description,
    hsn,
    qty,
    uom,
    taxable_value,
    job_worker_gstin,
    expected_return_date,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.job_work_dispatch_register;


ALTER VIEW t_globex_silver.v1_job_work_dispatch_register OWNER TO tenant_migrate;

--
-- Name: v1_payment_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_payment_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    payment_date,
    amount,
    supplier_gstin,
    supplier_name,
    invoice_no,
    payment_ref,
    payment_mode,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.payment_register;


ALTER VIEW t_globex_silver.v1_payment_register OWNER TO tenant_migrate;

--
-- Name: v1_platform_settlement_report; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_platform_settlement_report AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    report_type,
    operator,
    order_no,
    order_date,
    invoice_no,
    invoice_date,
    buyer_state,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    tcs,
    marketplace_fee,
    shipping_charges,
    net_payout,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.platform_settlement_report;


ALTER VIEW t_globex_silver.v1_platform_settlement_report OWNER TO tenant_migrate;

--
-- Name: v1_product_sku_master; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_product_sku_master AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    sku,
    description,
    hsn,
    mrp_applicable,
    declared_mrp,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.product_sku_master;


ALTER VIEW t_globex_silver.v1_product_sku_master OWNER TO tenant_migrate;

--
-- Name: v1_purchase_invoice; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_purchase_invoice AS
 SELECT doc_id AS invoice_id,
    batch_id,
    entity_id,
    doc_number AS invoice_number,
    counterparty_gstin AS supplier_gstin,
    doc_date AS invoice_date,
    total_taxable_value,
    total_tax_value,
    total_value,
    ((attributes ->> 'payment_due_date'::text))::date AS payment_due_date,
    currency,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    bronze_ingest_id
   FROM t_globex_silver.transaction_document
  WHERE (doc_type = 'PURCHASE_REGISTER'::text);


ALTER VIEW t_globex_silver.v1_purchase_invoice OWNER TO tenant_migrate;

--
-- Name: v1_purchase_invoice_line; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_purchase_invoice_line AS
 SELECT l.line_id,
    l.doc_id AS invoice_id,
    d.batch_id,
    l.line_number,
    l.hsn_sac,
    l.description,
    l.quantity,
    l.unit_of_measure,
    l.unit_price,
    l.taxable_value,
    l.gst_rate,
    l.cgst_amount,
    l.sgst_amount,
    l.igst_amount,
    l.itc_amount
   FROM (t_globex_silver.transaction_line l
     JOIN t_globex_silver.transaction_document d ON ((d.doc_id = l.doc_id)))
  WHERE (d.doc_type = 'PURCHASE_REGISTER'::text);


ALTER VIEW t_globex_silver.v1_purchase_invoice_line OWNER TO tenant_migrate;

--
-- Name: v1_purchase_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_purchase_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    supplier_gstin,
    invoice_no,
    invoice_date,
    hsn_sac,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    cess,
    gl_code,
    cost_centre,
    itc_eligibility,
    rcm_flag,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.purchase_register;


ALTER VIEW t_globex_silver.v1_purchase_register OWNER TO tenant_migrate;

--
-- Name: v1_rcm_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_rcm_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    rcm_category,
    supplier_gstin,
    supplier_name,
    invoice_no,
    invoice_date,
    taxable_value,
    gst_rate,
    rcm_liability,
    cgst,
    sgst,
    igst,
    paid_period,
    is_paid,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.rcm_register;


ALTER VIEW t_globex_silver.v1_rcm_register OWNER TO tenant_migrate;

--
-- Name: v1_running_account_contract_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_running_account_contract_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    contract_ref,
    customer,
    customer_gstin,
    billing_frequency,
    statement_date,
    delivery_date,
    amount,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.running_account_contract_register;


ALTER VIEW t_globex_silver.v1_running_account_contract_register OWNER TO tenant_migrate;

--
-- Name: v1_sales_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_sales_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    document_hash,
    invoice_no,
    invoice_date,
    customer_gstin,
    invoice_type,
    supply_type,
    place_of_supply,
    reverse_charge,
    currency,
    trade_discount,
    freight,
    packing,
    insurance,
    taxable_value,
    cgst,
    sgst,
    igst,
    cess,
    total_value,
    total_tax,
    irn,
    ewb_no,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.sales_register;


ALTER VIEW t_globex_silver.v1_sales_register OWNER TO tenant_migrate;

--
-- Name: v1_sales_register_line; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_sales_register_line AS
 SELECT l.id,
    l.header_id,
    h.batch_id,
    h.entity_id,
    l.line_number,
    l.row_hash,
    l.hsn_sac,
    l.description,
    l.qty,
    l.uom,
    l.unit_price,
    l.taxable_value,
    l.gst_rate,
    l.cgst,
    l.sgst,
    l.igst,
    l.cess,
    l.total_value,
    l.total_tax,
    h.superseded_at
   FROM (t_globex_silver.sales_register_line l
     JOIN t_globex_silver.sales_register h ON ((h.id = l.header_id)));


ALTER VIEW t_globex_silver.v1_sales_register_line OWNER TO tenant_migrate;

--
-- Name: v1_stock_inventory_register; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_stock_inventory_register AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    sku,
    description,
    hsn,
    opening_qty,
    closing_qty,
    write_off_qty,
    write_off_value,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.stock_inventory_register;


ALTER VIEW t_globex_silver.v1_stock_inventory_register OWNER TO tenant_migrate;

--
-- Name: v1_transaction_document; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_transaction_document AS
 SELECT doc_id,
    batch_id,
    entity_id,
    doc_type,
    direction,
    doc_number,
    doc_date,
    counterparty_gstin,
    counterparty_name,
    total_taxable_value,
    total_tax_value,
    total_value,
    currency,
    attributes,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    bronze_ingest_id
   FROM t_globex_silver.transaction_document;


ALTER VIEW t_globex_silver.v1_transaction_document OWNER TO tenant_migrate;

--
-- Name: v1_transaction_line; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_transaction_line AS
 SELECT l.line_id,
    l.doc_id,
    d.batch_id,
    d.doc_type,
    d.direction,
    l.line_number,
    l.hsn_sac,
    l.description,
    l.quantity,
    l.unit_of_measure,
    l.unit_price,
    l.taxable_value,
    l.gst_rate,
    l.cgst_amount,
    l.sgst_amount,
    l.igst_amount,
    l.cess_amount,
    l.itc_amount,
    l.attributes
   FROM (t_globex_silver.transaction_line l
     JOIN t_globex_silver.transaction_document d ON ((d.doc_id = l.doc_id)));


ALTER VIEW t_globex_silver.v1_transaction_line OWNER TO tenant_migrate;

--
-- Name: v1_trial_balance; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_trial_balance AS
 SELECT id,
    entity_id,
    gstin,
    fy,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    gl_code,
    account_description,
    opening_balance,
    closing_balance,
    dr_cr,
    gst_treatment,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.trial_balance;


ALTER VIEW t_globex_silver.v1_trial_balance OWNER TO tenant_migrate;

--
-- Name: v1_unbilled_revenue_schedule; Type: VIEW; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE VIEW t_globex_silver.v1_unbilled_revenue_schedule AS
 SELECT id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    item_id,
    description,
    contract_ref,
    customer,
    customer_gstin,
    amount,
    date_of_service_completion,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
   FROM t_globex_silver.unbilled_revenue_schedule;


ALTER VIEW t_globex_silver.v1_unbilled_revenue_schedule OWNER TO tenant_migrate;

--
-- Name: shared_migration_version shared_migration_version_pkey; Type: CONSTRAINT; Schema: app; Owner: tenant_migrate
--

ALTER TABLE ONLY app.shared_migration_version
    ADD CONSTRAINT shared_migration_version_pkey PRIMARY KEY (filename);


--
-- Name: tenant_registry tenant_registry_pkey; Type: CONSTRAINT; Schema: app; Owner: tenant_migrate
--

ALTER TABLE ONLY app.tenant_registry
    ADD CONSTRAINT tenant_registry_pkey PRIMARY KEY (slug);


--
-- Name: tenant_registry tenant_registry_tenant_id_key; Type: CONSTRAINT; Schema: app; Owner: tenant_migrate
--

ALTER TABLE ONLY app.tenant_registry
    ADD CONSTRAINT tenant_registry_tenant_id_key UNIQUE (tenant_id);


--
-- Name: document_type document_type_pkey; Type: CONSTRAINT; Schema: platform_ref; Owner: tenant_migrate
--

ALTER TABLE ONLY platform_ref.document_type
    ADD CONSTRAINT document_type_pkey PRIMARY KEY (doc_type_code);


--
-- Name: document_type_ref document_type_ref_pkey; Type: CONSTRAINT; Schema: platform_ref; Owner: tenant_migrate
--

ALTER TABLE ONLY platform_ref.document_type_ref
    ADD CONSTRAINT document_type_ref_pkey PRIMARY KEY (register_ref, doc_type_code);


--
-- Name: universal_master universal_master_pkey; Type: CONSTRAINT; Schema: platform_ref; Owner: tenant_migrate
--

ALTER TABLE ONLY platform_ref.universal_master
    ADD CONSTRAINT universal_master_pkey PRIMARY KEY (value_type, value);


--
-- Name: __schema_identity __schema_identity_pkey; Type: CONSTRAINT; Schema: t_acme_bronze; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_bronze.__schema_identity
    ADD CONSTRAINT __schema_identity_pkey PRIMARY KEY (singleton);


--
-- Name: artefact_ledger artefact_ledger_pkey; Type: CONSTRAINT; Schema: t_acme_bronze; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_bronze.artefact_ledger
    ADD CONSTRAINT artefact_ledger_pkey PRIMARY KEY (ingest_id);


--
-- Name: __schema_identity __schema_identity_pkey; Type: CONSTRAINT; Schema: t_acme_gold; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_gold.__schema_identity
    ADD CONSTRAINT __schema_identity_pkey PRIMARY KEY (singleton);


--
-- Name: batch_execution batch_execution_pkey; Type: CONSTRAINT; Schema: t_acme_gold; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_gold.batch_execution
    ADD CONSTRAINT batch_execution_pkey PRIMARY KEY (batch_id, rule_catalog_version, corpus_version, tenant_pack_version);


--
-- Name: consumer_watermark consumer_watermark_pkey; Type: CONSTRAINT; Schema: t_acme_gold; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_gold.consumer_watermark
    ADD CONSTRAINT consumer_watermark_pkey PRIMARY KEY (consumer_name, document_type);


--
-- Name: fact_record fact_record_pkey; Type: CONSTRAINT; Schema: t_acme_gold; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_gold.fact_record
    ADD CONSTRAINT fact_record_pkey PRIMARY KEY (fact_id);


--
-- Name: __migration_version __migration_version_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.__migration_version
    ADD CONSTRAINT __migration_version_pkey PRIMARY KEY (filename);


--
-- Name: __schema_identity __schema_identity_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.__schema_identity
    ADD CONSTRAINT __schema_identity_pkey PRIMARY KEY (singleton);


--
-- Name: advance_receipt_register advance_receipt_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.advance_receipt_register
    ADD CONSTRAINT advance_receipt_register_pkey PRIMARY KEY (id);


--
-- Name: audited_pl_account audited_pl_account_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.audited_pl_account
    ADD CONSTRAINT audited_pl_account_pkey PRIMARY KEY (id);


--
-- Name: balance_sheet balance_sheet_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.balance_sheet
    ADD CONSTRAINT balance_sheet_pkey PRIMARY KEY (id);


--
-- Name: bank_statement_inward_forex bank_statement_inward_forex_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.bank_statement_inward_forex
    ADD CONSTRAINT bank_statement_inward_forex_pkey PRIMARY KEY (id);


--
-- Name: bank_statement_outward bank_statement_outward_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.bank_statement_outward
    ADD CONSTRAINT bank_statement_outward_pkey PRIMARY KEY (id);


--
-- Name: common_input_service_invoice common_input_service_invoice_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.common_input_service_invoice
    ADD CONSTRAINT common_input_service_invoice_pkey PRIMARY KEY (id);


--
-- Name: cost_allocation_register cost_allocation_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.cost_allocation_register
    ADD CONSTRAINT cost_allocation_register_pkey PRIMARY KEY (id);


--
-- Name: credit_debit_note_register credit_debit_note_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.credit_debit_note_register
    ADD CONSTRAINT credit_debit_note_register_pkey PRIMARY KEY (id);


--
-- Name: creditor_ageing_report creditor_ageing_report_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.creditor_ageing_report
    ADD CONSTRAINT creditor_ageing_report_pkey PRIMARY KEY (id);


--
-- Name: entitlement_instrument entitlement_instrument_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_pkey PRIMARY KEY (instrument_id);


--
-- Name: fixed_asset_register fixed_asset_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.fixed_asset_register
    ADD CONSTRAINT fixed_asset_register_pkey PRIMARY KEY (id);


--
-- Name: foreign_currency_payment_register foreign_currency_payment_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.foreign_currency_payment_register
    ADD CONSTRAINT foreign_currency_payment_register_pkey PRIMARY KEY (id);


--
-- Name: ingest_batch ingest_batch_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_pkey PRIMARY KEY (batch_id);


--
-- Name: inter_gstin_transaction_register inter_gstin_transaction_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.inter_gstin_transaction_register
    ADD CONSTRAINT inter_gstin_transaction_register_pkey PRIMARY KEY (id);


--
-- Name: itc_reversal_register itc_reversal_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.itc_reversal_register
    ADD CONSTRAINT itc_reversal_register_pkey PRIMARY KEY (id);


--
-- Name: job_work_dispatch_register job_work_dispatch_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.job_work_dispatch_register
    ADD CONSTRAINT job_work_dispatch_register_pkey PRIMARY KEY (id);


--
-- Name: payment_register payment_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.payment_register
    ADD CONSTRAINT payment_register_pkey PRIMARY KEY (id);


--
-- Name: platform_settlement_report platform_settlement_report_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.platform_settlement_report
    ADD CONSTRAINT platform_settlement_report_pkey PRIMARY KEY (id);


--
-- Name: product_sku_master product_sku_master_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.product_sku_master
    ADD CONSTRAINT product_sku_master_pkey PRIMARY KEY (id);


--
-- Name: purchase_register purchase_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.purchase_register
    ADD CONSTRAINT purchase_register_pkey PRIMARY KEY (id);


--
-- Name: quarantined_artefact quarantined_artefact_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.quarantined_artefact
    ADD CONSTRAINT quarantined_artefact_pkey PRIMARY KEY (ingest_id);


--
-- Name: rcm_register rcm_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.rcm_register
    ADD CONSTRAINT rcm_register_pkey PRIMARY KEY (id);


--
-- Name: running_account_contract_register running_account_contract_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.running_account_contract_register
    ADD CONSTRAINT running_account_contract_register_pkey PRIMARY KEY (id);


--
-- Name: sales_register_line sales_register_line_natural_key; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.sales_register_line
    ADD CONSTRAINT sales_register_line_natural_key UNIQUE (header_id, line_number);


--
-- Name: sales_register_line sales_register_line_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.sales_register_line
    ADD CONSTRAINT sales_register_line_pkey PRIMARY KEY (id);


--
-- Name: sales_register sales_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.sales_register
    ADD CONSTRAINT sales_register_pkey PRIMARY KEY (id);


--
-- Name: stock_inventory_register stock_inventory_register_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.stock_inventory_register
    ADD CONSTRAINT stock_inventory_register_pkey PRIMARY KEY (id);


--
-- Name: transaction_document transaction_document_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.transaction_document
    ADD CONSTRAINT transaction_document_pkey PRIMARY KEY (doc_id);


--
-- Name: transaction_line transaction_line_doc_id_line_number_key; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.transaction_line
    ADD CONSTRAINT transaction_line_doc_id_line_number_key UNIQUE (doc_id, line_number);


--
-- Name: transaction_line transaction_line_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.transaction_line
    ADD CONSTRAINT transaction_line_pkey PRIMARY KEY (line_id);


--
-- Name: trial_balance trial_balance_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.trial_balance
    ADD CONSTRAINT trial_balance_pkey PRIMARY KEY (id);


--
-- Name: unbilled_revenue_schedule unbilled_revenue_schedule_pkey; Type: CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.unbilled_revenue_schedule
    ADD CONSTRAINT unbilled_revenue_schedule_pkey PRIMARY KEY (id);


--
-- Name: __schema_identity __schema_identity_pkey; Type: CONSTRAINT; Schema: t_globex_bronze; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_bronze.__schema_identity
    ADD CONSTRAINT __schema_identity_pkey PRIMARY KEY (singleton);


--
-- Name: artefact_ledger artefact_ledger_pkey; Type: CONSTRAINT; Schema: t_globex_bronze; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_bronze.artefact_ledger
    ADD CONSTRAINT artefact_ledger_pkey PRIMARY KEY (ingest_id);


--
-- Name: __schema_identity __schema_identity_pkey; Type: CONSTRAINT; Schema: t_globex_gold; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_gold.__schema_identity
    ADD CONSTRAINT __schema_identity_pkey PRIMARY KEY (singleton);


--
-- Name: batch_execution batch_execution_pkey; Type: CONSTRAINT; Schema: t_globex_gold; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_gold.batch_execution
    ADD CONSTRAINT batch_execution_pkey PRIMARY KEY (batch_id, rule_catalog_version, corpus_version, tenant_pack_version);


--
-- Name: consumer_watermark consumer_watermark_pkey; Type: CONSTRAINT; Schema: t_globex_gold; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_gold.consumer_watermark
    ADD CONSTRAINT consumer_watermark_pkey PRIMARY KEY (consumer_name, document_type);


--
-- Name: fact_record fact_record_pkey; Type: CONSTRAINT; Schema: t_globex_gold; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_gold.fact_record
    ADD CONSTRAINT fact_record_pkey PRIMARY KEY (fact_id);


--
-- Name: __migration_version __migration_version_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.__migration_version
    ADD CONSTRAINT __migration_version_pkey PRIMARY KEY (filename);


--
-- Name: __schema_identity __schema_identity_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.__schema_identity
    ADD CONSTRAINT __schema_identity_pkey PRIMARY KEY (singleton);


--
-- Name: advance_receipt_register advance_receipt_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.advance_receipt_register
    ADD CONSTRAINT advance_receipt_register_pkey PRIMARY KEY (id);


--
-- Name: audited_pl_account audited_pl_account_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.audited_pl_account
    ADD CONSTRAINT audited_pl_account_pkey PRIMARY KEY (id);


--
-- Name: balance_sheet balance_sheet_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.balance_sheet
    ADD CONSTRAINT balance_sheet_pkey PRIMARY KEY (id);


--
-- Name: bank_statement_inward_forex bank_statement_inward_forex_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.bank_statement_inward_forex
    ADD CONSTRAINT bank_statement_inward_forex_pkey PRIMARY KEY (id);


--
-- Name: bank_statement_outward bank_statement_outward_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.bank_statement_outward
    ADD CONSTRAINT bank_statement_outward_pkey PRIMARY KEY (id);


--
-- Name: common_input_service_invoice common_input_service_invoice_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.common_input_service_invoice
    ADD CONSTRAINT common_input_service_invoice_pkey PRIMARY KEY (id);


--
-- Name: cost_allocation_register cost_allocation_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.cost_allocation_register
    ADD CONSTRAINT cost_allocation_register_pkey PRIMARY KEY (id);


--
-- Name: credit_debit_note_register credit_debit_note_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.credit_debit_note_register
    ADD CONSTRAINT credit_debit_note_register_pkey PRIMARY KEY (id);


--
-- Name: creditor_ageing_report creditor_ageing_report_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.creditor_ageing_report
    ADD CONSTRAINT creditor_ageing_report_pkey PRIMARY KEY (id);


--
-- Name: entitlement_instrument entitlement_instrument_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_pkey PRIMARY KEY (instrument_id);


--
-- Name: fixed_asset_register fixed_asset_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.fixed_asset_register
    ADD CONSTRAINT fixed_asset_register_pkey PRIMARY KEY (id);


--
-- Name: foreign_currency_payment_register foreign_currency_payment_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.foreign_currency_payment_register
    ADD CONSTRAINT foreign_currency_payment_register_pkey PRIMARY KEY (id);


--
-- Name: ingest_batch ingest_batch_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_pkey PRIMARY KEY (batch_id);


--
-- Name: inter_gstin_transaction_register inter_gstin_transaction_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.inter_gstin_transaction_register
    ADD CONSTRAINT inter_gstin_transaction_register_pkey PRIMARY KEY (id);


--
-- Name: itc_reversal_register itc_reversal_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.itc_reversal_register
    ADD CONSTRAINT itc_reversal_register_pkey PRIMARY KEY (id);


--
-- Name: job_work_dispatch_register job_work_dispatch_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.job_work_dispatch_register
    ADD CONSTRAINT job_work_dispatch_register_pkey PRIMARY KEY (id);


--
-- Name: payment_register payment_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.payment_register
    ADD CONSTRAINT payment_register_pkey PRIMARY KEY (id);


--
-- Name: platform_settlement_report platform_settlement_report_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.platform_settlement_report
    ADD CONSTRAINT platform_settlement_report_pkey PRIMARY KEY (id);


--
-- Name: product_sku_master product_sku_master_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.product_sku_master
    ADD CONSTRAINT product_sku_master_pkey PRIMARY KEY (id);


--
-- Name: purchase_register purchase_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.purchase_register
    ADD CONSTRAINT purchase_register_pkey PRIMARY KEY (id);


--
-- Name: quarantined_artefact quarantined_artefact_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.quarantined_artefact
    ADD CONSTRAINT quarantined_artefact_pkey PRIMARY KEY (ingest_id);


--
-- Name: rcm_register rcm_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.rcm_register
    ADD CONSTRAINT rcm_register_pkey PRIMARY KEY (id);


--
-- Name: running_account_contract_register running_account_contract_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.running_account_contract_register
    ADD CONSTRAINT running_account_contract_register_pkey PRIMARY KEY (id);


--
-- Name: sales_register_line sales_register_line_natural_key; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.sales_register_line
    ADD CONSTRAINT sales_register_line_natural_key UNIQUE (header_id, line_number);


--
-- Name: sales_register_line sales_register_line_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.sales_register_line
    ADD CONSTRAINT sales_register_line_pkey PRIMARY KEY (id);


--
-- Name: sales_register sales_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.sales_register
    ADD CONSTRAINT sales_register_pkey PRIMARY KEY (id);


--
-- Name: stock_inventory_register stock_inventory_register_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.stock_inventory_register
    ADD CONSTRAINT stock_inventory_register_pkey PRIMARY KEY (id);


--
-- Name: transaction_document transaction_document_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.transaction_document
    ADD CONSTRAINT transaction_document_pkey PRIMARY KEY (doc_id);


--
-- Name: transaction_line transaction_line_doc_id_line_number_key; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.transaction_line
    ADD CONSTRAINT transaction_line_doc_id_line_number_key UNIQUE (doc_id, line_number);


--
-- Name: transaction_line transaction_line_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.transaction_line
    ADD CONSTRAINT transaction_line_pkey PRIMARY KEY (line_id);


--
-- Name: trial_balance trial_balance_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.trial_balance
    ADD CONSTRAINT trial_balance_pkey PRIMARY KEY (id);


--
-- Name: unbilled_revenue_schedule unbilled_revenue_schedule_pkey; Type: CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.unbilled_revenue_schedule
    ADD CONSTRAINT unbilled_revenue_schedule_pkey PRIMARY KEY (id);


--
-- Name: document_type_code_archetype_uq; Type: INDEX; Schema: platform_ref; Owner: tenant_migrate
--

CREATE UNIQUE INDEX document_type_code_archetype_uq ON platform_ref.document_type USING btree (doc_type_code, archetype);


--
-- Name: document_type_ref_canonical_uq; Type: INDEX; Schema: platform_ref; Owner: tenant_migrate
--

CREATE UNIQUE INDEX document_type_ref_canonical_uq ON platform_ref.document_type_ref USING btree (doc_type_code) WHERE is_canonical;


--
-- Name: document_type_ref_code_idx; Type: INDEX; Schema: platform_ref; Owner: tenant_migrate
--

CREATE INDEX document_type_ref_code_idx ON platform_ref.document_type_ref USING btree (doc_type_code);


--
-- Name: document_type_table_name_idx; Type: INDEX; Schema: platform_ref; Owner: tenant_migrate
--

CREATE INDEX document_type_table_name_idx ON platform_ref.document_type USING btree (table_name) WHERE (table_name IS NOT NULL);


--
-- Name: artefact_ledger_content_hash_uq; Type: INDEX; Schema: t_acme_bronze; Owner: tenant_migrate
--

CREATE UNIQUE INDEX artefact_ledger_content_hash_uq ON t_acme_bronze.artefact_ledger USING btree (content_hash);


--
-- Name: batch_execution_executed_idx; Type: INDEX; Schema: t_acme_gold; Owner: tenant_migrate
--

CREATE INDEX batch_execution_executed_idx ON t_acme_gold.batch_execution USING btree (executed_at);


--
-- Name: fact_record_batch_idx; Type: INDEX; Schema: t_acme_gold; Owner: tenant_migrate
--

CREATE INDEX fact_record_batch_idx ON t_acme_gold.fact_record USING btree (batch_id);


--
-- Name: fact_record_invoice_idx; Type: INDEX; Schema: t_acme_gold; Owner: tenant_migrate
--

CREATE INDEX fact_record_invoice_idx ON t_acme_gold.fact_record USING btree (invoice_id);


--
-- Name: advance_receipt_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX advance_receipt_register_batch_idx ON t_acme_silver.advance_receipt_register USING btree (batch_id);


--
-- Name: advance_receipt_register_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX advance_receipt_register_content_key_uq ON t_acme_silver.advance_receipt_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: advance_receipt_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX advance_receipt_register_gstin_period_idx ON t_acme_silver.advance_receipt_register USING btree (gstin, tax_period);


--
-- Name: audited_pl_account_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX audited_pl_account_batch_idx ON t_acme_silver.audited_pl_account USING btree (batch_id);


--
-- Name: audited_pl_account_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX audited_pl_account_content_key_uq ON t_acme_silver.audited_pl_account USING btree (entity_id, gstin, fy, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: audited_pl_account_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX audited_pl_account_gstin_period_idx ON t_acme_silver.audited_pl_account USING btree (gstin, fy);


--
-- Name: balance_sheet_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX balance_sheet_batch_idx ON t_acme_silver.balance_sheet USING btree (batch_id);


--
-- Name: balance_sheet_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX balance_sheet_gstin_period_idx ON t_acme_silver.balance_sheet USING btree (gstin, fy);


--
-- Name: balance_sheet_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX balance_sheet_natural_key_uq ON t_acme_silver.balance_sheet USING btree (entity_id, gstin, line_item) WHERE (superseded_at IS NULL);


--
-- Name: bank_statement_inward_forex_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX bank_statement_inward_forex_batch_idx ON t_acme_silver.bank_statement_inward_forex USING btree (batch_id);


--
-- Name: bank_statement_inward_forex_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX bank_statement_inward_forex_content_key_uq ON t_acme_silver.bank_statement_inward_forex USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: bank_statement_inward_forex_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX bank_statement_inward_forex_gstin_period_idx ON t_acme_silver.bank_statement_inward_forex USING btree (gstin, tax_period);


--
-- Name: bank_statement_outward_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX bank_statement_outward_batch_idx ON t_acme_silver.bank_statement_outward USING btree (batch_id);


--
-- Name: bank_statement_outward_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX bank_statement_outward_content_key_uq ON t_acme_silver.bank_statement_outward USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: bank_statement_outward_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX bank_statement_outward_gstin_period_idx ON t_acme_silver.bank_statement_outward USING btree (gstin, tax_period);


--
-- Name: common_input_service_invoice_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX common_input_service_invoice_batch_idx ON t_acme_silver.common_input_service_invoice USING btree (batch_id);


--
-- Name: common_input_service_invoice_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX common_input_service_invoice_gstin_period_idx ON t_acme_silver.common_input_service_invoice USING btree (gstin, tax_period);


--
-- Name: common_input_service_invoice_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX common_input_service_invoice_natural_key_uq ON t_acme_silver.common_input_service_invoice USING btree (entity_id, gstin, supplier_gstin, invoice_no) WHERE (superseded_at IS NULL);


--
-- Name: cost_allocation_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX cost_allocation_register_batch_idx ON t_acme_silver.cost_allocation_register USING btree (batch_id);


--
-- Name: cost_allocation_register_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX cost_allocation_register_content_key_uq ON t_acme_silver.cost_allocation_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: cost_allocation_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX cost_allocation_register_gstin_period_idx ON t_acme_silver.cost_allocation_register USING btree (gstin, tax_period);


--
-- Name: credit_debit_note_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX credit_debit_note_register_batch_idx ON t_acme_silver.credit_debit_note_register USING btree (batch_id);


--
-- Name: credit_debit_note_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX credit_debit_note_register_gstin_period_idx ON t_acme_silver.credit_debit_note_register USING btree (gstin, tax_period);


--
-- Name: credit_debit_note_register_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX credit_debit_note_register_natural_key_uq ON t_acme_silver.credit_debit_note_register USING btree (entity_id, gstin, note_no) WHERE (superseded_at IS NULL);


--
-- Name: creditor_ageing_report_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX creditor_ageing_report_batch_idx ON t_acme_silver.creditor_ageing_report USING btree (batch_id);


--
-- Name: creditor_ageing_report_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX creditor_ageing_report_gstin_period_idx ON t_acme_silver.creditor_ageing_report USING btree (gstin, tax_period);


--
-- Name: creditor_ageing_report_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX creditor_ageing_report_natural_key_uq ON t_acme_silver.creditor_ageing_report USING btree (entity_id, gstin, as_at_date, supplier_gstin, invoice_no) WHERE (superseded_at IS NULL);


--
-- Name: entitlement_instrument_current_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX entitlement_instrument_current_uq ON t_acme_silver.entitlement_instrument USING btree (entity_id, instrument_type, instrument_number) WHERE (superseded_at IS NULL);


--
-- Name: entitlement_instrument_hsn_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX entitlement_instrument_hsn_idx ON t_acme_silver.entitlement_instrument USING gin (scope_hsn) WHERE (superseded_at IS NULL);


--
-- Name: entitlement_instrument_lookup_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX entitlement_instrument_lookup_idx ON t_acme_silver.entitlement_instrument USING btree (entity_id, instrument_type, valid_from, valid_to) WHERE (superseded_at IS NULL);


--
-- Name: fixed_asset_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX fixed_asset_register_batch_idx ON t_acme_silver.fixed_asset_register USING btree (batch_id);


--
-- Name: fixed_asset_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX fixed_asset_register_gstin_period_idx ON t_acme_silver.fixed_asset_register USING btree (gstin, tax_period);


--
-- Name: fixed_asset_register_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX fixed_asset_register_natural_key_uq ON t_acme_silver.fixed_asset_register USING btree (entity_id, gstin, asset_id) WHERE (superseded_at IS NULL);


--
-- Name: foreign_currency_payment_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX foreign_currency_payment_register_batch_idx ON t_acme_silver.foreign_currency_payment_register USING btree (batch_id);


--
-- Name: foreign_currency_payment_register_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX foreign_currency_payment_register_content_key_uq ON t_acme_silver.foreign_currency_payment_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: foreign_currency_payment_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX foreign_currency_payment_register_gstin_period_idx ON t_acme_silver.foreign_currency_payment_register USING btree (gstin, tax_period);


--
-- Name: ingest_batch_ready_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX ingest_batch_ready_idx ON t_acme_silver.ingest_batch USING btree (ready_at) WHERE (status = 'READY'::text);


--
-- Name: ingest_batch_scope_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX ingest_batch_scope_idx ON t_acme_silver.ingest_batch USING btree (entity_id, document_type, period_start, period_end);


--
-- Name: inter_gstin_transaction_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX inter_gstin_transaction_register_batch_idx ON t_acme_silver.inter_gstin_transaction_register USING btree (batch_id);


--
-- Name: inter_gstin_transaction_register_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX inter_gstin_transaction_register_content_key_uq ON t_acme_silver.inter_gstin_transaction_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: inter_gstin_transaction_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX inter_gstin_transaction_register_gstin_period_idx ON t_acme_silver.inter_gstin_transaction_register USING btree (gstin, tax_period);


--
-- Name: itc_reversal_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX itc_reversal_register_batch_idx ON t_acme_silver.itc_reversal_register USING btree (batch_id);


--
-- Name: itc_reversal_register_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX itc_reversal_register_content_key_uq ON t_acme_silver.itc_reversal_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: itc_reversal_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX itc_reversal_register_gstin_period_idx ON t_acme_silver.itc_reversal_register USING btree (gstin, tax_period);


--
-- Name: job_work_dispatch_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX job_work_dispatch_register_batch_idx ON t_acme_silver.job_work_dispatch_register USING btree (batch_id);


--
-- Name: job_work_dispatch_register_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX job_work_dispatch_register_content_key_uq ON t_acme_silver.job_work_dispatch_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: job_work_dispatch_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX job_work_dispatch_register_gstin_period_idx ON t_acme_silver.job_work_dispatch_register USING btree (gstin, tax_period);


--
-- Name: payment_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX payment_register_batch_idx ON t_acme_silver.payment_register USING btree (batch_id);


--
-- Name: payment_register_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX payment_register_content_key_uq ON t_acme_silver.payment_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: payment_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX payment_register_gstin_period_idx ON t_acme_silver.payment_register USING btree (gstin, tax_period);


--
-- Name: platform_settlement_report_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX platform_settlement_report_batch_idx ON t_acme_silver.platform_settlement_report USING btree (batch_id);


--
-- Name: platform_settlement_report_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX platform_settlement_report_content_key_uq ON t_acme_silver.platform_settlement_report USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: platform_settlement_report_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX platform_settlement_report_gstin_period_idx ON t_acme_silver.platform_settlement_report USING btree (gstin, tax_period);


--
-- Name: product_sku_master_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX product_sku_master_batch_idx ON t_acme_silver.product_sku_master USING btree (batch_id);


--
-- Name: product_sku_master_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX product_sku_master_gstin_period_idx ON t_acme_silver.product_sku_master USING btree (gstin, tax_period);


--
-- Name: product_sku_master_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX product_sku_master_natural_key_uq ON t_acme_silver.product_sku_master USING btree (entity_id, gstin, sku) WHERE (superseded_at IS NULL);


--
-- Name: purchase_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX purchase_register_batch_idx ON t_acme_silver.purchase_register USING btree (batch_id);


--
-- Name: purchase_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX purchase_register_gstin_period_idx ON t_acme_silver.purchase_register USING btree (gstin, tax_period);


--
-- Name: purchase_register_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX purchase_register_natural_key_uq ON t_acme_silver.purchase_register USING btree (entity_id, gstin, supplier_gstin, invoice_no) WHERE (superseded_at IS NULL);


--
-- Name: quarantined_artefact_rejected_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX quarantined_artefact_rejected_idx ON t_acme_silver.quarantined_artefact USING btree (rejected_at);


--
-- Name: rcm_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX rcm_register_batch_idx ON t_acme_silver.rcm_register USING btree (batch_id);


--
-- Name: rcm_register_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX rcm_register_content_key_uq ON t_acme_silver.rcm_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: rcm_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX rcm_register_gstin_period_idx ON t_acme_silver.rcm_register USING btree (gstin, tax_period);


--
-- Name: running_account_contract_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX running_account_contract_register_batch_idx ON t_acme_silver.running_account_contract_register USING btree (batch_id);


--
-- Name: running_account_contract_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX running_account_contract_register_gstin_period_idx ON t_acme_silver.running_account_contract_register USING btree (gstin, tax_period);


--
-- Name: running_account_contract_register_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX running_account_contract_register_natural_key_uq ON t_acme_silver.running_account_contract_register USING btree (entity_id, gstin, contract_ref) WHERE (superseded_at IS NULL);


--
-- Name: sales_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX sales_register_batch_idx ON t_acme_silver.sales_register USING btree (batch_id);


--
-- Name: sales_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX sales_register_gstin_period_idx ON t_acme_silver.sales_register USING btree (gstin, tax_period);


--
-- Name: sales_register_line_header_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX sales_register_line_header_idx ON t_acme_silver.sales_register_line USING btree (header_id);


--
-- Name: sales_register_line_hsn_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX sales_register_line_hsn_idx ON t_acme_silver.sales_register_line USING btree (hsn_sac);


--
-- Name: sales_register_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX sales_register_natural_key_uq ON t_acme_silver.sales_register USING btree (entity_id, gstin, invoice_no) WHERE (superseded_at IS NULL);


--
-- Name: stock_inventory_register_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX stock_inventory_register_batch_idx ON t_acme_silver.stock_inventory_register USING btree (batch_id);


--
-- Name: stock_inventory_register_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX stock_inventory_register_gstin_period_idx ON t_acme_silver.stock_inventory_register USING btree (gstin, tax_period);


--
-- Name: stock_inventory_register_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX stock_inventory_register_natural_key_uq ON t_acme_silver.stock_inventory_register USING btree (entity_id, gstin, tax_period, sku) WHERE (superseded_at IS NULL);


--
-- Name: transaction_document_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX transaction_document_batch_idx ON t_acme_silver.transaction_document USING btree (batch_id);


--
-- Name: transaction_document_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX transaction_document_natural_key_uq ON t_acme_silver.transaction_document USING btree (entity_id, doc_type, COALESCE(counterparty_gstin, ''::text), doc_number) WHERE (superseded_at IS NULL);


--
-- Name: transaction_document_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX transaction_document_period_idx ON t_acme_silver.transaction_document USING btree (entity_id, direction, doc_date) WHERE (superseded_at IS NULL);


--
-- Name: transaction_line_doc_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX transaction_line_doc_idx ON t_acme_silver.transaction_line USING btree (doc_id);


--
-- Name: transaction_line_hsn_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX transaction_line_hsn_idx ON t_acme_silver.transaction_line USING btree (hsn_sac);


--
-- Name: trial_balance_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX trial_balance_batch_idx ON t_acme_silver.trial_balance USING btree (batch_id);


--
-- Name: trial_balance_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX trial_balance_gstin_period_idx ON t_acme_silver.trial_balance USING btree (gstin, fy);


--
-- Name: trial_balance_natural_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX trial_balance_natural_key_uq ON t_acme_silver.trial_balance USING btree (entity_id, gstin, gl_code) WHERE (superseded_at IS NULL);


--
-- Name: unbilled_revenue_schedule_batch_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX unbilled_revenue_schedule_batch_idx ON t_acme_silver.unbilled_revenue_schedule USING btree (batch_id);


--
-- Name: unbilled_revenue_schedule_content_key_uq; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX unbilled_revenue_schedule_content_key_uq ON t_acme_silver.unbilled_revenue_schedule USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: unbilled_revenue_schedule_gstin_period_idx; Type: INDEX; Schema: t_acme_silver; Owner: tenant_migrate
--

CREATE INDEX unbilled_revenue_schedule_gstin_period_idx ON t_acme_silver.unbilled_revenue_schedule USING btree (gstin, tax_period);


--
-- Name: artefact_ledger_content_hash_uq; Type: INDEX; Schema: t_globex_bronze; Owner: tenant_migrate
--

CREATE UNIQUE INDEX artefact_ledger_content_hash_uq ON t_globex_bronze.artefact_ledger USING btree (content_hash);


--
-- Name: batch_execution_executed_idx; Type: INDEX; Schema: t_globex_gold; Owner: tenant_migrate
--

CREATE INDEX batch_execution_executed_idx ON t_globex_gold.batch_execution USING btree (executed_at);


--
-- Name: fact_record_batch_idx; Type: INDEX; Schema: t_globex_gold; Owner: tenant_migrate
--

CREATE INDEX fact_record_batch_idx ON t_globex_gold.fact_record USING btree (batch_id);


--
-- Name: fact_record_invoice_idx; Type: INDEX; Schema: t_globex_gold; Owner: tenant_migrate
--

CREATE INDEX fact_record_invoice_idx ON t_globex_gold.fact_record USING btree (invoice_id);


--
-- Name: advance_receipt_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX advance_receipt_register_batch_idx ON t_globex_silver.advance_receipt_register USING btree (batch_id);


--
-- Name: advance_receipt_register_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX advance_receipt_register_content_key_uq ON t_globex_silver.advance_receipt_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: advance_receipt_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX advance_receipt_register_gstin_period_idx ON t_globex_silver.advance_receipt_register USING btree (gstin, tax_period);


--
-- Name: audited_pl_account_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX audited_pl_account_batch_idx ON t_globex_silver.audited_pl_account USING btree (batch_id);


--
-- Name: audited_pl_account_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX audited_pl_account_content_key_uq ON t_globex_silver.audited_pl_account USING btree (entity_id, gstin, fy, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: audited_pl_account_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX audited_pl_account_gstin_period_idx ON t_globex_silver.audited_pl_account USING btree (gstin, fy);


--
-- Name: balance_sheet_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX balance_sheet_batch_idx ON t_globex_silver.balance_sheet USING btree (batch_id);


--
-- Name: balance_sheet_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX balance_sheet_gstin_period_idx ON t_globex_silver.balance_sheet USING btree (gstin, fy);


--
-- Name: balance_sheet_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX balance_sheet_natural_key_uq ON t_globex_silver.balance_sheet USING btree (entity_id, gstin, line_item) WHERE (superseded_at IS NULL);


--
-- Name: bank_statement_inward_forex_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX bank_statement_inward_forex_batch_idx ON t_globex_silver.bank_statement_inward_forex USING btree (batch_id);


--
-- Name: bank_statement_inward_forex_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX bank_statement_inward_forex_content_key_uq ON t_globex_silver.bank_statement_inward_forex USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: bank_statement_inward_forex_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX bank_statement_inward_forex_gstin_period_idx ON t_globex_silver.bank_statement_inward_forex USING btree (gstin, tax_period);


--
-- Name: bank_statement_outward_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX bank_statement_outward_batch_idx ON t_globex_silver.bank_statement_outward USING btree (batch_id);


--
-- Name: bank_statement_outward_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX bank_statement_outward_content_key_uq ON t_globex_silver.bank_statement_outward USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: bank_statement_outward_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX bank_statement_outward_gstin_period_idx ON t_globex_silver.bank_statement_outward USING btree (gstin, tax_period);


--
-- Name: common_input_service_invoice_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX common_input_service_invoice_batch_idx ON t_globex_silver.common_input_service_invoice USING btree (batch_id);


--
-- Name: common_input_service_invoice_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX common_input_service_invoice_gstin_period_idx ON t_globex_silver.common_input_service_invoice USING btree (gstin, tax_period);


--
-- Name: common_input_service_invoice_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX common_input_service_invoice_natural_key_uq ON t_globex_silver.common_input_service_invoice USING btree (entity_id, gstin, supplier_gstin, invoice_no) WHERE (superseded_at IS NULL);


--
-- Name: cost_allocation_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX cost_allocation_register_batch_idx ON t_globex_silver.cost_allocation_register USING btree (batch_id);


--
-- Name: cost_allocation_register_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX cost_allocation_register_content_key_uq ON t_globex_silver.cost_allocation_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: cost_allocation_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX cost_allocation_register_gstin_period_idx ON t_globex_silver.cost_allocation_register USING btree (gstin, tax_period);


--
-- Name: credit_debit_note_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX credit_debit_note_register_batch_idx ON t_globex_silver.credit_debit_note_register USING btree (batch_id);


--
-- Name: credit_debit_note_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX credit_debit_note_register_gstin_period_idx ON t_globex_silver.credit_debit_note_register USING btree (gstin, tax_period);


--
-- Name: credit_debit_note_register_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX credit_debit_note_register_natural_key_uq ON t_globex_silver.credit_debit_note_register USING btree (entity_id, gstin, note_no) WHERE (superseded_at IS NULL);


--
-- Name: creditor_ageing_report_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX creditor_ageing_report_batch_idx ON t_globex_silver.creditor_ageing_report USING btree (batch_id);


--
-- Name: creditor_ageing_report_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX creditor_ageing_report_gstin_period_idx ON t_globex_silver.creditor_ageing_report USING btree (gstin, tax_period);


--
-- Name: creditor_ageing_report_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX creditor_ageing_report_natural_key_uq ON t_globex_silver.creditor_ageing_report USING btree (entity_id, gstin, as_at_date, supplier_gstin, invoice_no) WHERE (superseded_at IS NULL);


--
-- Name: entitlement_instrument_current_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX entitlement_instrument_current_uq ON t_globex_silver.entitlement_instrument USING btree (entity_id, instrument_type, instrument_number) WHERE (superseded_at IS NULL);


--
-- Name: entitlement_instrument_hsn_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX entitlement_instrument_hsn_idx ON t_globex_silver.entitlement_instrument USING gin (scope_hsn) WHERE (superseded_at IS NULL);


--
-- Name: entitlement_instrument_lookup_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX entitlement_instrument_lookup_idx ON t_globex_silver.entitlement_instrument USING btree (entity_id, instrument_type, valid_from, valid_to) WHERE (superseded_at IS NULL);


--
-- Name: fixed_asset_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX fixed_asset_register_batch_idx ON t_globex_silver.fixed_asset_register USING btree (batch_id);


--
-- Name: fixed_asset_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX fixed_asset_register_gstin_period_idx ON t_globex_silver.fixed_asset_register USING btree (gstin, tax_period);


--
-- Name: fixed_asset_register_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX fixed_asset_register_natural_key_uq ON t_globex_silver.fixed_asset_register USING btree (entity_id, gstin, asset_id) WHERE (superseded_at IS NULL);


--
-- Name: foreign_currency_payment_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX foreign_currency_payment_register_batch_idx ON t_globex_silver.foreign_currency_payment_register USING btree (batch_id);


--
-- Name: foreign_currency_payment_register_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX foreign_currency_payment_register_content_key_uq ON t_globex_silver.foreign_currency_payment_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: foreign_currency_payment_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX foreign_currency_payment_register_gstin_period_idx ON t_globex_silver.foreign_currency_payment_register USING btree (gstin, tax_period);


--
-- Name: ingest_batch_ready_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX ingest_batch_ready_idx ON t_globex_silver.ingest_batch USING btree (ready_at) WHERE (status = 'READY'::text);


--
-- Name: ingest_batch_scope_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX ingest_batch_scope_idx ON t_globex_silver.ingest_batch USING btree (entity_id, document_type, period_start, period_end);


--
-- Name: inter_gstin_transaction_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX inter_gstin_transaction_register_batch_idx ON t_globex_silver.inter_gstin_transaction_register USING btree (batch_id);


--
-- Name: inter_gstin_transaction_register_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX inter_gstin_transaction_register_content_key_uq ON t_globex_silver.inter_gstin_transaction_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: inter_gstin_transaction_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX inter_gstin_transaction_register_gstin_period_idx ON t_globex_silver.inter_gstin_transaction_register USING btree (gstin, tax_period);


--
-- Name: itc_reversal_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX itc_reversal_register_batch_idx ON t_globex_silver.itc_reversal_register USING btree (batch_id);


--
-- Name: itc_reversal_register_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX itc_reversal_register_content_key_uq ON t_globex_silver.itc_reversal_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: itc_reversal_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX itc_reversal_register_gstin_period_idx ON t_globex_silver.itc_reversal_register USING btree (gstin, tax_period);


--
-- Name: job_work_dispatch_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX job_work_dispatch_register_batch_idx ON t_globex_silver.job_work_dispatch_register USING btree (batch_id);


--
-- Name: job_work_dispatch_register_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX job_work_dispatch_register_content_key_uq ON t_globex_silver.job_work_dispatch_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: job_work_dispatch_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX job_work_dispatch_register_gstin_period_idx ON t_globex_silver.job_work_dispatch_register USING btree (gstin, tax_period);


--
-- Name: payment_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX payment_register_batch_idx ON t_globex_silver.payment_register USING btree (batch_id);


--
-- Name: payment_register_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX payment_register_content_key_uq ON t_globex_silver.payment_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: payment_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX payment_register_gstin_period_idx ON t_globex_silver.payment_register USING btree (gstin, tax_period);


--
-- Name: platform_settlement_report_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX platform_settlement_report_batch_idx ON t_globex_silver.platform_settlement_report USING btree (batch_id);


--
-- Name: platform_settlement_report_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX platform_settlement_report_content_key_uq ON t_globex_silver.platform_settlement_report USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: platform_settlement_report_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX platform_settlement_report_gstin_period_idx ON t_globex_silver.platform_settlement_report USING btree (gstin, tax_period);


--
-- Name: product_sku_master_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX product_sku_master_batch_idx ON t_globex_silver.product_sku_master USING btree (batch_id);


--
-- Name: product_sku_master_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX product_sku_master_gstin_period_idx ON t_globex_silver.product_sku_master USING btree (gstin, tax_period);


--
-- Name: product_sku_master_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX product_sku_master_natural_key_uq ON t_globex_silver.product_sku_master USING btree (entity_id, gstin, sku) WHERE (superseded_at IS NULL);


--
-- Name: purchase_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX purchase_register_batch_idx ON t_globex_silver.purchase_register USING btree (batch_id);


--
-- Name: purchase_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX purchase_register_gstin_period_idx ON t_globex_silver.purchase_register USING btree (gstin, tax_period);


--
-- Name: purchase_register_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX purchase_register_natural_key_uq ON t_globex_silver.purchase_register USING btree (entity_id, gstin, supplier_gstin, invoice_no) WHERE (superseded_at IS NULL);


--
-- Name: quarantined_artefact_rejected_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX quarantined_artefact_rejected_idx ON t_globex_silver.quarantined_artefact USING btree (rejected_at);


--
-- Name: rcm_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX rcm_register_batch_idx ON t_globex_silver.rcm_register USING btree (batch_id);


--
-- Name: rcm_register_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX rcm_register_content_key_uq ON t_globex_silver.rcm_register USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: rcm_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX rcm_register_gstin_period_idx ON t_globex_silver.rcm_register USING btree (gstin, tax_period);


--
-- Name: running_account_contract_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX running_account_contract_register_batch_idx ON t_globex_silver.running_account_contract_register USING btree (batch_id);


--
-- Name: running_account_contract_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX running_account_contract_register_gstin_period_idx ON t_globex_silver.running_account_contract_register USING btree (gstin, tax_period);


--
-- Name: running_account_contract_register_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX running_account_contract_register_natural_key_uq ON t_globex_silver.running_account_contract_register USING btree (entity_id, gstin, contract_ref) WHERE (superseded_at IS NULL);


--
-- Name: sales_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX sales_register_batch_idx ON t_globex_silver.sales_register USING btree (batch_id);


--
-- Name: sales_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX sales_register_gstin_period_idx ON t_globex_silver.sales_register USING btree (gstin, tax_period);


--
-- Name: sales_register_line_header_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX sales_register_line_header_idx ON t_globex_silver.sales_register_line USING btree (header_id);


--
-- Name: sales_register_line_hsn_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX sales_register_line_hsn_idx ON t_globex_silver.sales_register_line USING btree (hsn_sac);


--
-- Name: sales_register_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX sales_register_natural_key_uq ON t_globex_silver.sales_register USING btree (entity_id, gstin, invoice_no) WHERE (superseded_at IS NULL);


--
-- Name: stock_inventory_register_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX stock_inventory_register_batch_idx ON t_globex_silver.stock_inventory_register USING btree (batch_id);


--
-- Name: stock_inventory_register_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX stock_inventory_register_gstin_period_idx ON t_globex_silver.stock_inventory_register USING btree (gstin, tax_period);


--
-- Name: stock_inventory_register_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX stock_inventory_register_natural_key_uq ON t_globex_silver.stock_inventory_register USING btree (entity_id, gstin, tax_period, sku) WHERE (superseded_at IS NULL);


--
-- Name: transaction_document_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX transaction_document_batch_idx ON t_globex_silver.transaction_document USING btree (batch_id);


--
-- Name: transaction_document_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX transaction_document_natural_key_uq ON t_globex_silver.transaction_document USING btree (entity_id, doc_type, COALESCE(counterparty_gstin, ''::text), doc_number) WHERE (superseded_at IS NULL);


--
-- Name: transaction_document_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX transaction_document_period_idx ON t_globex_silver.transaction_document USING btree (entity_id, direction, doc_date) WHERE (superseded_at IS NULL);


--
-- Name: transaction_line_doc_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX transaction_line_doc_idx ON t_globex_silver.transaction_line USING btree (doc_id);


--
-- Name: transaction_line_hsn_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX transaction_line_hsn_idx ON t_globex_silver.transaction_line USING btree (hsn_sac);


--
-- Name: trial_balance_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX trial_balance_batch_idx ON t_globex_silver.trial_balance USING btree (batch_id);


--
-- Name: trial_balance_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX trial_balance_gstin_period_idx ON t_globex_silver.trial_balance USING btree (gstin, fy);


--
-- Name: trial_balance_natural_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX trial_balance_natural_key_uq ON t_globex_silver.trial_balance USING btree (entity_id, gstin, gl_code) WHERE (superseded_at IS NULL);


--
-- Name: unbilled_revenue_schedule_batch_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX unbilled_revenue_schedule_batch_idx ON t_globex_silver.unbilled_revenue_schedule USING btree (batch_id);


--
-- Name: unbilled_revenue_schedule_content_key_uq; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE UNIQUE INDEX unbilled_revenue_schedule_content_key_uq ON t_globex_silver.unbilled_revenue_schedule USING btree (entity_id, gstin, tax_period, row_hash) WHERE (superseded_at IS NULL);


--
-- Name: unbilled_revenue_schedule_gstin_period_idx; Type: INDEX; Schema: t_globex_silver; Owner: tenant_migrate
--

CREATE INDEX unbilled_revenue_schedule_gstin_period_idx ON t_globex_silver.unbilled_revenue_schedule USING btree (gstin, tax_period);


--
-- Name: document_type document_type_doc_type_code_vt_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: platform_ref; Owner: tenant_migrate
--

ALTER TABLE ONLY platform_ref.document_type
    ADD CONSTRAINT document_type_doc_type_code_vt_doc_type_code_fkey FOREIGN KEY (doc_type_code_vt, doc_type_code) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: document_type_ref document_type_ref_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: platform_ref; Owner: tenant_migrate
--

ALTER TABLE ONLY platform_ref.document_type_ref
    ADD CONSTRAINT document_type_ref_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: document_type document_type_stream_vt_stream_fkey; Type: FK CONSTRAINT; Schema: platform_ref; Owner: tenant_migrate
--

ALTER TABLE ONLY platform_ref.document_type
    ADD CONSTRAINT document_type_stream_vt_stream_fkey FOREIGN KEY (stream_vt, stream) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: artefact_ledger artefact_ledger_declared_document_type_vt_declared_documen_fkey; Type: FK CONSTRAINT; Schema: t_acme_bronze; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_bronze.artefact_ledger
    ADD CONSTRAINT artefact_ledger_declared_document_type_vt_declared_documen_fkey FOREIGN KEY (declared_document_type_vt, declared_document_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: artefact_ledger artefact_ledger_source_stream_vt_source_stream_fkey; Type: FK CONSTRAINT; Schema: t_acme_bronze; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_bronze.artefact_ledger
    ADD CONSTRAINT artefact_ledger_source_stream_vt_source_stream_fkey FOREIGN KEY (source_stream_vt, source_stream) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: advance_receipt_register advance_receipt_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.advance_receipt_register
    ADD CONSTRAINT advance_receipt_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: advance_receipt_register advance_receipt_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.advance_receipt_register
    ADD CONSTRAINT advance_receipt_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: audited_pl_account audited_pl_account_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.audited_pl_account
    ADD CONSTRAINT audited_pl_account_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: audited_pl_account audited_pl_account_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.audited_pl_account
    ADD CONSTRAINT audited_pl_account_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: balance_sheet balance_sheet_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.balance_sheet
    ADD CONSTRAINT balance_sheet_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: balance_sheet balance_sheet_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.balance_sheet
    ADD CONSTRAINT balance_sheet_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: bank_statement_inward_forex bank_statement_inward_forex_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.bank_statement_inward_forex
    ADD CONSTRAINT bank_statement_inward_forex_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: bank_statement_inward_forex bank_statement_inward_forex_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.bank_statement_inward_forex
    ADD CONSTRAINT bank_statement_inward_forex_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: bank_statement_outward bank_statement_outward_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.bank_statement_outward
    ADD CONSTRAINT bank_statement_outward_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: bank_statement_outward bank_statement_outward_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.bank_statement_outward
    ADD CONSTRAINT bank_statement_outward_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: common_input_service_invoice common_input_service_invoice_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.common_input_service_invoice
    ADD CONSTRAINT common_input_service_invoice_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: common_input_service_invoice common_input_service_invoice_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.common_input_service_invoice
    ADD CONSTRAINT common_input_service_invoice_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: cost_allocation_register cost_allocation_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.cost_allocation_register
    ADD CONSTRAINT cost_allocation_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: cost_allocation_register cost_allocation_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.cost_allocation_register
    ADD CONSTRAINT cost_allocation_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: credit_debit_note_register credit_debit_note_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.credit_debit_note_register
    ADD CONSTRAINT credit_debit_note_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: credit_debit_note_register credit_debit_note_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.credit_debit_note_register
    ADD CONSTRAINT credit_debit_note_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: creditor_ageing_report creditor_ageing_report_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.creditor_ageing_report
    ADD CONSTRAINT creditor_ageing_report_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: creditor_ageing_report creditor_ageing_report_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.creditor_ageing_report
    ADD CONSTRAINT creditor_ageing_report_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: entitlement_instrument entitlement_instrument_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: entitlement_instrument entitlement_instrument_instrument_type_instrument_archetyp_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_instrument_type_instrument_archetyp_fkey FOREIGN KEY (instrument_type, instrument_archetype) REFERENCES platform_ref.document_type(doc_type_code, archetype);


--
-- Name: entitlement_instrument entitlement_instrument_issuing_authority_vt_issuing_author_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_issuing_authority_vt_issuing_author_fkey FOREIGN KEY (issuing_authority_vt, issuing_authority) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: entitlement_instrument entitlement_instrument_status_vt_status_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_status_vt_status_fkey FOREIGN KEY (status_vt, status) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: entitlement_instrument entitlement_instrument_supersedes_instrument_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_supersedes_instrument_id_fkey FOREIGN KEY (supersedes_instrument_id) REFERENCES t_acme_silver.entitlement_instrument(instrument_id);


--
-- Name: fixed_asset_register fixed_asset_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.fixed_asset_register
    ADD CONSTRAINT fixed_asset_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: fixed_asset_register fixed_asset_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.fixed_asset_register
    ADD CONSTRAINT fixed_asset_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: foreign_currency_payment_register foreign_currency_payment_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.foreign_currency_payment_register
    ADD CONSTRAINT foreign_currency_payment_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: foreign_currency_payment_register foreign_currency_payment_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.foreign_currency_payment_register
    ADD CONSTRAINT foreign_currency_payment_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: ingest_batch ingest_batch_document_type_vt_document_type_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_document_type_vt_document_type_fkey FOREIGN KEY (document_type_vt, document_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: ingest_batch ingest_batch_source_stream_vt_source_stream_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_source_stream_vt_source_stream_fkey FOREIGN KEY (source_stream_vt, source_stream) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: ingest_batch ingest_batch_status_vt_status_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_status_vt_status_fkey FOREIGN KEY (status_vt, status) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: ingest_batch ingest_batch_superseded_by_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: inter_gstin_transaction_register inter_gstin_transaction_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.inter_gstin_transaction_register
    ADD CONSTRAINT inter_gstin_transaction_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: inter_gstin_transaction_register inter_gstin_transaction_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.inter_gstin_transaction_register
    ADD CONSTRAINT inter_gstin_transaction_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: itc_reversal_register itc_reversal_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.itc_reversal_register
    ADD CONSTRAINT itc_reversal_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: itc_reversal_register itc_reversal_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.itc_reversal_register
    ADD CONSTRAINT itc_reversal_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: job_work_dispatch_register job_work_dispatch_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.job_work_dispatch_register
    ADD CONSTRAINT job_work_dispatch_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: job_work_dispatch_register job_work_dispatch_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.job_work_dispatch_register
    ADD CONSTRAINT job_work_dispatch_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: payment_register payment_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.payment_register
    ADD CONSTRAINT payment_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: payment_register payment_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.payment_register
    ADD CONSTRAINT payment_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: platform_settlement_report platform_settlement_report_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.platform_settlement_report
    ADD CONSTRAINT platform_settlement_report_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: platform_settlement_report platform_settlement_report_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.platform_settlement_report
    ADD CONSTRAINT platform_settlement_report_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: product_sku_master product_sku_master_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.product_sku_master
    ADD CONSTRAINT product_sku_master_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: product_sku_master product_sku_master_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.product_sku_master
    ADD CONSTRAINT product_sku_master_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: purchase_register purchase_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.purchase_register
    ADD CONSTRAINT purchase_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: purchase_register purchase_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.purchase_register
    ADD CONSTRAINT purchase_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: quarantined_artefact quarantined_artefact_document_type_vt_document_type_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.quarantined_artefact
    ADD CONSTRAINT quarantined_artefact_document_type_vt_document_type_fkey FOREIGN KEY (document_type_vt, document_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: rcm_register rcm_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.rcm_register
    ADD CONSTRAINT rcm_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: rcm_register rcm_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.rcm_register
    ADD CONSTRAINT rcm_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: running_account_contract_register running_account_contract_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.running_account_contract_register
    ADD CONSTRAINT running_account_contract_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: running_account_contract_register running_account_contract_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.running_account_contract_register
    ADD CONSTRAINT running_account_contract_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: sales_register sales_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.sales_register
    ADD CONSTRAINT sales_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: sales_register sales_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.sales_register
    ADD CONSTRAINT sales_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: sales_register sales_register_invoice_type_vt_invoice_type_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.sales_register
    ADD CONSTRAINT sales_register_invoice_type_vt_invoice_type_fkey FOREIGN KEY (invoice_type_vt, invoice_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: sales_register_line sales_register_line_header_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.sales_register_line
    ADD CONSTRAINT sales_register_line_header_id_fkey FOREIGN KEY (header_id) REFERENCES t_acme_silver.sales_register(id) ON DELETE CASCADE;


--
-- Name: sales_register sales_register_supply_type_vt_supply_type_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.sales_register
    ADD CONSTRAINT sales_register_supply_type_vt_supply_type_fkey FOREIGN KEY (supply_type_vt, supply_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: stock_inventory_register stock_inventory_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.stock_inventory_register
    ADD CONSTRAINT stock_inventory_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: stock_inventory_register stock_inventory_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.stock_inventory_register
    ADD CONSTRAINT stock_inventory_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: transaction_document transaction_document_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.transaction_document
    ADD CONSTRAINT transaction_document_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: transaction_document transaction_document_direction_vt_direction_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.transaction_document
    ADD CONSTRAINT transaction_document_direction_vt_direction_fkey FOREIGN KEY (direction_vt, direction) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: transaction_document transaction_document_doc_type_doc_archetype_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.transaction_document
    ADD CONSTRAINT transaction_document_doc_type_doc_archetype_fkey FOREIGN KEY (doc_type, doc_archetype) REFERENCES platform_ref.document_type(doc_type_code, archetype);


--
-- Name: transaction_line transaction_line_doc_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.transaction_line
    ADD CONSTRAINT transaction_line_doc_id_fkey FOREIGN KEY (doc_id) REFERENCES t_acme_silver.transaction_document(doc_id) ON DELETE CASCADE;


--
-- Name: trial_balance trial_balance_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.trial_balance
    ADD CONSTRAINT trial_balance_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: trial_balance trial_balance_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.trial_balance
    ADD CONSTRAINT trial_balance_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: unbilled_revenue_schedule unbilled_revenue_schedule_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.unbilled_revenue_schedule
    ADD CONSTRAINT unbilled_revenue_schedule_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_acme_silver.ingest_batch(batch_id);


--
-- Name: unbilled_revenue_schedule unbilled_revenue_schedule_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_acme_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_acme_silver.unbilled_revenue_schedule
    ADD CONSTRAINT unbilled_revenue_schedule_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: artefact_ledger artefact_ledger_declared_document_type_vt_declared_documen_fkey; Type: FK CONSTRAINT; Schema: t_globex_bronze; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_bronze.artefact_ledger
    ADD CONSTRAINT artefact_ledger_declared_document_type_vt_declared_documen_fkey FOREIGN KEY (declared_document_type_vt, declared_document_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: artefact_ledger artefact_ledger_source_stream_vt_source_stream_fkey; Type: FK CONSTRAINT; Schema: t_globex_bronze; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_bronze.artefact_ledger
    ADD CONSTRAINT artefact_ledger_source_stream_vt_source_stream_fkey FOREIGN KEY (source_stream_vt, source_stream) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: advance_receipt_register advance_receipt_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.advance_receipt_register
    ADD CONSTRAINT advance_receipt_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: advance_receipt_register advance_receipt_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.advance_receipt_register
    ADD CONSTRAINT advance_receipt_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: audited_pl_account audited_pl_account_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.audited_pl_account
    ADD CONSTRAINT audited_pl_account_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: audited_pl_account audited_pl_account_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.audited_pl_account
    ADD CONSTRAINT audited_pl_account_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: balance_sheet balance_sheet_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.balance_sheet
    ADD CONSTRAINT balance_sheet_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: balance_sheet balance_sheet_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.balance_sheet
    ADD CONSTRAINT balance_sheet_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: bank_statement_inward_forex bank_statement_inward_forex_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.bank_statement_inward_forex
    ADD CONSTRAINT bank_statement_inward_forex_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: bank_statement_inward_forex bank_statement_inward_forex_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.bank_statement_inward_forex
    ADD CONSTRAINT bank_statement_inward_forex_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: bank_statement_outward bank_statement_outward_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.bank_statement_outward
    ADD CONSTRAINT bank_statement_outward_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: bank_statement_outward bank_statement_outward_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.bank_statement_outward
    ADD CONSTRAINT bank_statement_outward_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: common_input_service_invoice common_input_service_invoice_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.common_input_service_invoice
    ADD CONSTRAINT common_input_service_invoice_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: common_input_service_invoice common_input_service_invoice_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.common_input_service_invoice
    ADD CONSTRAINT common_input_service_invoice_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: cost_allocation_register cost_allocation_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.cost_allocation_register
    ADD CONSTRAINT cost_allocation_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: cost_allocation_register cost_allocation_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.cost_allocation_register
    ADD CONSTRAINT cost_allocation_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: credit_debit_note_register credit_debit_note_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.credit_debit_note_register
    ADD CONSTRAINT credit_debit_note_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: credit_debit_note_register credit_debit_note_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.credit_debit_note_register
    ADD CONSTRAINT credit_debit_note_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: creditor_ageing_report creditor_ageing_report_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.creditor_ageing_report
    ADD CONSTRAINT creditor_ageing_report_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: creditor_ageing_report creditor_ageing_report_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.creditor_ageing_report
    ADD CONSTRAINT creditor_ageing_report_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: entitlement_instrument entitlement_instrument_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: entitlement_instrument entitlement_instrument_instrument_type_instrument_archetyp_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_instrument_type_instrument_archetyp_fkey FOREIGN KEY (instrument_type, instrument_archetype) REFERENCES platform_ref.document_type(doc_type_code, archetype);


--
-- Name: entitlement_instrument entitlement_instrument_issuing_authority_vt_issuing_author_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_issuing_authority_vt_issuing_author_fkey FOREIGN KEY (issuing_authority_vt, issuing_authority) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: entitlement_instrument entitlement_instrument_status_vt_status_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_status_vt_status_fkey FOREIGN KEY (status_vt, status) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: entitlement_instrument entitlement_instrument_supersedes_instrument_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.entitlement_instrument
    ADD CONSTRAINT entitlement_instrument_supersedes_instrument_id_fkey FOREIGN KEY (supersedes_instrument_id) REFERENCES t_globex_silver.entitlement_instrument(instrument_id);


--
-- Name: fixed_asset_register fixed_asset_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.fixed_asset_register
    ADD CONSTRAINT fixed_asset_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: fixed_asset_register fixed_asset_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.fixed_asset_register
    ADD CONSTRAINT fixed_asset_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: foreign_currency_payment_register foreign_currency_payment_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.foreign_currency_payment_register
    ADD CONSTRAINT foreign_currency_payment_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: foreign_currency_payment_register foreign_currency_payment_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.foreign_currency_payment_register
    ADD CONSTRAINT foreign_currency_payment_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: ingest_batch ingest_batch_document_type_vt_document_type_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_document_type_vt_document_type_fkey FOREIGN KEY (document_type_vt, document_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: ingest_batch ingest_batch_source_stream_vt_source_stream_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_source_stream_vt_source_stream_fkey FOREIGN KEY (source_stream_vt, source_stream) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: ingest_batch ingest_batch_status_vt_status_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_status_vt_status_fkey FOREIGN KEY (status_vt, status) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: ingest_batch ingest_batch_superseded_by_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.ingest_batch
    ADD CONSTRAINT ingest_batch_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: inter_gstin_transaction_register inter_gstin_transaction_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.inter_gstin_transaction_register
    ADD CONSTRAINT inter_gstin_transaction_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: inter_gstin_transaction_register inter_gstin_transaction_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.inter_gstin_transaction_register
    ADD CONSTRAINT inter_gstin_transaction_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: itc_reversal_register itc_reversal_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.itc_reversal_register
    ADD CONSTRAINT itc_reversal_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: itc_reversal_register itc_reversal_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.itc_reversal_register
    ADD CONSTRAINT itc_reversal_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: job_work_dispatch_register job_work_dispatch_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.job_work_dispatch_register
    ADD CONSTRAINT job_work_dispatch_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: job_work_dispatch_register job_work_dispatch_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.job_work_dispatch_register
    ADD CONSTRAINT job_work_dispatch_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: payment_register payment_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.payment_register
    ADD CONSTRAINT payment_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: payment_register payment_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.payment_register
    ADD CONSTRAINT payment_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: platform_settlement_report platform_settlement_report_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.platform_settlement_report
    ADD CONSTRAINT platform_settlement_report_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: platform_settlement_report platform_settlement_report_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.platform_settlement_report
    ADD CONSTRAINT platform_settlement_report_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: product_sku_master product_sku_master_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.product_sku_master
    ADD CONSTRAINT product_sku_master_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: product_sku_master product_sku_master_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.product_sku_master
    ADD CONSTRAINT product_sku_master_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: purchase_register purchase_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.purchase_register
    ADD CONSTRAINT purchase_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: purchase_register purchase_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.purchase_register
    ADD CONSTRAINT purchase_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: quarantined_artefact quarantined_artefact_document_type_vt_document_type_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.quarantined_artefact
    ADD CONSTRAINT quarantined_artefact_document_type_vt_document_type_fkey FOREIGN KEY (document_type_vt, document_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: rcm_register rcm_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.rcm_register
    ADD CONSTRAINT rcm_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: rcm_register rcm_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.rcm_register
    ADD CONSTRAINT rcm_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: running_account_contract_register running_account_contract_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.running_account_contract_register
    ADD CONSTRAINT running_account_contract_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: running_account_contract_register running_account_contract_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.running_account_contract_register
    ADD CONSTRAINT running_account_contract_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: sales_register sales_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.sales_register
    ADD CONSTRAINT sales_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: sales_register sales_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.sales_register
    ADD CONSTRAINT sales_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: sales_register sales_register_invoice_type_vt_invoice_type_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.sales_register
    ADD CONSTRAINT sales_register_invoice_type_vt_invoice_type_fkey FOREIGN KEY (invoice_type_vt, invoice_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: sales_register_line sales_register_line_header_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.sales_register_line
    ADD CONSTRAINT sales_register_line_header_id_fkey FOREIGN KEY (header_id) REFERENCES t_globex_silver.sales_register(id) ON DELETE CASCADE;


--
-- Name: sales_register sales_register_supply_type_vt_supply_type_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.sales_register
    ADD CONSTRAINT sales_register_supply_type_vt_supply_type_fkey FOREIGN KEY (supply_type_vt, supply_type) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: stock_inventory_register stock_inventory_register_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.stock_inventory_register
    ADD CONSTRAINT stock_inventory_register_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: stock_inventory_register stock_inventory_register_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.stock_inventory_register
    ADD CONSTRAINT stock_inventory_register_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: transaction_document transaction_document_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.transaction_document
    ADD CONSTRAINT transaction_document_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: transaction_document transaction_document_direction_vt_direction_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.transaction_document
    ADD CONSTRAINT transaction_document_direction_vt_direction_fkey FOREIGN KEY (direction_vt, direction) REFERENCES platform_ref.universal_master(value_type, value);


--
-- Name: transaction_document transaction_document_doc_type_doc_archetype_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.transaction_document
    ADD CONSTRAINT transaction_document_doc_type_doc_archetype_fkey FOREIGN KEY (doc_type, doc_archetype) REFERENCES platform_ref.document_type(doc_type_code, archetype);


--
-- Name: transaction_line transaction_line_doc_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.transaction_line
    ADD CONSTRAINT transaction_line_doc_id_fkey FOREIGN KEY (doc_id) REFERENCES t_globex_silver.transaction_document(doc_id) ON DELETE CASCADE;


--
-- Name: trial_balance trial_balance_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.trial_balance
    ADD CONSTRAINT trial_balance_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: trial_balance trial_balance_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.trial_balance
    ADD CONSTRAINT trial_balance_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: unbilled_revenue_schedule unbilled_revenue_schedule_batch_id_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.unbilled_revenue_schedule
    ADD CONSTRAINT unbilled_revenue_schedule_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES t_globex_silver.ingest_batch(batch_id);


--
-- Name: unbilled_revenue_schedule unbilled_revenue_schedule_doc_type_code_fkey; Type: FK CONSTRAINT; Schema: t_globex_silver; Owner: tenant_migrate
--

ALTER TABLE ONLY t_globex_silver.unbilled_revenue_schedule
    ADD CONSTRAINT unbilled_revenue_schedule_doc_type_code_fkey FOREIGN KEY (doc_type_code) REFERENCES platform_ref.document_type(doc_type_code);


--
-- Name: SCHEMA app; Type: ACL; Schema: -; Owner: tenant_migrate
--

GRANT USAGE ON SCHEMA app TO PUBLIC;


--
-- Name: SCHEMA platform_ref; Type: ACL; Schema: -; Owner: tenant_migrate
--

GRANT USAGE ON SCHEMA platform_ref TO platform_ref_reader;


--
-- Name: SCHEMA t_acme_bronze; Type: ACL; Schema: -; Owner: tenant_migrate
--

GRANT USAGE ON SCHEMA t_acme_bronze TO t_acme_ingest;
GRANT USAGE ON SCHEMA t_acme_bronze TO t_acme_support;


--
-- Name: SCHEMA t_acme_gold; Type: ACL; Schema: -; Owner: tenant_migrate
--

GRANT USAGE ON SCHEMA t_acme_gold TO t_acme_recon;
GRANT USAGE ON SCHEMA t_acme_gold TO t_acme_support;


--
-- Name: SCHEMA t_acme_silver; Type: ACL; Schema: -; Owner: tenant_migrate
--

GRANT USAGE ON SCHEMA t_acme_silver TO t_acme_ingest;
GRANT USAGE ON SCHEMA t_acme_silver TO t_acme_recon;
GRANT USAGE ON SCHEMA t_acme_silver TO t_acme_support;


--
-- Name: SCHEMA t_globex_bronze; Type: ACL; Schema: -; Owner: tenant_migrate
--

GRANT USAGE ON SCHEMA t_globex_bronze TO t_globex_ingest;
GRANT USAGE ON SCHEMA t_globex_bronze TO t_globex_support;


--
-- Name: SCHEMA t_globex_gold; Type: ACL; Schema: -; Owner: tenant_migrate
--

GRANT USAGE ON SCHEMA t_globex_gold TO t_globex_recon;
GRANT USAGE ON SCHEMA t_globex_gold TO t_globex_support;


--
-- Name: SCHEMA t_globex_silver; Type: ACL; Schema: -; Owner: tenant_migrate
--

GRANT USAGE ON SCHEMA t_globex_silver TO t_globex_ingest;
GRANT USAGE ON SCHEMA t_globex_silver TO t_globex_recon;
GRANT USAGE ON SCHEMA t_globex_silver TO t_globex_support;


--
-- Name: TABLE document_type; Type: ACL; Schema: platform_ref; Owner: tenant_migrate
--

GRANT SELECT ON TABLE platform_ref.document_type TO platform_ref_reader;


--
-- Name: TABLE document_type_ref; Type: ACL; Schema: platform_ref; Owner: tenant_migrate
--

GRANT SELECT ON TABLE platform_ref.document_type_ref TO platform_ref_reader;


--
-- Name: TABLE universal_master; Type: ACL; Schema: platform_ref; Owner: tenant_migrate
--

GRANT SELECT ON TABLE platform_ref.universal_master TO platform_ref_reader;


--
-- Name: TABLE __schema_identity; Type: ACL; Schema: t_acme_bronze; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_bronze.__schema_identity TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_bronze.__schema_identity TO t_acme_support;


--
-- Name: TABLE artefact_ledger; Type: ACL; Schema: t_acme_bronze; Owner: tenant_migrate
--

GRANT SELECT,INSERT ON TABLE t_acme_bronze.artefact_ledger TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_bronze.artefact_ledger TO t_acme_support;


--
-- Name: TABLE __schema_identity; Type: ACL; Schema: t_acme_gold; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_gold.__schema_identity TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_gold.__schema_identity TO t_acme_support;


--
-- Name: TABLE batch_execution; Type: ACL; Schema: t_acme_gold; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_gold.batch_execution TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_gold.batch_execution TO t_acme_support;


--
-- Name: TABLE consumer_watermark; Type: ACL; Schema: t_acme_gold; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_gold.consumer_watermark TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_gold.consumer_watermark TO t_acme_support;


--
-- Name: TABLE fact_record; Type: ACL; Schema: t_acme_gold; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_gold.fact_record TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_gold.fact_record TO t_acme_support;


--
-- Name: TABLE __schema_identity; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.__schema_identity TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.__schema_identity TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.__schema_identity TO t_acme_support;


--
-- Name: TABLE advance_receipt_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.advance_receipt_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.advance_receipt_register TO t_acme_support;


--
-- Name: SEQUENCE advance_receipt_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.advance_receipt_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE audited_pl_account; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.audited_pl_account TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.audited_pl_account TO t_acme_support;


--
-- Name: SEQUENCE audited_pl_account_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.audited_pl_account_id_seq TO t_acme_ingest;


--
-- Name: TABLE balance_sheet; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.balance_sheet TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.balance_sheet TO t_acme_support;


--
-- Name: SEQUENCE balance_sheet_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.balance_sheet_id_seq TO t_acme_ingest;


--
-- Name: TABLE bank_statement_inward_forex; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.bank_statement_inward_forex TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.bank_statement_inward_forex TO t_acme_support;


--
-- Name: SEQUENCE bank_statement_inward_forex_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.bank_statement_inward_forex_id_seq TO t_acme_ingest;


--
-- Name: TABLE bank_statement_outward; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.bank_statement_outward TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.bank_statement_outward TO t_acme_support;


--
-- Name: SEQUENCE bank_statement_outward_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.bank_statement_outward_id_seq TO t_acme_ingest;


--
-- Name: TABLE common_input_service_invoice; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.common_input_service_invoice TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.common_input_service_invoice TO t_acme_support;


--
-- Name: SEQUENCE common_input_service_invoice_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.common_input_service_invoice_id_seq TO t_acme_ingest;


--
-- Name: TABLE cost_allocation_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.cost_allocation_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.cost_allocation_register TO t_acme_support;


--
-- Name: SEQUENCE cost_allocation_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.cost_allocation_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE credit_debit_note_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.credit_debit_note_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.credit_debit_note_register TO t_acme_support;


--
-- Name: SEQUENCE credit_debit_note_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.credit_debit_note_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE creditor_ageing_report; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.creditor_ageing_report TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.creditor_ageing_report TO t_acme_support;


--
-- Name: SEQUENCE creditor_ageing_report_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.creditor_ageing_report_id_seq TO t_acme_ingest;


--
-- Name: TABLE entitlement_instrument; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.entitlement_instrument TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.entitlement_instrument TO t_acme_support;


--
-- Name: TABLE fixed_asset_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.fixed_asset_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.fixed_asset_register TO t_acme_support;


--
-- Name: SEQUENCE fixed_asset_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.fixed_asset_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE foreign_currency_payment_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.foreign_currency_payment_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.foreign_currency_payment_register TO t_acme_support;


--
-- Name: SEQUENCE foreign_currency_payment_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.foreign_currency_payment_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE ingest_batch; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.ingest_batch TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.ingest_batch TO t_acme_support;


--
-- Name: TABLE inter_gstin_transaction_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.inter_gstin_transaction_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.inter_gstin_transaction_register TO t_acme_support;


--
-- Name: SEQUENCE inter_gstin_transaction_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.inter_gstin_transaction_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE itc_reversal_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.itc_reversal_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.itc_reversal_register TO t_acme_support;


--
-- Name: SEQUENCE itc_reversal_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.itc_reversal_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE job_work_dispatch_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.job_work_dispatch_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.job_work_dispatch_register TO t_acme_support;


--
-- Name: SEQUENCE job_work_dispatch_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.job_work_dispatch_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE payment_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.payment_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.payment_register TO t_acme_support;


--
-- Name: SEQUENCE payment_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.payment_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE platform_settlement_report; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.platform_settlement_report TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.platform_settlement_report TO t_acme_support;


--
-- Name: SEQUENCE platform_settlement_report_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.platform_settlement_report_id_seq TO t_acme_ingest;


--
-- Name: TABLE product_sku_master; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.product_sku_master TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.product_sku_master TO t_acme_support;


--
-- Name: SEQUENCE product_sku_master_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.product_sku_master_id_seq TO t_acme_ingest;


--
-- Name: TABLE purchase_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.purchase_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.purchase_register TO t_acme_support;


--
-- Name: SEQUENCE purchase_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.purchase_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE quarantined_artefact; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.quarantined_artefact TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.quarantined_artefact TO t_acme_support;


--
-- Name: TABLE rcm_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.rcm_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.rcm_register TO t_acme_support;


--
-- Name: SEQUENCE rcm_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.rcm_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE running_account_contract_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.running_account_contract_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.running_account_contract_register TO t_acme_support;


--
-- Name: SEQUENCE running_account_contract_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.running_account_contract_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE sales_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.sales_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.sales_register TO t_acme_support;


--
-- Name: SEQUENCE sales_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.sales_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE sales_register_line; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.sales_register_line TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.sales_register_line TO t_acme_support;


--
-- Name: SEQUENCE sales_register_line_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.sales_register_line_id_seq TO t_acme_ingest;


--
-- Name: TABLE stock_inventory_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.stock_inventory_register TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.stock_inventory_register TO t_acme_support;


--
-- Name: SEQUENCE stock_inventory_register_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.stock_inventory_register_id_seq TO t_acme_ingest;


--
-- Name: TABLE transaction_document; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.transaction_document TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.transaction_document TO t_acme_support;


--
-- Name: TABLE transaction_line; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.transaction_line TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.transaction_line TO t_acme_support;


--
-- Name: TABLE trial_balance; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.trial_balance TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.trial_balance TO t_acme_support;


--
-- Name: SEQUENCE trial_balance_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.trial_balance_id_seq TO t_acme_ingest;


--
-- Name: TABLE unbilled_revenue_schedule; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_acme_silver.unbilled_revenue_schedule TO t_acme_ingest;
GRANT SELECT ON TABLE t_acme_silver.unbilled_revenue_schedule TO t_acme_support;


--
-- Name: SEQUENCE unbilled_revenue_schedule_id_seq; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_acme_silver.unbilled_revenue_schedule_id_seq TO t_acme_ingest;


--
-- Name: TABLE v1_advance_receipt_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_advance_receipt_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_advance_receipt_register TO t_acme_support;


--
-- Name: TABLE v1_audited_pl_account; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_audited_pl_account TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_audited_pl_account TO t_acme_support;


--
-- Name: TABLE v1_balance_sheet; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_balance_sheet TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_balance_sheet TO t_acme_support;


--
-- Name: TABLE v1_bank_statement_inward_forex; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_bank_statement_inward_forex TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_bank_statement_inward_forex TO t_acme_support;


--
-- Name: TABLE v1_bank_statement_outward; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_bank_statement_outward TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_bank_statement_outward TO t_acme_support;


--
-- Name: TABLE v1_common_input_service_invoice; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_common_input_service_invoice TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_common_input_service_invoice TO t_acme_support;


--
-- Name: TABLE v1_cost_allocation_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_cost_allocation_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_cost_allocation_register TO t_acme_support;


--
-- Name: TABLE v1_credit_debit_note_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_credit_debit_note_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_credit_debit_note_register TO t_acme_support;


--
-- Name: TABLE v1_creditor_ageing_report; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_creditor_ageing_report TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_creditor_ageing_report TO t_acme_support;


--
-- Name: TABLE v1_entitlement_instrument; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_entitlement_instrument TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_entitlement_instrument TO t_acme_support;


--
-- Name: TABLE v1_fixed_asset_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_fixed_asset_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_fixed_asset_register TO t_acme_support;


--
-- Name: TABLE v1_foreign_currency_payment_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_foreign_currency_payment_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_foreign_currency_payment_register TO t_acme_support;


--
-- Name: TABLE v1_ingest_batch; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_ingest_batch TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_ingest_batch TO t_acme_support;


--
-- Name: TABLE v1_inter_gstin_transaction_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_inter_gstin_transaction_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_inter_gstin_transaction_register TO t_acme_support;


--
-- Name: TABLE v1_itc_reversal_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_itc_reversal_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_itc_reversal_register TO t_acme_support;


--
-- Name: TABLE v1_job_work_dispatch_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_job_work_dispatch_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_job_work_dispatch_register TO t_acme_support;


--
-- Name: TABLE v1_payment_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_payment_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_payment_register TO t_acme_support;


--
-- Name: TABLE v1_platform_settlement_report; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_platform_settlement_report TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_platform_settlement_report TO t_acme_support;


--
-- Name: TABLE v1_product_sku_master; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_product_sku_master TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_product_sku_master TO t_acme_support;


--
-- Name: TABLE v1_purchase_invoice; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_purchase_invoice TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_purchase_invoice TO t_acme_support;


--
-- Name: TABLE v1_purchase_invoice_line; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_purchase_invoice_line TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_purchase_invoice_line TO t_acme_support;


--
-- Name: TABLE v1_purchase_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_purchase_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_purchase_register TO t_acme_support;


--
-- Name: TABLE v1_rcm_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_rcm_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_rcm_register TO t_acme_support;


--
-- Name: TABLE v1_running_account_contract_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_running_account_contract_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_running_account_contract_register TO t_acme_support;


--
-- Name: TABLE v1_sales_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_sales_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_sales_register TO t_acme_support;


--
-- Name: TABLE v1_sales_register_line; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_sales_register_line TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_sales_register_line TO t_acme_support;


--
-- Name: TABLE v1_stock_inventory_register; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_stock_inventory_register TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_stock_inventory_register TO t_acme_support;


--
-- Name: TABLE v1_transaction_document; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_transaction_document TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_transaction_document TO t_acme_support;


--
-- Name: TABLE v1_transaction_line; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_transaction_line TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_transaction_line TO t_acme_support;


--
-- Name: TABLE v1_trial_balance; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_trial_balance TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_trial_balance TO t_acme_support;


--
-- Name: TABLE v1_unbilled_revenue_schedule; Type: ACL; Schema: t_acme_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_acme_silver.v1_unbilled_revenue_schedule TO t_acme_recon;
GRANT SELECT ON TABLE t_acme_silver.v1_unbilled_revenue_schedule TO t_acme_support;


--
-- Name: TABLE __schema_identity; Type: ACL; Schema: t_globex_bronze; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_bronze.__schema_identity TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_bronze.__schema_identity TO t_globex_support;


--
-- Name: TABLE artefact_ledger; Type: ACL; Schema: t_globex_bronze; Owner: tenant_migrate
--

GRANT SELECT,INSERT ON TABLE t_globex_bronze.artefact_ledger TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_bronze.artefact_ledger TO t_globex_support;


--
-- Name: TABLE __schema_identity; Type: ACL; Schema: t_globex_gold; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_gold.__schema_identity TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_gold.__schema_identity TO t_globex_support;


--
-- Name: TABLE batch_execution; Type: ACL; Schema: t_globex_gold; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_gold.batch_execution TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_gold.batch_execution TO t_globex_support;


--
-- Name: TABLE consumer_watermark; Type: ACL; Schema: t_globex_gold; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_gold.consumer_watermark TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_gold.consumer_watermark TO t_globex_support;


--
-- Name: TABLE fact_record; Type: ACL; Schema: t_globex_gold; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_gold.fact_record TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_gold.fact_record TO t_globex_support;


--
-- Name: TABLE __schema_identity; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.__schema_identity TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.__schema_identity TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.__schema_identity TO t_globex_support;


--
-- Name: TABLE advance_receipt_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.advance_receipt_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.advance_receipt_register TO t_globex_support;


--
-- Name: SEQUENCE advance_receipt_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.advance_receipt_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE audited_pl_account; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.audited_pl_account TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.audited_pl_account TO t_globex_support;


--
-- Name: SEQUENCE audited_pl_account_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.audited_pl_account_id_seq TO t_globex_ingest;


--
-- Name: TABLE balance_sheet; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.balance_sheet TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.balance_sheet TO t_globex_support;


--
-- Name: SEQUENCE balance_sheet_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.balance_sheet_id_seq TO t_globex_ingest;


--
-- Name: TABLE bank_statement_inward_forex; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.bank_statement_inward_forex TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.bank_statement_inward_forex TO t_globex_support;


--
-- Name: SEQUENCE bank_statement_inward_forex_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.bank_statement_inward_forex_id_seq TO t_globex_ingest;


--
-- Name: TABLE bank_statement_outward; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.bank_statement_outward TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.bank_statement_outward TO t_globex_support;


--
-- Name: SEQUENCE bank_statement_outward_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.bank_statement_outward_id_seq TO t_globex_ingest;


--
-- Name: TABLE common_input_service_invoice; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.common_input_service_invoice TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.common_input_service_invoice TO t_globex_support;


--
-- Name: SEQUENCE common_input_service_invoice_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.common_input_service_invoice_id_seq TO t_globex_ingest;


--
-- Name: TABLE cost_allocation_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.cost_allocation_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.cost_allocation_register TO t_globex_support;


--
-- Name: SEQUENCE cost_allocation_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.cost_allocation_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE credit_debit_note_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.credit_debit_note_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.credit_debit_note_register TO t_globex_support;


--
-- Name: SEQUENCE credit_debit_note_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.credit_debit_note_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE creditor_ageing_report; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.creditor_ageing_report TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.creditor_ageing_report TO t_globex_support;


--
-- Name: SEQUENCE creditor_ageing_report_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.creditor_ageing_report_id_seq TO t_globex_ingest;


--
-- Name: TABLE entitlement_instrument; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.entitlement_instrument TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.entitlement_instrument TO t_globex_support;


--
-- Name: TABLE fixed_asset_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.fixed_asset_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.fixed_asset_register TO t_globex_support;


--
-- Name: SEQUENCE fixed_asset_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.fixed_asset_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE foreign_currency_payment_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.foreign_currency_payment_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.foreign_currency_payment_register TO t_globex_support;


--
-- Name: SEQUENCE foreign_currency_payment_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.foreign_currency_payment_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE ingest_batch; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.ingest_batch TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.ingest_batch TO t_globex_support;


--
-- Name: TABLE inter_gstin_transaction_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.inter_gstin_transaction_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.inter_gstin_transaction_register TO t_globex_support;


--
-- Name: SEQUENCE inter_gstin_transaction_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.inter_gstin_transaction_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE itc_reversal_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.itc_reversal_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.itc_reversal_register TO t_globex_support;


--
-- Name: SEQUENCE itc_reversal_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.itc_reversal_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE job_work_dispatch_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.job_work_dispatch_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.job_work_dispatch_register TO t_globex_support;


--
-- Name: SEQUENCE job_work_dispatch_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.job_work_dispatch_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE payment_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.payment_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.payment_register TO t_globex_support;


--
-- Name: SEQUENCE payment_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.payment_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE platform_settlement_report; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.platform_settlement_report TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.platform_settlement_report TO t_globex_support;


--
-- Name: SEQUENCE platform_settlement_report_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.platform_settlement_report_id_seq TO t_globex_ingest;


--
-- Name: TABLE product_sku_master; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.product_sku_master TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.product_sku_master TO t_globex_support;


--
-- Name: SEQUENCE product_sku_master_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.product_sku_master_id_seq TO t_globex_ingest;


--
-- Name: TABLE purchase_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.purchase_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.purchase_register TO t_globex_support;


--
-- Name: SEQUENCE purchase_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.purchase_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE quarantined_artefact; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.quarantined_artefact TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.quarantined_artefact TO t_globex_support;


--
-- Name: TABLE rcm_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.rcm_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.rcm_register TO t_globex_support;


--
-- Name: SEQUENCE rcm_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.rcm_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE running_account_contract_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.running_account_contract_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.running_account_contract_register TO t_globex_support;


--
-- Name: SEQUENCE running_account_contract_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.running_account_contract_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE sales_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.sales_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.sales_register TO t_globex_support;


--
-- Name: SEQUENCE sales_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.sales_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE sales_register_line; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.sales_register_line TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.sales_register_line TO t_globex_support;


--
-- Name: SEQUENCE sales_register_line_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.sales_register_line_id_seq TO t_globex_ingest;


--
-- Name: TABLE stock_inventory_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.stock_inventory_register TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.stock_inventory_register TO t_globex_support;


--
-- Name: SEQUENCE stock_inventory_register_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.stock_inventory_register_id_seq TO t_globex_ingest;


--
-- Name: TABLE transaction_document; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.transaction_document TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.transaction_document TO t_globex_support;


--
-- Name: TABLE transaction_line; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.transaction_line TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.transaction_line TO t_globex_support;


--
-- Name: TABLE trial_balance; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.trial_balance TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.trial_balance TO t_globex_support;


--
-- Name: SEQUENCE trial_balance_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.trial_balance_id_seq TO t_globex_ingest;


--
-- Name: TABLE unbilled_revenue_schedule; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE t_globex_silver.unbilled_revenue_schedule TO t_globex_ingest;
GRANT SELECT ON TABLE t_globex_silver.unbilled_revenue_schedule TO t_globex_support;


--
-- Name: SEQUENCE unbilled_revenue_schedule_id_seq; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT USAGE ON SEQUENCE t_globex_silver.unbilled_revenue_schedule_id_seq TO t_globex_ingest;


--
-- Name: TABLE v1_advance_receipt_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_advance_receipt_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_advance_receipt_register TO t_globex_support;


--
-- Name: TABLE v1_audited_pl_account; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_audited_pl_account TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_audited_pl_account TO t_globex_support;


--
-- Name: TABLE v1_balance_sheet; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_balance_sheet TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_balance_sheet TO t_globex_support;


--
-- Name: TABLE v1_bank_statement_inward_forex; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_bank_statement_inward_forex TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_bank_statement_inward_forex TO t_globex_support;


--
-- Name: TABLE v1_bank_statement_outward; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_bank_statement_outward TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_bank_statement_outward TO t_globex_support;


--
-- Name: TABLE v1_common_input_service_invoice; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_common_input_service_invoice TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_common_input_service_invoice TO t_globex_support;


--
-- Name: TABLE v1_cost_allocation_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_cost_allocation_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_cost_allocation_register TO t_globex_support;


--
-- Name: TABLE v1_credit_debit_note_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_credit_debit_note_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_credit_debit_note_register TO t_globex_support;


--
-- Name: TABLE v1_creditor_ageing_report; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_creditor_ageing_report TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_creditor_ageing_report TO t_globex_support;


--
-- Name: TABLE v1_entitlement_instrument; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_entitlement_instrument TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_entitlement_instrument TO t_globex_support;


--
-- Name: TABLE v1_fixed_asset_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_fixed_asset_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_fixed_asset_register TO t_globex_support;


--
-- Name: TABLE v1_foreign_currency_payment_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_foreign_currency_payment_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_foreign_currency_payment_register TO t_globex_support;


--
-- Name: TABLE v1_ingest_batch; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_ingest_batch TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_ingest_batch TO t_globex_support;


--
-- Name: TABLE v1_inter_gstin_transaction_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_inter_gstin_transaction_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_inter_gstin_transaction_register TO t_globex_support;


--
-- Name: TABLE v1_itc_reversal_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_itc_reversal_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_itc_reversal_register TO t_globex_support;


--
-- Name: TABLE v1_job_work_dispatch_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_job_work_dispatch_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_job_work_dispatch_register TO t_globex_support;


--
-- Name: TABLE v1_payment_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_payment_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_payment_register TO t_globex_support;


--
-- Name: TABLE v1_platform_settlement_report; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_platform_settlement_report TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_platform_settlement_report TO t_globex_support;


--
-- Name: TABLE v1_product_sku_master; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_product_sku_master TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_product_sku_master TO t_globex_support;


--
-- Name: TABLE v1_purchase_invoice; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_purchase_invoice TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_purchase_invoice TO t_globex_support;


--
-- Name: TABLE v1_purchase_invoice_line; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_purchase_invoice_line TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_purchase_invoice_line TO t_globex_support;


--
-- Name: TABLE v1_purchase_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_purchase_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_purchase_register TO t_globex_support;


--
-- Name: TABLE v1_rcm_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_rcm_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_rcm_register TO t_globex_support;


--
-- Name: TABLE v1_running_account_contract_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_running_account_contract_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_running_account_contract_register TO t_globex_support;


--
-- Name: TABLE v1_sales_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_sales_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_sales_register TO t_globex_support;


--
-- Name: TABLE v1_sales_register_line; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_sales_register_line TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_sales_register_line TO t_globex_support;


--
-- Name: TABLE v1_stock_inventory_register; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_stock_inventory_register TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_stock_inventory_register TO t_globex_support;


--
-- Name: TABLE v1_transaction_document; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_transaction_document TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_transaction_document TO t_globex_support;


--
-- Name: TABLE v1_transaction_line; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_transaction_line TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_transaction_line TO t_globex_support;


--
-- Name: TABLE v1_trial_balance; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_trial_balance TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_trial_balance TO t_globex_support;


--
-- Name: TABLE v1_unbilled_revenue_schedule; Type: ACL; Schema: t_globex_silver; Owner: tenant_migrate
--

GRANT SELECT ON TABLE t_globex_silver.v1_unbilled_revenue_schedule TO t_globex_recon;
GRANT SELECT ON TABLE t_globex_silver.v1_unbilled_revenue_schedule TO t_globex_support;


--
-- PostgreSQL database dump complete
--

\unrestrict nkHd10lQvRk5EvOxJRJ40gDpJzgaTGu9RYZHqzrea1nR01B9n0Ptj3HiEfwtcgT

