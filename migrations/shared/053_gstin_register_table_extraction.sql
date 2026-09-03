-- =============================================================================
-- Shared migration 053 — GSTIN_REGISTER's `extraction_spec` gains a
-- table_pattern/table_columns clause, so its GSTIN list is captured, not
-- discarded, and `as_of_date` becomes optional.
--
-- TABLE. Each row's `gstin` group tolerates internal whitespace
-- (`\s*` between every fixed-format segment) because the real specimen
-- wraps a GSTIN mid-code across a line break ("27AABCM4521F1" / "Z5") —
-- `coerce_field_value`'s `gstin` type strips whitespace before validating,
-- so the captured raw text does not need to be contiguous.
-- `registration_type` is a closed GST-law vocabulary (Regular, Composition,
-- Casual Taxable Person, ...), not an invented one — declaring it as a fixed
-- alternation (rather than free text) is what lets `state` (itself free
-- text, and routinely two words — "Tamil Nadu") stop in the right place
-- without an anchor of its own; the same reasoning already governs a CHECK
-- vocabulary column per CLAUDE.md. `principal_place`'s trailing
-- `(?:(?!GSTIN-token).)+` stops it from swallowing the NEXT row's GSTIN
-- when this row's own value continues onto a further wrapped line.
--
-- as_of_date DROPPED TO OPTIONAL. The real specimen states no label:value
-- "Register Date" line at all — its only date-shaped text sits inside a
-- table COLUMN HEADER ("Status (as on 31-Mar-2025)"), which is not a fact
-- this grammar binds. Leaving `as_of_date` required would permanently
-- quarantine this type (Partial, never Extracted) and the table content
-- below — which does not depend on `as_of_date` — would never be written.
-- This is the same "genuine gap, not silently guessed" judgment already
-- applied elsewhere (entity_master_types.py's module docstring); it stays
-- unbound, not fabricated, while the rest of the document is still usable.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Total GSTINs under PAN",as_of_date:date:"Register Date";table_pattern="(?P<gstin>[0-9]{2}\s*[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\s*[1-9A-Z]\s*Z\s*[0-9A-Z])\s+(?P<state>.+?)\s+(?P<registration_type>Regular|Composition|Casual Taxable Person|Non[- ]Resident Taxable Person|SEZ Unit|SEZ Developer|Input Service Distributor|TDS Deductor|TCS Collector|UIN Holder)\s+(?P<effective_date>[0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})\s+(?P<status>Active|Suspended\s*\(\s*[0-9]{1,2}-[A-Za-z]{3}-\s*[0-9]{4}\s*\)|Cancelled\s*\(\s*[0-9]{1,2}-[A-Za-z]{3}-\s*[0-9]{4}\s*\))\s+(?P<principal_place>(?:(?![0-9]{2}\s*[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\s*[1-9A-Z]\s*Z\s*[0-9A-Z]).)+)";table_columns=gstin:gstin,state:text,registration_type:text,effective_date:date,status:text,principal_place:text'
 WHERE doc_type_code = 'GSTIN_REGISTER';
