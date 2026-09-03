-- =============================================================================
-- Shared migration 048 — SHAREHOLDING_PATTERN's `as_of_date` label was wrong.
--
-- THE BUG, found re-investigating a follow-up ERP upload E2E report
-- (2026-09-02) that named eight PDF_EXTRACTION types as quarantining. Seven
-- of the eight are confirmed, already-decided tier-1 limitations (see
-- CLAUDE.md backlog item 8 and this migration's siblings below) — but
-- SHAREHOLDING_PATTERN's is a genuine mislabeling, not a limitation.
-- `extraction_spec` declared `as_of_date:date!:"As On Date"`, and no line in
-- the real specimen (`reference/A1-A7Documents/A2.03_Shareholding_
-- Pattern.pdf`) ever reads "As On Date: ..." — that label does not exist in
-- the document. The actual fact is on the SAME line as `reference_number`'s
-- already-correct label:
--
--     Financial Year: 2024–25 (as on 31 March 2025)
--
-- Both fields legitimately read the same label: `reference_number` (type
-- `text`) takes the line's value whole ("2024–25 (as on 31 March 2025)"),
-- and `as_of_date` (type `date`) searches within that same raw value for a
-- date-shaped substring — `labelvalue.py`'s `_parse_date` already does a
-- `.search()`, not a full-string match, precisely for lines like a validity
-- window's trailing prose, so re-pointing `as_of_date` at "Financial Year"
-- needs no grammar change, only the correct label. Confirmed against the
-- real specimen: this now extracts `Extracted`, not `Partial`.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Financial Year",as_of_date:date!:"Financial Year"'
 WHERE doc_type_code = 'SHAREHOLDING_PATTERN';
