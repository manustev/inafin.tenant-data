-- =============================================================================
-- Shared migration 059 — GSTIN_REGISTER's published `source_label` catches
-- up to migration 058's `extraction_spec` fix.
--
-- Same shape as shared migration 049 (SHAREHOLDING_PATTERN): 058 corrected
-- `document_type.extraction_spec` but not `document_type_field.source_label`
-- — the schema catalogue's own copy of that label, populated from the same
-- cell by `document_schema.py::_from_extraction_spec` at seed time
-- (migration 036). Left alone, the published catalogue would still tell a
-- client `as_of_date` binds to "Register Date" — a label that appears
-- nowhere in the specimen — while the extractor itself now correctly reads
-- "Status (as on". `test_schema_catalogue.py::test_the_catalogue_matches_
-- the_derivation` is what caught this drift.
-- =============================================================================

UPDATE platform_ref.document_type_field SET source_label = 'Status (as on'
 WHERE doc_type_code = 'GSTIN_REGISTER' AND field_name = 'as_of_date';
