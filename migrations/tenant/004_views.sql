-- =============================================================================
-- Tenant migration 004 — the v1_ read contract (ARCHITECTURE.md 2.4).
--
-- Pipeline 2 NEVER touches a Silver base table. It is granted SELECT on these
-- views and on nothing else in the Silver schema, so Pipeline 1 can refactor
-- base tables freely — the view is the contract.
--
-- A breaking change publishes v2_ alongside v1_, both live, migrates the
-- consumer, then retires v1_. Never edit a v1_ view's column meaning in place.
--
-- IMPORTANT — these views are DEFINER-rights (Postgres default). Do NOT add
-- WITH (security_invoker = true).
--
-- Definer rights are the mechanism that makes the read contract work. The view
-- executes as its owner (tenant_migrate), which does hold SELECT on the base
-- tables, so granting recon SELECT on the VIEW is sufficient — recon needs, and
-- has, no privilege on the base table at all. That is precisely the separation
-- ARCHITECTURE.md 5.1 requires.
--
-- With security_invoker = true the view would execute as recon, recon would
-- need SELECT on the base tables, and the v1_ contract would collapse into
-- "recon can read all of Silver". security_invoker was load-bearing in the
-- earlier shared-schema + RLS design, where a definer-rights view would have
-- bypassed the base table's row policy. Under schema-per-tenant the polarity
-- is reversed: it is the definer rights that enforce the boundary.
-- =============================================================================

CREATE OR REPLACE VIEW {{silver}}.v1_ingest_batch AS
SELECT
    batch_id,
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
FROM {{silver}}.ingest_batch;


CREATE OR REPLACE VIEW {{silver}}.v1_purchase_invoice AS
SELECT
    invoice_id,
    batch_id,
    entity_id,
    invoice_number,
    supplier_gstin,
    invoice_date,
    total_taxable_value,
    total_tax_value,
    total_value,
    payment_due_date,
    currency,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    bronze_ingest_id
FROM {{silver}}.purchase_invoice;


CREATE OR REPLACE VIEW {{silver}}.v1_purchase_invoice_line AS
SELECT
    l.line_id,
    l.invoice_id,
    i.batch_id,
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
FROM {{silver}}.purchase_invoice_line l
JOIN {{silver}}.purchase_invoice i ON i.invoice_id = l.invoice_id;
