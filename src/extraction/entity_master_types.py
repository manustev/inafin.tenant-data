"""Archetype 7 — the seven entity/counterparty master types.

Corrective refactor (2026-08-11): the seven hand-written `EntityMasterExtractor`
subclasses this module used to hold are gone — their `doc_type_code` +
`label_spec` now live as data in `registry/document_types.csv`'s
`extraction_spec` column, read at runtime by `src/extraction/registry.py`'s
`build_extractor_registry`. `EntityMasterExtractor` (`src/extraction/base.py`)
is instantiated directly.

Confirmed against the real specimens (streamed-marinating-gray.md's
verification method) which of these bind BOTH `reference_number` and
`as_of_date` as clean `Label: Value` lines, and which only bind
`reference_number` — the latter extract as a named
`Partial(missing=("as_of_date",))`, an honest tier-1 limitation
(`migrations/tenant/017_entity_master_record.sql`'s header), not a bug:

    Extracted                    CERTIFICATE_OF_INCORPORATION,
                                  RELATED_PARTY_REGISTER, FORM_DIR_12
    Partial(missing=as_of_date)  SHAREHOLDING_PATTERN, GSTIN_REGISTER,
                                  DIRECTOR_LIST_WITH_DIN, KMP_LIST
"""

from __future__ import annotations
