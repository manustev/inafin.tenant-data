-- =============================================================================
-- Shared migration 002 — platform_ref: the Category B universal master table.
--
-- ARCHITECTURE.md 4: platform-db is the AUTHORING source for Category B. This
-- schema is its replica inside tenant-db, so tenant tables can carry real FK
-- constraints to reference data with no cross-cluster hop. Phase 1 seeds it
-- from this file; Phase 2 replaces the seed with logical replication and this
-- schema becomes read-only to everything including tenant_migrate.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS platform_ref AUTHORIZATION tenant_migrate;

-- Structure per the Full Data Architecture, Category B Pattern 1.
CREATE TABLE IF NOT EXISTS platform_ref.universal_master (
    value_type       text NOT NULL,
    value            text NOT NULL,
    description      text NOT NULL,
    from_date        date NOT NULL DEFAULT DATE '1900-01-01',
    to_date          date NOT NULL DEFAULT DATE '9999-12-31',
    creation_date    timestamptz NOT NULL DEFAULT now(),
    last_update_date timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL DEFAULT 'platform',
    updated_by       text NOT NULL DEFAULT 'platform',
    attribute_1      text, attribute_2  text, attribute_3  text, attribute_4  text,
    attribute_5      text, attribute_6  text, attribute_7  text, attribute_8  text,
    attribute_9      text, attribute_10 text, attribute_11 text, attribute_12 text,
    attribute_13     text, attribute_14 text, attribute_15 text,

    -- Composite PK. `value` alone is NOT unique across value_types — 'EXPORT'
    -- is both a Customer_Category and a plausible Domain — so referencing
    -- tables FK on (value_type, value) via a generated constant column. See
    -- the document_type_vt pattern in migrations/tenant/002_silver.sql.
    PRIMARY KEY (value_type, value),

    CONSTRAINT universal_master_window CHECK (to_date > from_date)
);

COMMENT ON TABLE platform_ref.universal_master IS
    'Category B Pattern 1. Every categorical value in the platform. Adding a '
    'reference type or a value is a data entry, never a schema change. Note '
    'that an FK proves existence, not temporal validity — from_date/to_date '
    'must still be resolved as-of the relevant date by the caller.';

REVOKE ALL ON platform_ref.universal_master FROM PUBLIC;

-- Granted once, to the group. Tenant roles reach it through membership, so the
-- concurrent fan-out never contends on this schema's ACL.
GRANT USAGE ON SCHEMA platform_ref TO platform_ref_reader;
GRANT SELECT ON platform_ref.universal_master TO platform_ref_reader;


-- --- Seed -------------------------------------------------------------------
-- Only the value types Phase 1 exercises. The full catalogue arrives with the
-- Document Type Registry in Phase 4.

INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    -- Ingestion streams. ARCHITECTURE.md 7 — Stream C is the addition the
    -- Source Document Register requires and the A-F taxonomy has no home for.
    ('Source_Stream', 'A', 'Portal-authoritative — GSTN / ICEGATE / DGFT API'),
    ('Source_Stream', 'B', 'Client-authoritative — ERP connector or upload'),
    ('Source_Stream', 'C', 'Third-party-authoritative — marketplace, AD bank, MCA, NCLT'),

    -- Batch lifecycle. Amendments arrive as new batches that mark prior
    -- batches SUPERSEDED, which is why status is explicit and not inferred.
    ('Batch_Status', 'READY',       'Available for downstream consumption'),
    ('Batch_Status', 'SUPERSEDED',  'Replaced by a later batch for the same scope'),
    ('Batch_Status', 'QUARANTINED', 'Failed validation; not consumable'),

    -- Bronze artefact lifecycle.
    ('Artefact_Status', 'RECEIVED',    'Stored in object store, not yet parsed'),
    ('Artefact_Status', 'PARSED',      'Parsed, not yet promoted to Silver'),
    ('Artefact_Status', 'PROMOTED',    'Promoted to a Silver batch'),
    ('Artefact_Status', 'QUARANTINED', 'Failed validation; retained as evidence'),
    ('Artefact_Status', 'REJECTED',    'Rejected at intake — duplicate or unreadable'),

    -- Document types. Phase 1 exercises PURCHASE_INVOICE only (PHASE1-PLAN D5);
    -- the rest are seeded so the FK is proven against a realistic catalogue.
    ('Document_Type', 'PURCHASE_INVOICE', 'Inward supply invoice — ITC entitlement per s.16'),
    ('Document_Type', 'SALES_INVOICE',    'Outward supply invoice — GSTR-1 reconciliation'),
    ('Document_Type', 'CREDIT_NOTE',      'Credit note — s.34 compliance'),
    ('Document_Type', 'DEBIT_NOTE',       'Debit note — valuation adjustment'),
    ('Document_Type', 'PAYMENT_RECORD',   '180-day ITC reversal tracking per s.16(2)')
ON CONFLICT (value_type, value) DO NOTHING;
