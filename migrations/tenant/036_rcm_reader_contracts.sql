-- =============================================================================
-- Tenant migration 036 — RCM reader contracts for inafin-reconciliation-engine
-- (docs/adr/0001, docs/adr/0002). Four of the seven TD-RCM-* views from
-- inafin-tenant-data-rcm-contract-request.md that are buildable today without
-- new extraction work. TD-RCM-003 (related-party) and TD-RCM-006 (foreign
-- payment enrichment) are blocked — see docs/review/. TD-RCM-007 is
-- optional/deferred by the request doc itself.
--
-- Every view here is additive: it projects an existing, already-verified
-- table under a new name/shape. No existing v1_ view's column meaning
-- changes (invariant 5). Definer-rights, common core only — same discipline
-- as every other v1_ view in this repo.
--
-- Granting: apply_tenant_grants (shared migration 060) already grants
-- t_<slug>_recon_engine SELECT on any view named v1_rcm_%, so nothing here
-- needs a matching grant-migration — the next `make migrate` picks these up
-- automatically for every tenant.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- TD-RCM-001 — payroll/TDS evidence. Straight rename-projection of the
-- existing payroll_tds_register (tenant migration 020) — no new facts, no
-- schema change to the base table.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{silver}}.v1_rcm_payroll_tds_evidence AS
SELECT
    id                AS record_id,
    entity_id,
    gstin,
    person_name,
    role_title,
    classification    AS employment_or_payment_classification,
    tds_section,
    gross_amount,
    tds_deducted,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id,
    row_hash
FROM {{silver}}.payroll_tds_register;

COMMENT ON VIEW {{silver}}.v1_rcm_payroll_tds_evidence IS
    'TD-RCM-001. Evidence, not a legal conclusion that a director is an '
    'employee — see inafin-tenant-data-rcm-contract-request.md. Projects '
    'payroll_tds_register unchanged; v1_payroll_tds_register is untouched.';


-- -----------------------------------------------------------------------------
-- TD-RCM-002 — director evidence (docs/adr/0002: client-supplied director
-- list only, FORM_DIR_12/Forensic Mode deferred — see
-- docs/review/sme-question-dir12-director-evidence.md).
--
-- IMPORTANT LIMITATION, stated here rather than silently overpromised: there
-- is no key linking one director_list_din row to a specific board-resolution
-- or service-agreement narrative_contract row — narrative_contract
-- (archetype 8) is an evidence LOCATOR keyed only by entity_id, with no
-- per-director field in its unstructured key_terms. board_resolution_
-- reference / service_agreement_reference below therefore point at the most
-- recent non-superseded contract of each type FOR THE ENTITY, not a
-- director-specific match. employment_status_basis records only whether that
-- entity-level evidence exists, not that it names this particular director.
--
-- gstin is NULL: directorship is a PAN/entity-level fact in this data model,
-- not a GSTIN-level one (entity_master_record and narrative_contract both
-- key on entity_id only) — an entity with multiple GSTINs has no single
-- correct GSTIN to attach here, so none is fabricated (request doc's own
-- "preserve unknown, never manufacture" rule).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{silver}}.v1_rcm_director_evidence AS
SELECT
    d.fact_id                       AS director_evidence_id,
    d.entity_id,
    CAST(NULL AS platform_ref.gstin) AS gstin,
    d.din,
    d.name                           AS director_name,
    d.appointment_date,
    d.cessation_date,
    d.currently_active               AS employment_status,
    'CLIENT_DIRECTOR_LIST'::text     AS employment_status_basis,
    board.contract_id                AS board_resolution_reference,
    service.contract_id              AS service_agreement_reference,
    CASE
        WHEN board.contract_id IS NOT NULL AND service.contract_id IS NOT NULL
            THEN 'BOARD_RESOLUTION_AND_SERVICE_AGREEMENT_ON_FILE'
        WHEN board.contract_id IS NOT NULL THEN 'BOARD_RESOLUTION_ON_FILE'
        WHEN service.contract_id IS NOT NULL THEN 'SERVICE_AGREEMENT_ON_FILE'
        ELSE 'CLIENT_LIST_ONLY'
    END                              AS evidence_status,
    d.appointment_date               AS valid_from,
    NULL::date                       AS valid_to,
    d.recorded_at,
    d.superseded_at,
    d.batch_id,
    d.bronze_ingest_id,
    ARRAY[d.bronze_ingest_id]
        || COALESCE(ARRAY[board.bronze_ingest_id], ARRAY[]::uuid[])
        || COALESCE(ARRAY[service.bronze_ingest_id], ARRAY[]::uuid[])
                                     AS source_record_references
