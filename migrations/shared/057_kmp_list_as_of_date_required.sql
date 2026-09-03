-- =============================================================================
-- Shared migration 057 — correction: KMP_LIST's `as_of_date` reverts to
-- required. Same reasoning as shared migration 056 (GSTIN_REGISTER) — see
-- that migration's header. `table_pattern`/`table_columns` (from migration
-- 055) are kept unchanged.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Extracted from",as_of_date:date!:"List Date";table_pattern="(?P<name>(?:\S+\s+){0,5}?\S+)\s+(?P<designation>(?:Managing Director|Whole-time Director|Independent Director|Non-Executive Director|Nominee Director|Additional Director|Director|Company Secretary|Chief Executive Officer|CEO|Chief Financial Officer|CFO|Manager)(?:\s*&\s*(?:Managing Director|Whole-time Director|Independent Director|Non-Executive Director|Nominee Director|Additional Director|Director|Company Secretary|Chief Executive Officer|CEO|Chief Financial Officer|CFO|Manager))?(?:\s*\([A-Za-z ]+\))?)\s+(?P<din>\d{8}|N/A\s*\(not a director\))\s+(?P<membership>—|ACS\s*\d+|PAN\s*[A-Z0-9]+)";table_columns=name:text,designation:text,din:text,membership:text'
 WHERE doc_type_code = 'KMP_LIST';
