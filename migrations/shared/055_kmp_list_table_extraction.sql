-- =============================================================================
-- Shared migration 055 — KMP_LIST's `extraction_spec` gains a
-- table_pattern/table_columns clause, so its KMP list is captured, not
-- discarded, and `as_of_date` becomes optional.
--
-- `designation` is Section 203 of the Companies Act, 2013's own closed KMP
-- role vocabulary (Managing Director, Whole-time Director, CEO, CFO,
-- Company Secretary, Manager — the roles the statute itself enumerates as
-- "key managerial personnel"), plus the "&"-combination and parenthetical
-- qualifier shapes the real specimen shows ("Managing Director & CEO",
-- "Whole-time Director (Operations)"). This is what makes `name` (free
-- text, immediately followed by `designation` with NO anchor between them —
-- unlike DIRECTOR_LIST_WITH_DIN, whose `din` sits between the two)
-- unambiguous: without a closed designation vocabulary, "Arvind Rao
-- Deshmukh Managing Director" has no single correct name/designation split
-- from shape alone. Verified against the real specimen: all 4 rows split
-- correctly, including the CFO row whose `din` is literally "N/A (not a
-- director)" rather than 8 digits.
--
-- as_of_date DROPPED TO OPTIONAL, same reasoning as GSTIN_REGISTER (shared
-- migration 053): the real specimen states only a reporting PERIOD
-- ("Annual Report FY 2024-25"), a fiscal-year-end convention, not a stated
-- date this grammar can parse as `date` without guessing which day of that
-- year it means. Left unbound rather than fabricated; the table content
-- below does not depend on it.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Extracted from",as_of_date:date:"List Date";table_pattern="(?P<name>(?:\S+\s+){0,5}?\S+)\s+(?P<designation>(?:Managing Director|Whole-time Director|Independent Director|Non-Executive Director|Nominee Director|Additional Director|Director|Company Secretary|Chief Executive Officer|CEO|Chief Financial Officer|CFO|Manager)(?:\s*&\s*(?:Managing Director|Whole-time Director|Independent Director|Non-Executive Director|Nominee Director|Additional Director|Director|Company Secretary|Chief Executive Officer|CEO|Chief Financial Officer|CFO|Manager))?(?:\s*\([A-Za-z ]+\))?)\s+(?P<din>\d{8}|N/A\s*\(not a director\))\s+(?P<membership>—|ACS\s*\d+|PAN\s*[A-Z0-9]+)";table_columns=name:text,designation:text,din:text,membership:text'
 WHERE doc_type_code = 'KMP_LIST';
