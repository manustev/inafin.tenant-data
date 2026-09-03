-- =============================================================================
-- Shared migration 056 — correction: GSTIN_REGISTER's `as_of_date` reverts
-- to required.
--
-- Migration 053 (this same session) dropped it to optional so the table
-- content wouldn't be blocked by an unrelated header gap — but
-- `entity_master_record.as_of_date` is `NOT NULL` and part of the row's
-- own bitemporal unique key (tenant migration 017), not a display field:
-- `EntityMasterExtractor.to_silver` (`src/extraction/base.py`) reads
-- `fields["as_of_date"]` unconditionally to build that key, so an
-- `Extracted` outcome with no `as_of_date` is not writable at all — it
-- raised `KeyError`, caught by this session's own test suite before this
-- reached anywhere real. Fabricating a stand-in date would be exactly the
-- kind of guess CLAUDE.md rejects for a genuinely unstated fact, so this
-- reverts to the pre-053 state: `GSTIN_REGISTER` stays a named
-- `Partial(missing=("as_of_date",))`, unchanged from before this session.
--
-- The `table_pattern`/`table_columns` clause added by 053 is KEPT — it is
-- correct, unit-tested (`tests/conformance/test_table_extraction.py`), and
-- ready the moment a future session resolves the `as_of_date` gap (a
-- genuine, still-open product decision — see TODO.md/SESSION-HISTORY.md),
-- without needing to re-author the table grammar.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Total GSTINs under PAN",as_of_date:date!:"Register Date";table_pattern="(?P<gstin>[0-9]{2}\s*[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\s*[1-9A-Z]\s*Z\s*[0-9A-Z])\s+(?P<state>.+?)\s+(?P<registration_type>Regular|Composition|Casual Taxable Person|Non[- ]Resident Taxable Person|SEZ Unit|SEZ Developer|Input Service Distributor|TDS Deductor|TCS Collector|UIN Holder)\s+(?P<effective_date>[0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})\s+(?P<status>Active|Suspended\s*\(\s*[0-9]{1,2}-[A-Za-z]{3}-\s*[0-9]{4}\s*\)|Cancelled\s*\(\s*[0-9]{1,2}-[A-Za-z]{3}-\s*[0-9]{4}\s*\))\s+(?P<principal_place>(?:(?![0-9]{2}\s*[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\s*[1-9A-Z]\s*Z\s*[0-9A-Z]).)+)";table_columns=gstin:gstin,state:text,registration_type:text,effective_date:date,status:text,principal_place:text'
 WHERE doc_type_code = 'GSTIN_REGISTER';
