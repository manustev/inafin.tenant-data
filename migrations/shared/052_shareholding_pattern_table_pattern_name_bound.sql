-- =============================================================================
-- Shared migration 052 — SHAREHOLDING_PATTERN's `table_pattern` gains a
-- bounded `holder_name` group.
--
-- WHY. `src/extraction/tablevalue.py`'s row-reconstruction algorithm was
-- rewritten this session (see that module's docstring) to search WINDOWS of
-- physical lines, not a single one-line look-back — needed to reconstruct
-- `GSTIN_REGISTER`/`DIRECTOR_LIST_WITH_DIN`/`KMP_LIST`'s real rows, which
-- wrap across up to 5 physical lines, sometimes mid-token. An unbounded
-- `.+?` leading name group is safe under the OLD one-line algorithm (a
-- digit-bearing header line resets it) but not under the new windowed one —
-- verified against the real SHAREHOLDING_PATTERN specimen, an unbounded
-- `holder_name` absorbed the table's own multi-line column-header text
-- ahead of the first real row. Bounding `holder_name` to at most 6
-- whitespace-separated tokens (`(?:\S+\s+){0,5}?\S+`) — chosen from the
-- real specimen's own longest holder names ("Arvind Rao Deshmukh (Promoter
-- / MD)", "Public / Others (< 1% each)", both exactly 6 — not tuned to make
-- one test pass) — closes that gap the same way it already does for
-- `GSTIN_REGISTER`/`DIRECTOR_LIST_WITH_DIN`/`KMP_LIST` (shared migrations
-- 053-055).
-- =============================================================================

UPDATE platform_ref.document_type SET
    extraction_spec = 'fields=reference_number:text!:"Financial Year",as_of_date:date!:"Financial Year";table_pattern="(?P<holder_name>(?:\S+\s+){0,5}?\S+)\s+(?P<shares>[\d,]+)\s+(?P<pct_current_fy>[\d.]+)%\s+(?P<pct_prior_fy>[\d.]+)%\s+(?P<pledged>Yes|No)";table_columns=holder_name:text,shares:money,pct_current_fy:money,pct_prior_fy:money,pledged:text'
 WHERE doc_type_code = 'SHAREHOLDING_PATTERN';
