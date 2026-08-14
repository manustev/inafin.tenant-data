-- =============================================================================
-- Shared migration 029 — HSN/SAC code catalog.
--
-- Referenced by inafin-api today but created by no migration anywhere — the
-- API depends on this list without owning it. Same shape as platform_ref.
-- document_type (003_document_registry.sql): a lookup table, not tenant data.
--
-- Seeded EMPTY. No real HSN/SAC code list exists anywhere in this workspace
-- to seed from — stated plainly rather than invented. See TODO.md for the
-- pending real data source.
-- =============================================================================

CREATE TABLE IF NOT EXISTS platform_ref.hsn_master (
    hsn_code    text PRIMARY KEY,
    description text NOT NULL,
    gst_rate    numeric(5, 2),
    effective_from date NOT NULL DEFAULT DATE '1900-01-01',
    effective_to   date NOT NULL DEFAULT DATE '9999-12-31',
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT hsn_master_window CHECK (effective_to > effective_from)
);

CREATE TABLE IF NOT EXISTS platform_ref.sac_master (
    sac_code    text PRIMARY KEY,
    description text NOT NULL,
    gst_rate    numeric(5, 2),
    effective_from date NOT NULL DEFAULT DATE '1900-01-01',
    effective_to   date NOT NULL DEFAULT DATE '9999-12-31',
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT sac_master_window CHECK (effective_to > effective_from)
);

REVOKE ALL ON platform_ref.hsn_master FROM PUBLIC;
REVOKE ALL ON platform_ref.sac_master FROM PUBLIC;

GRANT SELECT ON platform_ref.hsn_master, platform_ref.sac_master TO platform_ref_reader;
