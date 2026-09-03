-- =============================================================================
-- Shared migration 051 — SHAREHOLDING_PATTERN's `extraction_spec` gains a
-- table_pattern/table_columns clause, so its shareholder table is captured,
-- not discarded.
--
-- See tenant migration 032 (`shareholding_pattern_holder`) for the storage
-- side and `src/extraction/tablevalue.py`/`entity_master_types.py` for the
-- parsing/write side; this migration is only the registry data change.
--
-- Named groups (`holder_name`, `shares`, `pct_current_fy`, `pct_prior_fy`,
-- `pledged`) match `ShareholdingPatternExtractor._extra_silver_write`'s
-- column names exactly — a mismatch here is a `SpecError` at registry-build
-- time, not a silent drop, so this migration and that Python cannot drift
-- without a startup failure surfacing it.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Financial Year",as_of_date:date!:"Financial Year";table_pattern="(?P<holder_name>.+?)\s+(?P<shares>[\d,]+)\s+(?P<pct_current_fy>[\d.]+)%\s+(?P<pct_prior_fy>[\d.]+)%\s+(?P<pledged>Yes|No)";table_columns=holder_name:text,shares:money,pct_current_fy:money,pct_prior_fy:money,pledged:text'
 WHERE doc_type_code = 'SHAREHOLDING_PATTERN';
