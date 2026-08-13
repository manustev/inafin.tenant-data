"""Archetype 3 — the 24 entitlement-instrument types sampled in
`reference/A1-A7Documents/`.

Corrective refactor (2026-08-11): this module used to hold 24 hand-written
`EntitlementExtractor` subclasses, each a `ClassVar`-only `doc_type_code` +
`instrument_type` + `issuing_authority` + `label_spec`. That per-type config
now lives as data, in each type's `extraction_spec` cell in `registry/
document_types.csv`, read at runtime by `src/extraction/registry.py`'s
`build_extractor_registry` (`platform_ref.document_type.extraction_spec`,
parsed by `src/extraction/spec.py`). There is nothing left to subclass —
`EntitlementExtractor` (`src/extraction/base.py`) is instantiated directly,
once per registry row.

The registry's own `archetype` column is authoritative for which 24 types
these are (24 rows, not the 25 streamed-marinating-gray.md's prose estimated
before the CSV was re-checked — SEZ has five sampled sub-types, not six).

Two date shapes recur across the specimens, both expressible in one
`extraction_spec` field token each:

  * `validity` (`date_range`) — a document that states its own window as one
    line: `"Validity: 01 April 2024 to 31 March 2025"`.
  * `valid_from` (`date`), `valid_to` unbound — a document that states only an
    issue/registration date and confers standing indefinitely (the same
    "NULL means unrestricted" pattern `entitlement_instrument` already uses
    for `scope_hsn`, applied here to the open-ended validity window).

A few types (`SEZ_ANNUAL_PERFORMANCE_REPORT`, `CUSTOMS_DUTY_EXEMPTION_
CERTIFICATE`) have no clean label:value date at all — a periodic table, or a
prose validity clause ("Co-terminus with EOU LoP"). Both keep `valid_from`
required in their registry row, so they extract as a named
`Partial(missing=("valid_from",))` rather than being special-cased into
skipping validation — an honest, accepted limitation of tier-1 parsing on
those two specimens, not a bug. (`SEZ_ANNUAL_PERFORMANCE_REPORT`'s specimen
states years only in a table — FY 2021-22, ... — never as a `Label: Value`
date; `CUSTOMS_DUTY_EXEMPTION_CERTIFICATE`'s Validity line is prose,
"Co-terminus with EOU LoP — valid until 10 July 2026", with no clean
`Date of Issue` label either.)
"""

from __future__ import annotations
