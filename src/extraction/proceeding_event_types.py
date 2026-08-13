"""Archetype 6 — RBI_COMPOUNDING_ORDER and ITC_04_ACKNOWLEDGEMENT.

Corrective refactor (2026-08-11): the two hand-written `ProceedingEventExtractor`
subclasses this module used to hold are gone — their `doc_type_code` +
`authority` + `label_spec` now live as data in `registry/document_types.csv`'s
`extraction_spec` column, read at runtime by `src/extraction/registry.py`'s
`build_extractor_registry`. `ProceedingEventExtractor` (`src/extraction/
base.py`) is instantiated directly.

`ITC_04_ACKNOWLEDGEMENT`'s registry archetype was corrected 5 -> 6 by shared
migration 014 — see that migration's comment. Its specimen is a table (one
row per quarter's dispatch/return counts), not a `Label: Value` header, so
tier-1 legitimately cannot bind a single `reference_number`/`event_date` for
the WHOLE filing — its registry row still declares both required, so it lands
as a named `Partial`, same accepted-limitation shape as the two archetype-3
specimens documented in `entitlement_types.py`.
"""

from __future__ import annotations
