-- =============================================================================
-- Tenant migration 030 — `transaction_document` gains a supersede pointer.
--
-- THE BUG, found by the ERP upload E2E suite (2026-09-01) against schema
-- release `v4`: re-triggering `GTA_INVOICE_CONSIGNMENT_NOTE`'s reference PDF
-- a second time returned 409 CONFLICT on `transaction_document_natural_key_uq`
-- (tenant migration 007). Not a fixture problem — `src/silver/promote.py`'s
-- `promote_transaction_documents` has ALWAYS done a blind INSERT for every
-- archetype-1 type, CSV or PDF, with no lookup against the natural key at
-- all. The five archetype-3/4/6/7/8 services had exactly this gap until
-- shared migration 041/042 and `src/silver/supersede.py` closed it earlier
-- this session; archetype 1 never got the same fix because the PDF half of
-- it (`BILL_OF_ENTRY`, `GTA_INVOICE_CONSIGNMENT_NOTE`) was UNREACHABLE until
-- shared migration 043 corrected the catalogue's routing — nobody could
-- re-trigger a real PDF specimen through this path before that fix existed,
-- so nothing had ever exercised a second ingest of the same document here.
--
-- UNLIKE the five archetype tables, `transaction_document` (tenant 007) never
-- got a `supersedes_*` pointer in the first place — its `superseded_at`
-- column has always been there, but nothing to record WHICH row a correction
-- replaced. This migration adds that column; `src/silver/promote.py` is
-- fixed in the same commit to find-and-close the current row (keyed on
-- exactly `transaction_document_natural_key_uq`'s columns — entity_id,
-- doc_type, COALESCE(counterparty_gstin, ''), doc_number — per
-- `src/silver/supersede.py`'s rule) before inserting the correction.
--
-- Both the CSV path (ARCHETYPE1_PROMOTE — five registry types) and the PDF
-- path (PDF_EXTRACTION's `TransactionDocumentExtractor` subclasses) call the
-- SAME `promote_transaction_documents`, so this fixes both at once — the
-- CSV path had the identical latent bug, just never observed because nothing
-- in this repo's test fixtures re-uploads the SAME invoice number twice.
--
-- WHY NOT IN v1_purchase_invoice/v1_purchase_invoice_line: those two views
-- are the FROZEN downstream contract tenant migration 007's header names
-- explicitly ("keep their exact column lists ... so inafinplatform/v2 sees
-- nothing") — a compatibility promise unrelated to this fix, so the new
-- column is not added there. `v1_transaction_document` IS the generalised,
-- unfrozen contract ("v2 migrates to these at its own pace"), so it gains
-- the column the same way `v1_entitlement_instrument` already exposes
-- `supersedes_instrument_id`.
-- =============================================================================

ALTER TABLE {{silver}}.transaction_document
    ADD COLUMN supersedes_doc_id uuid
        REFERENCES {{silver}}.transaction_document (doc_id);

-- `supersedes_doc_id` is appended at the END of the SELECT list, not
-- alongside `superseded_at` where it reads more naturally — `CREATE OR
-- REPLACE VIEW` only allows adding columns after every existing one; putting
-- it earlier reads to Postgres as RENAMING the column already in that
-- position (`bronze_ingest_id`, here), which is a different, destructive
-- operation `ALTER VIEW ... RENAME COLUMN` would be needed for, not what
-- this migration means to do.
CREATE OR REPLACE VIEW {{silver}}.v1_transaction_document AS
SELECT
    doc_id,
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
    bronze_ingest_id,
    supersedes_doc_id
FROM {{silver}}.transaction_document;
