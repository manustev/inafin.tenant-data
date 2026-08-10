-- =============================================================================
-- Tenant migration 014 — NULLS NOT DISTINCT on the three natural keys that
-- carry a nullable column.
--
-- TODO.md "A natural key containing a NULLable column does not enforce
-- itself", found 2026-08-07. purchase_register, creditor_ageing_report and
-- common_input_service_invoice (tenant 010) all key on `supplier_gstin`,
-- which is nullable because an unregistered supplier is legitimate. A
-- partial unique index treats two NULLs as DISTINCT, so
-- `WHERE superseded_at IS NULL` alone does not stop two concurrent loads of
-- the same unregistered-supplier invoice both inserting live rows —
-- `RegisterLoader._upsert_natural` already matches with
-- `IS NOT DISTINCT FROM` (src/silver/registers/loader.py), so the LOOKUP is
-- correct; only the INDEX was not enforcing what its column list implies.
--
-- Cheap now, done here, per TODO.md: no production data exists (all six
-- deploy blockers in TODO.md are open), so this is a straight index rebuild
-- with no duplicate sweep required. After real data, the same change would
-- need a duplicate check first — record that here so a later session does
-- not copy this migration's shape unmodified once that stops being true.
--
-- Migrations are checksum-pinned, so 010's indexes are dropped and recreated
-- here rather than edited in place.
-- =============================================================================

DROP INDEX IF EXISTS {{silver}}.purchase_register_natural_key_uq;
CREATE UNIQUE INDEX purchase_register_natural_key_uq
    ON {{silver}}.purchase_register (entity_id, gstin, supplier_gstin, invoice_no)
    NULLS NOT DISTINCT
    WHERE superseded_at IS NULL;

DROP INDEX IF EXISTS {{silver}}.creditor_ageing_report_natural_key_uq;
CREATE UNIQUE INDEX creditor_ageing_report_natural_key_uq
    ON {{silver}}.creditor_ageing_report (entity_id, gstin, as_at_date, supplier_gstin, invoice_no)
    NULLS NOT DISTINCT
    WHERE superseded_at IS NULL;

DROP INDEX IF EXISTS {{silver}}.common_input_service_invoice_natural_key_uq;
CREATE UNIQUE INDEX common_input_service_invoice_natural_key_uq
    ON {{silver}}.common_input_service_invoice (entity_id, gstin, supplier_gstin, invoice_no)
    NULLS NOT DISTINCT
    WHERE superseded_at IS NULL;
