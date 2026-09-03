-- =============================================================================
-- Tenant migration 037 — v1_rcm_registration_history: effective_to fix.
--
-- Correction reported by inafin-reconciliation-engine, 2026-09-03. Tenant
-- migration 036's first cut derived effective_to from superseded_at — a
-- SYSTEM/AUDIT timestamp (when tenant-data recorded a correction), not a
-- business-validity end date. Concretely wrong on a late correction: a
-- registration cancelled 31-Jul but corrected into Silver on 15-Sep would
-- have shown effective_to = 15-Sep, so an invoice dated 20-Aug would read as
-- "registration still valid" when it legally was not.
--
-- The real business end date was always present, just unstructured: GSTIN_
-- REGISTER's table_pattern (registry/document_types.csv, A2.07) already
-- captures a cancelled/suspended GSTIN's status as e.g.
-- 'Cancelled (31-Jul-2025)' or 'Suspended (28-Feb-2025)' — the date is the
-- SOURCE DOCUMENT's own stated legal end date, not something derived from
-- when we happened to ingest it. This migration parses that date out of
-- gstin_register_entry.status at the view level (no base-table or extractor
-- change — the fact was always there, just not split into its own column)
-- rather than inventing a value with no source, per this repo's standing
-- rule against manufacturing facts.
--
-- registration_status is cleaned to the bare state word (Active/Suspended/
-- Cancelled) as a consequence — exposing 'Suspended (28-Feb-2025)' as a
-- "status" while ALSO exposing effective_to = 2025-02-28 would restate the
-- same fact twice, once correctly structured and once not.
--
-- recipient_is_business_entity stays NULL — unchanged, still no source
-- states it, still not derived from registration_type by guesswork.
-- =============================================================================

CREATE OR REPLACE VIEW {{silver}}.v1_rcm_registration_history AS
SELECT
    entity_id,
    gstin,
    registration_type,
    CASE
        WHEN status ~ '^(Cancelled|Suspended)' THEN split_part(status, ' (', 1)
        ELSE status
    END                            AS registration_status,
    CAST(NULL AS boolean)         AS recipient_is_business_entity,
    effective_date                 AS effective_from,
    CASE
        WHEN status ~ '^(Cancelled|Suspended)' THEN
            to_date(
                regexp_replace(substring(status FROM '\(([^)]*)\)'), '\s+', '', 'g'),
                'DD-Mon-YYYY'
            )
        ELSE NULL
    END                             AS effective_to,
    recorded_at,
    bronze_ingest_id               AS source_reference
FROM {{silver}}.gstin_register_entry;

COMMENT ON VIEW {{silver}}.v1_rcm_registration_history IS
    'TD-RCM-005. effective_to is the source document''s own stated legal end '
    'date (parsed from status, e.g. "Cancelled (31-Jul-2025)"), NOT '
    'superseded_at — fixed 2026-09-03 per inafin-reconciliation-engine '
    'feedback. NULL means the source states no end date (open registration). '
    'recipient_is_business_entity is NULL — no source states it.';
