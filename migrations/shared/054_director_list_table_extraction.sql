-- =============================================================================
-- Shared migration 054 — DIRECTOR_LIST_WITH_DIN's `extraction_spec` gains a
-- table_pattern/table_columns clause, so its director list is captured, not
-- discarded.
--
-- `name` is bounded to at most 6 whitespace-separated tokens (the real
-- specimen's own longest name, "Michael Tan Wei Liang", is 4 — this bound
-- has headroom, not a tight fit) for the same reason SHAREHOLDING_PATTERN's
-- `holder_name` needed one (shared migration 052): an unbounded leading
-- free-text group can absorb the table's own multi-line column-header text.
-- That alone was NOT sufficient here — the real header's final wrapped word,
-- "Active" (from "Currently \nActive"), is short enough to fit under any
-- believable name-length bound, so the pattern also excludes it (and its
-- neighbour "Currently") by name via a negative lookahead. This is the
-- document's OWN observed header vocabulary, not an invented one.
--
-- `din` (an 8-digit anchor immediately after the name) is what makes `name`
-- unambiguous without needing a fixed-vocabulary trick the way
-- GSTIN_REGISTER's `state`/`registration_type` boundary did (shared
-- migration 053) — `designation` sits between two hard anchors (`din`,
-- `appointment_date`) so its own free-text form is never ambiguous either.
-- `cessation_date`/`currently_active` both tolerate a wrapped "(resigned)"
-- qualifier, verified against the real specimen's one resigned director.
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Financial Years Covered",as_of_date:date!:"Source";table_pattern="(?!(?:Active|Currently)\b)(?P<name>(?:\S+\s+){0,5}?\S+)\s+(?P<din>\d{8})\s+(?P<designation>.+?)\s+(?P<appointment_date>[0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})\s+(?P<cessation_date>—|[0-9]{1,2}-[A-Za-z]{3}-[0-9]{4}(?:\s*\(resigned\))?)\s+(?P<currently_active>Yes|No(?:\s*\(resigned\))?)";table_columns=name:text,din:text,designation:text,appointment_date:date,cessation_date:text,currently_active:text'
 WHERE doc_type_code = 'DIRECTOR_LIST_WITH_DIN';