FROM {{silver}}.director_list_din d
LEFT JOIN LATERAL (
    SELECT nc.contract_id, nc.bronze_ingest_id
    FROM {{silver}}.narrative_contract nc
    WHERE nc.entity_id = d.entity_id
      AND nc.contract_type = 'BOARD_RESOLUTION_DIRECTOR_APPOINTMENT'
      AND nc.superseded_at IS NULL
    ORDER BY nc.recorded_at DESC
    LIMIT 1
) board ON true
LEFT JOIN LATERAL (
    SELECT nc.contract_id, nc.bronze_ingest_id
    FROM {{silver}}.narrative_contract nc
    WHERE nc.entity_id = d.entity_id
      AND nc.contract_type = 'DIRECTOR_SERVICE_AGREEMENT'
      AND nc.superseded_at IS NULL
    ORDER BY nc.recorded_at DESC
    LIMIT 1
) service ON true;

COMMENT ON VIEW {{silver}}.v1_rcm_director_evidence IS
    'TD-RCM-002, Phase 1 grain per docs/adr/0002 — client-supplied director '
    'list + entity-level (not director-specific) board-resolution/service- '
    'agreement evidence locators. FORM_DIR_12 not built; SME question open '
    'in docs/review/sme-question-dir12-director-evidence.md.';


-- -----------------------------------------------------------------------------
-- TD-RCM-004 — purchase candidate enrichment. Additive projection of
-- purchase_register (tenant migration 010) — does not touch
-- v1_purchase_register.
--
-- supplier_name / description_or_narration are NULL: purchase_register never
-- captured either (confirmed against its RegisterSpec, src/silver/registers/
-- catalog.py — not a lookup miss, the columns do not exist). Exposed as NULL
-- rather than omitted, per the request doc's own instruction to "identify
-- whether it comes from the source or is unavailable" — a real gap, not
-- invented data. Adding real columns needs a register/RegisterSpec change
-- plus a real specimen showing the client's export actually carries them.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{silver}}.v1_rcm_purchase_candidate AS
SELECT
    id                       AS purchase_record_id,
    entity_id,
    gstin,
    supplier_gstin,
    CAST(NULL AS text)       AS supplier_name,
    invoice_no,
    invoice_date,
    tax_period,
    hsn_sac,
    CAST(NULL AS text)       AS description_or_narration,
    taxable_value,
    gst_rate,
    cgst,
    sgst,
    igst,
    cess,
    gl_code,
    cost_centre,
    rcm_flag                 AS source_rcm_flag,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id,
    row_hash
FROM {{silver}}.purchase_register;

COMMENT ON VIEW {{silver}}.v1_rcm_purchase_candidate IS
    'TD-RCM-004. supplier_name/description_or_narration are NULL — not '
    'captured by purchase_register today (ADR-004 narration gap, request '
    'doc). Projects purchase_register unchanged; v1_purchase_register '
    'untouched.';


-- -----------------------------------------------------------------------------
-- TD-RCM-005 — registration history. Built off gstin_register_entry (tenant
-- migration 033), NOT platform_ref.gst_registration — that onboarding table
-- is current-state-only (API-CONTRACT.md) and could not serve history
-- without inventing it; gstin_register_entry already carries real,
-- per-tenant, supersede-tracked history for exactly this fact.
--
-- effective_to is derived, not stored: gstin_register_entry has no validity
-- window of its own (tenant migration 033's own header explains why — a
-- registration is a single current fact, not a %-holding-style period). A
-- superseded row's effective_to is the timestamp it was superseded; the
-- current row's is open (NULL).
--
-- recipient_is_business_entity is NULL: no source in this data model states
-- it, and none of gstin_register_entry's registration_type values map to it
-- without a judgement call this repo should not make unasked. Flagged, not
-- guessed.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{silver}}.v1_rcm_registration_history AS
SELECT
    entity_id,
    gstin,
    registration_type,
    status                        AS registration_status,
    CAST(NULL AS boolean)         AS recipient_is_business_entity,
    effective_date                AS effective_from,
    superseded_at::date           AS effective_to,
    recorded_at,
    bronze_ingest_id              AS source_reference
FROM {{silver}}.gstin_register_entry;

COMMENT ON VIEW {{silver}}.v1_rcm_registration_history IS
    'TD-RCM-005, sourced from gstin_register_entry (real per-tenant history) '
    'rather than platform_ref.gst_registration (current-state-only). '
    'recipient_is_business_entity is NULL — no source states it.';
