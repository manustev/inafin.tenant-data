-- =============================================================================
-- Shared migration 049 — SHAREHOLDING_PATTERN's published `source_label`
-- catches up to migration 048's `extraction_spec` fix.
--
-- Migration 048 corrected `document_type.extraction_spec` (the thing the
-- extractor actually reads) but not `document_type_field.source_label` (the
-- schema catalogue's own COPY of that label, populated from the same cell by
-- `document_schema.py::_from_extraction_spec` at seed time — migration 036).
-- Left alone, the published catalogue would tell a client `as_of_date`
-- binds to "As On Date" — a label that appears nowhere in the specimen —
-- while the extractor itself now correctly reads "Financial Year". Applied
-- as its own migration, not folded into 048, because 048 was already applied
-- and checksum-pinned by the time this was found.
-- =============================================================================

UPDATE platform_ref.document_type_field SET source_label = 'Financial Year'
 WHERE doc_type_code = 'SHAREHOLDING_PATTERN' AND field_name = 'as_of_date';
