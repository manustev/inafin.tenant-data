-- =============================================================================
-- Shared migration 058 — GSTIN_REGISTER's `as_of_date` was mislabeled, not
-- a genuine tier-1 gap.
--
-- The real specimen states no "Register Date" line, but it DOES carry a
-- real as-of date on its table header: "Status (as on 31-Mar-2025)". That
-- label has no colon, and `pypdf` wraps it mid-token ("Status (as on 31-"
-- / "Mar-2025)") — `src/extraction/labelvalue.py`'s `_find_value` gained a
-- colon-less, one-line-lookahead date fallback this session (mirroring the
-- existing `money` one built for `BILL_OF_ENTRY`) specifically to bind
-- this. Re-pointing the label at "Status (as on" (kept required — see
-- shared migration 056 for why fabricating a stand-in was rejected) now
-- makes `GSTIN_REGISTER` `Extracted`, unblocking the table content shared
-- migration 053/056 already built and tenant migration 033 already stores.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Total GSTINs under PAN",as_of_date:date!:"Status (as on";table_pattern="(?P<gstin>[0-9]{2}\s*[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\s*[1-9A-Z]\s*Z\s*[0-9A-Z])\s+(?P<state>.+?)\s+(?P<registration_type>Regular|Composition|Casual Taxable Person|Non[- ]Resident Taxable Person|SEZ Unit|SEZ Developer|Input Service Distributor|TDS Deductor|TCS Collector|UIN Holder)\s+(?P<effective_date>[0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})\s+(?P<status>Active|Suspended\s*\(\s*[0-9]{1,2}-[A-Za-z]{3}-\s*[0-9]{4}\s*\)|Cancelled\s*\(\s*[0-9]{1,2}-[A-Za-z]{3}-\s*[0-9]{4}\s*\))\s+(?P<principal_place>(?:(?![0-9]{2}\s*[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\s*[1-9A-Z]\s*Z\s*[0-9A-Z]).)+)";table_columns=gstin:gstin,state:text,registration_type:text,effective_date:date,status:text,principal_place:text'
 WHERE doc_type_code = 'GSTIN_REGISTER';
