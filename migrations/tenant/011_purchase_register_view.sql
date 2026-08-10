-- =============================================================================
-- Tenant migration 011 — retire the archetype-1 write path for PURCHASE_REGISTER.
--
-- TYPED-TABLES-PLAN.md 10 step 4, the last item on that build order.
--
-- THE WRITE-PATH GUARD IS APPLICATION-LEVEL, NOT A DATABASE CONSTRAINT — worth
-- recording, because a composite-FK guard was tried first and does not work
-- here. transaction_document's FK is (doc_type, doc_archetype) REFERENCES
-- document_type (doc_type_code, archetype) (tenant 007), so the obvious move
-- was flipping archetype away from 1 for this row. shared migration 003's
-- document_type_scope_ck forbids that: archetype IS NOT NULL is REQUIRED
-- whenever in_scope, so archetype is permanent classification data, not a
-- write-path switch, for every in-scope type. The guard is instead an explicit
-- check in src/silver/promote.py (`_RETIRED_ARCHETYPE_1_TYPES`), scoped to
-- PURCHASE_REGISTER only — the other 10 archetype-1 types are untouched.
--
-- WHAT THIS DOES NOT DO. It does not touch transaction_document or
-- transaction_line. No production data exists (all six TODO.md deploy
-- blockers are open) so there is nothing to backfill or migrate off of, unlike
-- migration 007's backfill from purchase_invoice.
--
-- THE SHAPE PROBLEM, not a rename. purchase_register (tenant 010) is
-- invoice-level per the client's own reference schema
-- (reference/inafin_a1_schema.sql: "Purchase register (inward supply,
-- invoice-level)", A1.03) — one row per invoice, one tax line. It has no
-- line_number, quantity, unit_price or description, because the source export
-- never carried them; v1_purchase_invoice_line has nothing to be defined over
-- and is dropped rather than faked. This is a BREAKING CHANGE to the v2
-- contract and needs v2 sign-off (TODO.md already lists v2 integration as
-- "not mine to decide"). v1_purchase_invoice survives because it needed no
-- aggregation: purchase_register is already invoice-grain, so the mapping is
-- 1:1, not a GROUP BY. Two columns are fabricated because the source has
-- nothing for them — `currency` defaults to INR (every A1 type is a domestic
-- GST filing) and `payment_due_date` is NULL always (never captured). Both are
-- called out here so nobody mistakes them for real data later.
-- =============================================================================

-- No successor. See the header above for why this is a drop, not a rename.
DROP VIEW IF EXISTS {{silver}}.v1_purchase_invoice_line;

-- Column list AND TYPES are BYTE-FOR-BYTE what migration 004/007 published.
-- CREATE OR REPLACE VIEW enforces both itself — a changed list or a changed
-- type is refused, which is the alarm that the v2 contract was about to break
-- by accident. It caught exactly that on the first attempt at this migration:
-- purchase_register.id is bigint (an identity PK, tenant 010), but invoice_id
-- was uuid (transaction_document.doc_id). Silently exposing a bigint instead
-- would break any v2 client that parses invoice_id as a UUID, so it is
-- deterministically derived instead of re-typed — same id always produces the
-- same invoice_id, but it is a synthetic surrogate, not a real UUID an ERP
-- generated, and does not correlate to any Bronze artefact on its own.
CREATE OR REPLACE VIEW {{silver}}.v1_purchase_invoice AS
SELECT
    md5('purchase_register:' || id::text)::uuid    AS invoice_id,
    batch_id,
    entity_id,
    invoice_no                                     AS invoice_number,
    supplier_gstin::text                           AS supplier_gstin,
    invoice_date,
    taxable_value::numeric(18,2)                   AS total_taxable_value,
    (cgst + sgst + igst + cess)::numeric(18,2)     AS total_tax_value,
    (taxable_value + cgst + sgst + igst + cess)::numeric(18,2) AS total_value,
    NULL::date                                     AS payment_due_date,
    'INR'::text                                    AS currency,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    bronze_ingest_id
FROM {{silver}}.purchase_register;
