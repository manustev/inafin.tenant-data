"""Archetype 8 — the eleven sampled narrative-contract types.

Corrective refactor (2026-08-11): this module used to hold a `type()` factory
building eleven near-identical `NarrativeContractExtractor` subclasses off a
hardcoded `NARRATIVE_CONTRACT_DOC_TYPES` tuple — the "list of which 11 doc
types are archetype 8" was itself the last hardcoded piece even though the
factory avoided per-class duplication. Both are gone: which document types
are archetype 8 is now simply which rows `registry/document_types.csv` marks
`archetype=8`, and `src/extraction/registry.py`'s `build_extractor_registry`
reads that column straight from `platform_ref.document_type` — no Python list
to keep in sync. `NarrativeContractExtractor` (`src/extraction/base.py`) is
instantiated directly, once per registry row, with `label_spec={}`.

Confirmed against all eleven specimens directly (streamed-marinating-gray
.md's verification method): none use `Label: Value` form ANYWHERE — dates,
parties and terms are stated in prose ("entered into on 1 April 2023,
effective from Financial Year 2023-24, between..."). Every one of these
eleven therefore has the SAME empty `extraction_spec` (`fields=`), which is
why an empty spec is a legitimate, explicit registry value rather than an
oversight — see `src/extraction/spec.py`'s grammar.

Every one of these extracts as `Extracted(fields={})` (all-optional label
spec, `NarrativeContractExtractor`'s base class) — a real, successful outcome,
not a failure being hidden: the row exists so a MinIO Silver copy has
somewhere to attach its lineage, and `key_terms` is honestly empty rather than
populated with a guess.

The eleven, per registry/document_types.csv's archetype column (A2.02, A2.06,
A3.03, A3.04, A4.01-06, A4.09): MEMORANDUM_ARTICLES_OF_ASSOCIATION,
JOINT_VENTURE_AGREEMENT, BOARD_RESOLUTION_DIRECTOR_APPOINTMENT,
DIRECTOR_SERVICE_AGREEMENT, COST_SHARING_AGREEMENT,
FOREIGN_CUSTOMER_SERVICE_CONTRACT, OVERSEAS_VENDOR_CONTRACT,
JOBWORK_AGREEMENT, LONG_DURATION_SERVICE_CONTRACT,
TRANSFER_PRICING_DOCUMENTATION, SECURITY_AGENCY_CONTRACT.
"""

from __future__ import annotations
