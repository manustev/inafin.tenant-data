-- =============================================================================
-- Tenant migration 013 — the read contract for "did my upload succeed".
--
-- This is the customer-facing question that motivated row-level rejection
-- (tenant 012's header) and the Bronze intake gate: a client uploaded one
-- file, and someone needs to answer accepted / partially accepted / refused,
-- and why, without reading application logs. Two things were still missing
-- to answer it from SQL alone:
--
--   1. rejected_row and quarantined_artefact had no v1_ view, so recon (and
--      any future reporting surface built on the same read contract as
--      Pipeline 2) could not reach them at all — 004's grant loop only
--      covers views already named v1_, so a base-table read would need its
--      own bespoke grant, which is exactly the per-object exception 004's
--      loop exists to avoid.
--   2. rejected_row was indexed on batch_id only. Answering "what happened
--      to bronze ingest_id X" means starting from the artefact, not the
--      batch, because a caller who only has the Bronze ingest_id (the ID
--      the upload API handed back) cannot get to a batch_id without this
--      index — before this migration that lookup was a sequential scan.
--
-- Both views are added for the same reason 004's views exist: so a future
-- base-table refactor of rejected_row or quarantined_artefact does not also
-- require changing every caller that reads today's shape.
-- =============================================================================

CREATE INDEX IF NOT EXISTS rejected_row_bronze_ingest_idx
    ON {{silver}}.rejected_row (bronze_ingest_id);

CREATE OR REPLACE VIEW {{silver}}.v1_rejected_row AS
SELECT
    id,
    batch_id,
    doc_type_code,
    bronze_ingest_id,
    source_line,
    column_name,
    message,
    rejected_at
FROM {{silver}}.rejected_row;

CREATE OR REPLACE VIEW {{silver}}.v1_quarantined_artefact AS
SELECT
    ingest_id,
    entity_id,
    document_type,
    rejected_at,
    reason,
    ingest_run_id
FROM {{silver}}.quarantined_artefact;
