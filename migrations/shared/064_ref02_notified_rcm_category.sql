-- =============================================================================
-- Shared migration 064 — platform-wide REF-02 notified RCM category reference
-- (inafin-reconciliation-engine's PRIM-07 request).
--
-- Platform-wide, not per-tenant: which supplies are notified under GST's
-- reverse-charge mechanism is a fact of law, the same for every tenant —
-- there is no entity_id/gstin column here, unlike tenant_setting (038) and
-- gl_category_bridge (039), which really are per-tenant/per-client config.
-- Lives in platform_ref, same shape as hsn_master/sac_master (029): a
-- platform reference master table, not tenant data.
--
-- Content: seeded EMPTY, same posture as hsn_master/sac_master (029) and for
-- the same reason stated there — "no real [list] exists anywhere in this
-- workspace to seed from, stated plainly rather than invented." The notified
-- RCM categories are real, publicly notified facts (CGST Act s.9(3)/9(4) and
-- its amending notifications), not client data, but this repo has no citable
-- source text for them checked in anywhere (checked inafin-gst-corpus — no
-- RCM/reverse-charge notification text there either). Fabricating a
-- threshold_amount or trigger_rate_percent that LOOKS plausible is worse
-- here than an empty table: this is compliance data with a real financial
-- consequence when read as authoritative, not a test fixture with an
-- explicit "TEST_" marker. Populating this table for real is a follow-up
-- task, gated on a real notification citation, not a code change here.
--
-- Grant model — a deliberate exception to platform_ref's own norm: every
-- other platform_ref table this repo publishes to platform_ref_reader
-- (universal_master, hsn_master, document_type, ...) is granted DIRECTLY as
-- a base table. This one is granted only through a v1_ view, because the
-- request explicitly asked for the same "base table never granted" lineage
-- discipline this repo already gives every Silver v1_ contract, and there is
-- no reason to refuse it just because platform_ref hasn't required it
-- before. platform_ref_reader is already the right grantee, not a new role:
-- every tenant role (ingest/recon/support/recon_engine, shared migration
-- 001/060) is already a member of it — t_<slug>_recon_engine included — so
-- this needs no new role, no new membership grant, and no per-tenant
-- migration. It does mean ingest/recon/support can read it too, same as
-- every other platform_ref reference table; nothing in the request asked
-- for recon_engine-only visibility, and platform reference data being
-- readable by every tenant role is the existing, unquestioned norm here.
-- =============================================================================

-- First use of btree_gist in this repo — needed below for a genuine temporal
-- non-overlap constraint. Trusted extension since PG13: tenant_migrate can
-- install it with no superuser step, same as pgcrypto (024).
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA platform_ref;

CREATE TABLE IF NOT EXISTS platform_ref.ref02_notified_rcm_category (
    category_row_id     uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),

    -- Stable business identifier, e.g. 'GTA_FREIGHT', 'SECURITY_SERVICES'.
    category_code        text NOT NULL,

    applicability_test   text NOT NULL
        CHECK (applicability_test IN (
            'always_rcm', 'entity_type_test', 'threshold_test',
            'routes_to_subunit', 'rate_test', 'registration_status_test'
        )),

    -- Category-specific governed conditions (eligible_supplier_types,
    -- rcm_triggering_entity_types, threshold_amount, trigger_rate_percent,
    -- forward_charge_rate_percent, requires_unregistered_supplier,
    -- opt_out_available, subunit_capability_id, ...). Free-form on purpose —
    -- which keys apply depends on applicability_test, and this repo is not
    -- the source of the RCM notification vocabulary the way the document
    -- registry is the source of document-type vocabulary.
    conditions_json       jsonb NOT NULL DEFAULT '{}'::jsonb,

    approval_state        text NOT NULL DEFAULT 'PENDING'
        CHECK (approval_state IN ('PENDING', 'APPROVED', 'REJECTED')),

    effective_from        date NOT NULL,
    effective_to          date,

    -- Which published edition of the REF-02 category set this row belongs
    -- to, and the real notification/source it was transcribed from — both
    -- required: an RCM category with no citation is not distinguishable
    -- from an invented one.
    reference_version     text NOT NULL,
    source_record_id      text NOT NULL,
    source_version        text NOT NULL,
    citation_reference    text,

    recorded_at           timestamptz NOT NULL DEFAULT now(),
    superseded_at         timestamptz,
    supersedes_row_id     uuid REFERENCES platform_ref.ref02_notified_rcm_category (category_row_id),
    created_by            text NOT NULL DEFAULT current_user,

    CONSTRAINT ref02_notified_rcm_category_window
        CHECK (effective_to IS NULL OR effective_to > effective_from),

    -- Contract rule 3: one category_code resolves to AT MOST ONE approved,
    -- effective row for any given date — overlapping APPROVED windows for
    -- the same category are invalid reference configuration, not merely
    -- undesirable. The per-tenant config tables (038, 039) only ever
    -- enforced "one CURRENT row per scope" via a plain unique index; this
    -- is a genuine temporal non-overlap requirement, which needs
    -- btree_gist's range-overlap operator rather than a unique index.
    CONSTRAINT ref02_notified_rcm_category_no_overlap EXCLUDE USING gist (
        category_code WITH =,
        daterange(effective_from, effective_to, '[]') WITH &&
    ) WHERE (approval_state = 'APPROVED' AND superseded_at IS NULL)
);

CREATE INDEX IF NOT EXISTS ref02_notified_rcm_category_lookup_idx
    ON platform_ref.ref02_notified_rcm_category (category_code)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE platform_ref.ref02_notified_rcm_category IS
    'Platform-wide GST reverse-charge-mechanism notified categories '
    '(2026-09-05, inafin-reconciliation-engine PRIM-07). Seeded EMPTY — no '
    'citable notification source checked into this workspace yet; same '
    'posture as hsn_master/sac_master (029).';


-- -----------------------------------------------------------------------------
-- Read contract. Full history — no "currently effective" pre-filter, same
-- principle every other reconciliation-engine contract in this repo follows:
-- resolving APPROVED + effective-for-a-date is the engine's own job
-- (contract rule 2).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW platform_ref.v1_ref02_notified_rcm_category AS
SELECT
    category_code,
    applicability_test,
    conditions_json,
    approval_state,
    effective_from,
    effective_to,
    reference_version,
    source_record_id,
    source_version,
    citation_reference,
    recorded_at,
    superseded_at
FROM platform_ref.ref02_notified_rcm_category;

COMMENT ON VIEW platform_ref.v1_ref02_notified_rcm_category IS
    'Full history, not just currently-effective rows — resolving APPROVED + '
    'effective-for-a-transaction-date is the reading engine''s own logic.';

REVOKE ALL ON platform_ref.ref02_notified_rcm_category FROM PUBLIC;
GRANT SELECT ON platform_ref.v1_ref02_notified_rcm_category TO platform_ref_reader;
