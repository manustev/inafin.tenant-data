-- =============================================================================
-- Shared migration 050 — DIRECTOR_LIST_WITH_DIN's `as_of_date` label was
-- wrong, same class of bug as shared migration 048/049.
--
-- `extraction_spec` declared `as_of_date:date!:"List Date"`, and no line in
-- the real specimen (`reference/A1-A7Documents/A3.01_List_of_Directors_
-- with_DIN.pdf`) reads "List Date: ...". The actual fact is the trailing
-- date in the document's provenance line:
--
--     Source: MCA21 portal (Form DIR-12 filings) and internal Board
--     minutes register, cross-verified as at 31 March 2025.
--
-- Re-pointing `as_of_date` at "Source" needs no grammar change:
-- `labelvalue.py`'s `_parse_date` already `.search()`es the raw value rather
-- than requiring a full-string match, so it finds "31 March 2025" inside the
-- trailing prose. Confirmed against the real specimen: this now extracts
-- `Extracted`, not `Partial`.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Financial Years Covered",as_of_date:date!:"Source"'
 WHERE doc_type_code = 'DIRECTOR_LIST_WITH_DIN';

UPDATE platform_ref.document_type_field SET source_label = 'Source'
 WHERE doc_type_code = 'DIRECTOR_LIST_WITH_DIN' AND field_name = 'as_of_date';
