"""Archetype 4 — the one sampled type, RELATED_PARTY_DISCLOSURE_IND_AS_24
(A2.05).

Corrective refactor (2026-08-11): the one hand-written
`FinancialStatementExtractor` subclass this module used to hold is gone — its
`doc_type_code` + `label_spec` now live as data in `registry/document_types
.csv`'s `extraction_spec` column, read at runtime by `src/extraction/
registry.py`'s `build_extractor_registry`. `FinancialStatementExtractor`
(`src/extraction/base.py`) is instantiated directly.

`auditor` is the one label tier-1 reliably binds; `financial_year` and
`filed_date` have no clean `Label: Value` line in the specimen (both appear
only in prose/title text), so `headline_figures` stays empty —
`migrations/tenant/018_financial_statement_extract.sql`'s accepted
limitation.
"""

from __future__ import annotations
