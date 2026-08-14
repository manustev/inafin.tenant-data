-- =============================================================================
-- Shared migration 032 — rebuild the onboarding tables (026-028) and the
-- HSN/SAC catalog (029) against inafin-api's REAL write contract.
--
-- 026-028 were built from table names alone (seventh session) — inafin-api's
-- withdrawn migrations/platform/0002-0004 were never read, only guessed at
-- the column level. inafin-api's hand-off spec then confirmed the guess was
-- wrong ("materially different column shapes"; HANDOFF-2026-08-14-session7.md
-- §4, TODO.md item 00). This migration is the deliberate reconciliation that
-- item called for: inafin-api supplied the exact column list per table
-- directly, and every table below adopts it verbatim rather than another
-- single-session guess.
--
-- Safe to DROP + recreate rather than ALTER path-by-path: confirmed via
-- direct query against the live shared cluster that every one of these 15
-- tables holds zero rows (no production tenant exists yet — TODO.md's six
-- deploy blockers are all still open). No data migration is needed. Per
-- CLAUDE.md, this Postgres is a permanently shared dev server now — this is
-- a targeted DROP/CREATE of specific objects this repo owns, not a reset of
-- the server.
--
-- One deliberate, additive deviation from the literal contract, flagged
-- rather than silently resolved: inafin-api's own note says
-- "customer_document.document_id/doc_type_code/status cannot safely
-- substitute for the API's document workflow fields without an agreed
-- mapping" — their contract has no doc_type_code at all, using free-text
-- document_name/category/requirement instead. Session 7's registry-linkage
-- design ("the onboarding document picklist and the ingestion registry are
-- one list, not two") is a real property worth keeping, so customer_document
-- and data_requirement each keep an OPTIONAL, NULLABLE doc_type_code column
-- bridging to platform_ref.document_type, alongside every column the API
-- contract asks for. inafin-api never has to populate it; nothing here
-- requires it. If that bridge turns out not to be wanted either, drop it in
-- a follow-up — it costs nothing sitting empty and unblocks integration now.
--
-- No CHECK constraint below encodes a vocabulary inafin-api did not state
-- explicitly (registration_type/registration_status, priority,
-- requirement_status, category on data_requirement) — inventing one is
-- exactly the mistake 026-028 already made once for onboarding_customer.status
-- and gst_registration.status. Free text until inafin-api names the values.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Drop the old (guessed) shapes. Children before parents; grants and views
-- built on top of a dropped table are automatically gone and reinstated
-- below, in migration 030's exact column set for app_login and the
-- platform_ref_reader set from 026-028.
--
-- onboarding_customer is the one exception: it is NOT dropped. Two objects
-- outside this migration's own file hold a real FK to it —
-- platform_ref.tenant_customer (026, unaffected by this reconciliation) and
-- t_<slug>_gold.deboarding_case (tenant migration 025, one per tenant
-- schema). DROP ... CASCADE would silently strip deboarding_case's FK
-- constraint in every tenant schema, which is a real regression this
-- migration must not cause. onboarding_customer is altered in place instead
-- — customer_id's type and meaning as primary key are unchanged, so both
-- dependents keep working across this migration with no action needed on
-- their side.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS platform_ref.smart_question_response;
DROP TABLE IF EXISTS platform_ref.smart_question;
DROP TABLE IF EXISTS platform_ref.smart_question_run;
DROP TABLE IF EXISTS platform_ref.data_requirement;
DROP TABLE IF EXISTS platform_ref.customer_document;
DROP TABLE IF EXISTS platform_ref.gst_registration;
DROP TABLE IF EXISTS platform_ref.onboarding_state;
DROP TABLE IF EXISTS platform_ref.onboarding_product_service;
DROP TABLE IF EXISTS platform_ref.onboarding_business_profile;
DROP TABLE IF EXISTS platform_ref.customer_profile;
DROP TABLE IF EXISTS platform_ref.principal_customer_access;
DROP TABLE IF EXISTS platform_ref.hsn_master;
DROP TABLE IF EXISTS platform_ref.sac_master;

-- ---------------------------------------------------------------------------
-- The customer record itself — altered in place, see note above.
-- ---------------------------------------------------------------------------
ALTER TABLE platform_ref.onboarding_customer
    ADD COLUMN customer_code text,
    ADD COLUMN is_enabled boolean NOT NULL DEFAULT true;

ALTER TABLE platform_ref.onboarding_customer
    RENAME COLUMN status TO onboarding_status;

ALTER TABLE platform_ref.onboarding_customer
    DROP CONSTRAINT onboarding_customer_status_check;

ALTER TABLE platform_ref.onboarding_customer
    ADD CONSTRAINT onboarding_customer_onboarding_status_check
        CHECK (onboarding_status IN ('DRAFT', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'));

ALTER TABLE platform_ref.onboarding_customer
    ALTER COLUMN onboarding_status SET DEFAULT 'DRAFT';

-- No production customer exists yet (table holds zero rows, confirmed
-- against the live cluster before this migration was written), so
-- backfilling customer_code is unnecessary — go straight to NOT NULL UNIQUE.
ALTER TABLE platform_ref.onboarding_customer
    ALTER COLUMN customer_code SET NOT NULL,
    ADD CONSTRAINT onboarding_customer_customer_code_key UNIQUE (customer_code);

-- Who may act on a customer, and how. READ/WRITE/ADMIN per inafin-api's
-- access-level vocabulary — not the OWNER/COLLABORATOR/VIEWER 026 guessed.
CREATE TABLE platform_ref.principal_customer_access (
    principal_id uuid NOT NULL REFERENCES platform_ref.api_principal (principal_id),
    customer_id  uuid NOT NULL REFERENCES platform_ref.onboarding_customer (customer_id),
    access_level text NOT NULL CHECK (access_level IN ('READ', 'WRITE', 'ADMIN')),
    is_enabled   boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   text NOT NULL,
    modified_at  timestamptz NOT NULL DEFAULT now(),
    modified_by  text NOT NULL,
    PRIMARY KEY (principal_id, customer_id)
);

-- Legal/registration facts. 026's guessed onboarding_business_profile had put
-- pan/cin here; inafin-api's contract puts them on customer_profile instead.
CREATE TABLE platform_ref.customer_profile (
    customer_id    uuid PRIMARY KEY
                       REFERENCES platform_ref.onboarding_customer (customer_id),
    trade_name     text,
    pan            text,
    cin            text,
    constitution   text,
    industry       text,
    sub_industry   text,
    financial_year text,
    address        jsonb NOT NULL DEFAULT '{}'::jsonb,
    contact_person text,
    email          text,
    phone          text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     text NOT NULL,
    modified_at    timestamptz NOT NULL DEFAULT now(),
    modified_by    text NOT NULL
);

-- Operating-model facts, distinct from customer_profile's legal/registration
-- facts. Wholly different columns from 026's guessed version (business_type/
-- incorporation_date/pan/cin/registered_address) — none of those survive here.
CREATE TABLE platform_ref.onboarding_business_profile (
    customer_id                 uuid PRIMARY KEY
                                    REFERENCES platform_ref.onboarding_customer (customer_id),
    activities                  text[] NOT NULL DEFAULT '{}',
    business_model               text CHECK (business_model IN ('B2B', 'B2C', 'Hybrid')),
    b2b_mix                     text,
    locations_count             integer,
    annual_turnover_band        text,
    monthly_invoice_volume_band text,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    created_by                  text NOT NULL,
    modified_at                 timestamptz NOT NULL DEFAULT now(),
    modified_by                 text NOT NULL
);

CREATE TABLE platform_ref.onboarding_product_service (
    product_service_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id         uuid NOT NULL
                            REFERENCES platform_ref.onboarding_customer (customer_id),
    name                text NOT NULL,
    description         text,
    category            text,
    item_type           text CHECK (item_type IN ('GOODS', 'SERVICES')),
    hsn_sac             text,
    gst_rate            numeric(5, 2),
    is_enabled          boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text NOT NULL,
    modified_at         timestamptz NOT NULL DEFAULT now(),
    modified_by         text NOT NULL
);

CREATE INDEX onboarding_product_service_customer_idx
    ON platform_ref.onboarding_product_service (customer_id);

-- A customer may hold more than one GSTIN (multi-state registration).
-- registration_type/registration_status are free text on purpose — inafin-api
-- did not enumerate a vocabulary for either, and 026's guessed
-- ACTIVE/CANCELLED/SUSPENDED CHECK is exactly the kind of invented vocabulary
-- that made the first attempt wrong.
CREATE TABLE platform_ref.gst_registration (
    gst_registration_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id          uuid NOT NULL
                             REFERENCES platform_ref.onboarding_customer (customer_id),
    state                text NOT NULL,
    gstin                text NOT NULL,
    registration_type    text,
    registration_status  text,
    is_enabled           boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now(),
    created_by           text NOT NULL,
    modified_at          timestamptz NOT NULL DEFAULT now(),
    modified_by          text NOT NULL,
    UNIQUE (customer_id, gstin)
);

-- inafin-api's document workflow is free-text (document_name/category/
-- requirement), not the Document Type Registry's controlled vocabulary.
-- doc_type_code is kept as an OPTIONAL bridge to platform_ref.document_type
-- (see this file's header) — nullable, not part of inafin-api's contract,
-- never required by it.
CREATE TABLE platform_ref.customer_document (
    onboarding_document_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id             uuid NOT NULL
                                REFERENCES platform_ref.onboarding_customer (customer_id),
    document_name           text NOT NULL,
    category                text NOT NULL CHECK (category IN ('COMPANY', 'GST', 'BUSINESS')),
    requirement              text NOT NULL CHECK (requirement IN ('Required', 'Recommended', 'Optional')),
    document_status         text NOT NULL DEFAULT 'Missing'
                                CHECK (document_status IN ('Uploaded', 'Missing', 'Verifying')),
    object_uri               text,
    is_enabled               boolean NOT NULL DEFAULT true,
    doc_type_code            text REFERENCES platform_ref.document_type (doc_type_code),
    created_at                timestamptz NOT NULL DEFAULT now(),
    created_by                text NOT NULL,
    modified_at                timestamptz NOT NULL DEFAULT now(),
    modified_by                text NOT NULL
);

COMMENT ON COLUMN platform_ref.customer_document.doc_type_code IS
    'Optional bridge to the Document Type Registry (platform_ref.document_type).'
    ' Not part of inafin-api''s write contract and never required by it — see'
    ' 032''s header for why it is kept anyway.';

CREATE INDEX customer_document_customer_idx
    ON platform_ref.customer_document (customer_id);

-- What a customer still owes before onboarding can complete. Same optional
-- doc_type_code bridge as customer_document, same reasoning.
CREATE TABLE platform_ref.data_requirement (
    data_requirement_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id          uuid NOT NULL
                             REFERENCES platform_ref.onboarding_customer (customer_id),
    requirement_name     text NOT NULL,
    category              text,
    priority              text,
    reason                text,
    requirement_status    text,
    is_enabled            boolean NOT NULL DEFAULT true,
    doc_type_code         text REFERENCES platform_ref.document_type (doc_type_code),
    created_at             timestamptz NOT NULL DEFAULT now(),
    created_by             text NOT NULL,
    modified_at             timestamptz NOT NULL DEFAULT now(),
    modified_by             text NOT NULL
);

COMMENT ON COLUMN platform_ref.data_requirement.doc_type_code IS
    'Optional bridge to the Document Type Registry, same reasoning as'
    ' customer_document.doc_type_code — see 032''s header.';

-- The onboarding wizard's own progress record. inafin-api's contract is a
-- single opaque jsonb blob ("persisted wizard state; it is not equivalent to
-- only current_step and completed_steps") — 028's typed current_step/
-- completed_steps[] shape does not survive.
CREATE TABLE platform_ref.onboarding_state (
    customer_id uuid PRIMARY KEY
                    REFERENCES platform_ref.onboarding_customer (customer_id),
    state       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    modified_at timestamptz NOT NULL DEFAULT now(),
    modified_by text NOT NULL
);

-- One run of the Smart Questions recommender per (customer, input state).
-- "One active GENERATED run per customer" is enforced below by a partial
-- unique index, not a CHECK — the invariant is about the set of live rows,
-- which only an index can enforce.
CREATE TABLE platform_ref.smart_question_run (
    question_run_id      uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id           uuid NOT NULL
                              REFERENCES platform_ref.onboarding_customer (customer_id),
    input_fingerprint     text NOT NULL,
    recommender_type      text NOT NULL,
    recommender_version   text NOT NULL,
    status                text NOT NULL DEFAULT 'GENERATED'
                              CHECK (status IN ('GENERATED', 'FAILED', 'SUPERSEDED')),
    generated_at           timestamptz,
    generated_by           text,
    failure_reason         text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    created_by              text NOT NULL,
    modified_at              timestamptz NOT NULL DEFAULT now(),
    modified_by              text NOT NULL
);

CREATE UNIQUE INDEX smart_question_run_one_active_idx
    ON platform_ref.smart_question_run (customer_id)
    WHERE status = 'GENERATED';

CREATE TABLE platform_ref.smart_question (
    question_id      uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    question_run_id   uuid NOT NULL
                          REFERENCES platform_ref.smart_question_run (question_run_id),
    question_code     text NOT NULL,
    question_text     text NOT NULL,
    question_type     text NOT NULL CHECK (question_type IN ('boolean', 'select', 'text')),
    depends_on_codes  text[] NOT NULL DEFAULT '{}',
    options            jsonb,
    trigger_codes      text[] NOT NULL DEFAULT '{}',
    display_order      integer NOT NULL,
    is_enabled         boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text NOT NULL,
    modified_at          timestamptz NOT NULL DEFAULT now(),
    modified_by          text NOT NULL,
    UNIQUE (question_run_id, question_code)
);

CREATE TABLE platform_ref.smart_question_response (
    response_id  uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id  uuid NOT NULL
                     REFERENCES platform_ref.onboarding_customer (customer_id),
    question_id  uuid NOT NULL REFERENCES platform_ref.smart_question (question_id),
    answer       jsonb NOT NULL,
    answered_at  timestamptz,
    answered_by  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   text NOT NULL,
    modified_at  timestamptz NOT NULL DEFAULT now(),
    modified_by  text NOT NULL,
    UNIQUE (customer_id, question_id)
);

-- ---------------------------------------------------------------------------
-- HSN/SAC catalog, rebuilt to inafin-api's exact 4-column contract. 029's
-- effective_from/effective_to validity window is dropped — inafin-api did not
-- ask for it and the table is still seeded empty (TODO.md), so nothing is
-- lost. Re-add a validity window later if a real source of dated HSN/SAC
-- revisions shows up; do not guess one back in now.
-- ---------------------------------------------------------------------------
CREATE TABLE platform_ref.hsn_master (
    hsn_code        text PRIMARY KEY,
    hsn_description text NOT NULL,
    gst_rate        numeric(5, 2),
    is_active        boolean NOT NULL DEFAULT true
);

CREATE TABLE platform_ref.sac_master (
    sac_code        text PRIMARY KEY,
    sac_description text NOT NULL,
    gst_rate        numeric(5, 2),
    is_active        boolean NOT NULL DEFAULT true
);

-- ---------------------------------------------------------------------------
-- Grants — reinstate exactly what 026-030 granted, table-for-table. Dropping
-- a table drops its grants with it, so this migration is also the reason
-- migration 030 keeps working unmodified: same table names, same privileges,
-- new columns underneath.
-- ---------------------------------------------------------------------------
REVOKE ALL ON platform_ref.onboarding_customer FROM PUBLIC;
REVOKE ALL ON platform_ref.principal_customer_access FROM PUBLIC;
REVOKE ALL ON platform_ref.customer_profile FROM PUBLIC;
REVOKE ALL ON platform_ref.onboarding_business_profile FROM PUBLIC;
REVOKE ALL ON platform_ref.onboarding_product_service FROM PUBLIC;
REVOKE ALL ON platform_ref.gst_registration FROM PUBLIC;
REVOKE ALL ON platform_ref.customer_document FROM PUBLIC;
REVOKE ALL ON platform_ref.data_requirement FROM PUBLIC;
REVOKE ALL ON platform_ref.onboarding_state FROM PUBLIC;
REVOKE ALL ON platform_ref.smart_question_run FROM PUBLIC;
REVOKE ALL ON platform_ref.smart_question FROM PUBLIC;
REVOKE ALL ON platform_ref.smart_question_response FROM PUBLIC;
REVOKE ALL ON platform_ref.hsn_master FROM PUBLIC;
REVOKE ALL ON platform_ref.sac_master FROM PUBLIC;

GRANT SELECT ON platform_ref.onboarding_customer, platform_ref.principal_customer_access,
    platform_ref.customer_profile, platform_ref.onboarding_business_profile,
    platform_ref.onboarding_product_service, platform_ref.gst_registration,
    platform_ref.customer_document, platform_ref.data_requirement,
    platform_ref.onboarding_state, platform_ref.smart_question_run,
    platform_ref.smart_question, platform_ref.smart_question_response,
    platform_ref.hsn_master, platform_ref.sac_master
    TO platform_ref_reader;

-- app_login's ambient access — same table set, same split (read-only
-- lookups vs read/write onboarding writes), that migration 030 already
-- granted. Reissued here because DROP TABLE revoked it.
GRANT SELECT ON
    platform_ref.hsn_master,
    platform_ref.sac_master,
    platform_ref.principal_customer_access
    TO app_login;

GRANT SELECT, INSERT, UPDATE ON
    platform_ref.onboarding_customer,
    platform_ref.customer_profile,
    platform_ref.onboarding_business_profile,
    platform_ref.onboarding_product_service,
    platform_ref.gst_registration,
    platform_ref.customer_document,
    platform_ref.data_requirement,
    platform_ref.onboarding_state,
    platform_ref.smart_question_run,
    platform_ref.smart_question,
    platform_ref.smart_question_response
    TO app_login;
