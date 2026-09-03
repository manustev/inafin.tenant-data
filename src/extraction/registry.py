"""build_extractor_registry — the runtime, DB-driven `EXTRACTOR_BY_DOC_TYPE`.

Corrective refactor (2026-08-11, see `src/extraction/spec.py`'s module
docstring for the full story): this used to be an import-time
`dict[str, type[DocumentExtractor]]` of CLASSES, assembled from five
hand-written modules (`entitlement_types.py` and the archetype 6/7/4/8
equivalents) that each hardcoded ~1-24 per-type subclasses. It is now built
at RUNTIME from `platform_ref.document_type` — the same registry
`src/silver/promote.py`'s `_load_spec` already reads `field_contract` from,
same query shape, same connection idiom (`pool.transaction(ctx, Role.INGEST)`,
fully qualified SQL, no new pattern invented.

Returns already-CONSTRUCTED instances, not classes: `dict[str,
DocumentExtractor]`. Building all ~39 costs one query plus ~39 cheap
`__init__` calls (every archetype base's constructor only stores its `pool`
reference — see e.g. `EntitlementService.__init__` — no I/O happens at
construction time), so there is no reason to defer instantiation the way the
old class-dict deferred it. `src/extraction/dispatch.py` is still the one
place an extractor is actually USED (`.extract()` / `.to_silver()`).

Archetypes 3/4/6/7/8 are fully described by their registry row (`doc_type_code`,
`label_spec`, `authority`) — one base class each, uniform constructor shape,
`_ARCHETYPE_BASE_CLASS` below is the whole dispatch table. Archetype 1's PDF
extractors keep real per-type Python (`_csv_row` — GST-rate computation, cast
handling) so archetype 1 is dispatched through
`transaction_types.TRANSACTION_DOCUMENT_EXTRACTOR_CLASS_BY_DOC_TYPE` instead
— a small, fixed, explicitly-out-of-scope dict, same category as archetype
2's `register_types.REGISTER_DOCUMENT_EXTRACTORS`, which is merged in
UNCHANGED: those three are genuinely bespoke per-type table-parsing code
(a payroll table and an FX table share no columns), not label:value-shaped, so
there is no `extraction_spec` for them and this module does not query for
one — `platform_ref.document_type.archetype = 2` rows are never selected here.
"""

from __future__ import annotations

from typing import Protocol, cast

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.extraction.base import (
    DocumentExtractor,
    EntitlementExtractor,
    EntityMasterExtractor,
    FinancialStatementExtractor,
    NarrativeContractExtractor,
    ProceedingEventExtractor,
)
from src.extraction.entity_master_types import (
    DirectorListExtractor,
    GstinRegisterExtractor,
    KmpListExtractor,
    ShareholdingPatternExtractor,
    TableFactExtractor,
)
from src.extraction.labelvalue import LabelSpec
from src.extraction.register_types import REGISTER_DOCUMENT_EXTRACTORS
from src.extraction.spec import parse_extraction_spec
from src.extraction.transaction_types import TRANSACTION_DOCUMENT_EXTRACTOR_CLASS_BY_DOC_TYPE
from src.provisioning.objectstore import ObjectStorePort

#: Archetype 7 rows whose table content needs a `TableFactExtractor`
#: subclass, not the generic `EntityMasterExtractor` — same small, fixed-dict
#: shape `TRANSACTION_DOCUMENT_EXTRACTOR_CLASS_BY_DOC_TYPE` already uses for
#: archetype 1's two bespoke PDF types. `RELATED_PARTY_REGISTER` is
#: deliberately absent — see `entity_master_types.py`'s module docstring for
#: why its table is not built. Every other archetype-7 doc_type_code still
#: gets the generic base class below, unchanged.
_ENTITY_MASTER_EXTRACTOR_CLASS_BY_DOC_TYPE: dict[str, type[TableFactExtractor]] = {
    "SHAREHOLDING_PATTERN": ShareholdingPatternExtractor,
    "GSTIN_REGISTER": GstinRegisterExtractor,
    "DIRECTOR_LIST_WITH_DIN": DirectorListExtractor,
    "KMP_LIST": KmpListExtractor,
}


class _ArchetypeExtractorCtor(Protocol):
    """The one constructor shape all five label:value archetype base classes
    share — declared once so `_ARCHETYPE_BASE_CLASS` can be called uniformly
    below without mypy falling back to `DocumentExtractor`'s (nonexistent,
    ABC-default) `__init__`."""

    def __call__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> DocumentExtractor: ...


#: One base class per label:value-shaped archetype. Fixed — archetypes don't
#: get invented per document type — and short on purpose: this dict, plus the
#: two small per-type dicts above it, is the entirety of what a document
#: type's ARCHETYPE membership dispatches to. WHICH label:value fields a type
#: within an archetype binds is what moved to data; that a `LUT` is archetype
#: 3 at all did not, and does not need to (ARCHITECTURE.md 6 fixes the 8
#: archetypes themselves).
_ARCHETYPE_BASE_CLASS: dict[int, _ArchetypeExtractorCtor] = {
    3: EntitlementExtractor,
    4: FinancialStatementExtractor,
    6: ProceedingEventExtractor,
    7: EntityMasterExtractor,
    8: NarrativeContractExtractor,
}


async def build_extractor_registry(
    pool: TenantScopedPool,
    ctx: TenantContext,
    *,
    store: ObjectStorePort | None = None,
) -> dict[str, DocumentExtractor]:
    """Read every in-scope archetype-1/3/4/6/7/8 document type's extraction
    rule from the registry and build one extractor instance per type.

    Read per call rather than cached at import time — the whole point of this
    refactor: a corrected `extraction_spec` cell takes effect the next time
    this is called, not at process-restart time. `_load_spec` in
    `src/silver/promote.py` makes the identical trade for `field_contract`,
    for the identical reason.
    """
    async with pool.transaction(ctx, Role.INGEST) as conn:
        rows = await (
            await conn.execute(
                "SELECT doc_type_code, archetype, extraction_spec"
                "  FROM platform_ref.document_type"
                " WHERE in_scope AND archetype IN (1, 3, 4, 6, 7, 8)"
            )
        ).fetchall()

    registry: dict[str, DocumentExtractor] = {}
    for raw_row in rows:
        doc_type_code, archetype, raw_spec = cast("tuple[str, int, str]", raw_row)
        if not raw_spec:
            # An empty cell means "no PDF extractor built for this type yet"
            # — not every in-scope archetype-1/3/4/6/7/8 row has a specimen
            # to sample (this batch built 47 of them). Distinct from the
            # eleven narrative-contract rows, whose cell is the non-empty,
            # explicit `fields=` (parses to zero fields, on purpose — see
            # src/extraction/spec.py and narrative_contract_types.py).
            continue
        spec = parse_extraction_spec(raw_spec)

        if archetype == 1:
            transaction_cls = TRANSACTION_DOCUMENT_EXTRACTOR_CLASS_BY_DOC_TYPE.get(
                doc_type_code
            )
            if transaction_cls is None:
                # Archetype 1 has ~11 CSV/NDJSON-loaded types with no PDF
                # extractor at all (this batch samples only the two whose
                # source document is a PDF) — not every archetype-1 row is
                # expected to be extractable here.
                continue
            registry[doc_type_code] = transaction_cls(
                pool, doc_type_code=doc_type_code, label_spec=spec.label_spec, store=store,
            )
            continue

        fact_cls = (
            _ENTITY_MASTER_EXTRACTOR_CLASS_BY_DOC_TYPE.get(doc_type_code)
            if archetype == 7
            else None
        )
        if fact_cls is not None:
            if spec.table is None:
                # A registry row that names this doc_type_code as table-shaped
                # but whose extraction_spec cell has no table_pattern/
                # table_columns clause is a data-integrity failure, not a
                # shape this loop should quietly fall back from — same rule
                # `derive_document_schema` applies to a REGISTER_LOADER row
                # with no RegisterSpec entry.
                raise ValueError(
                    f"{doc_type_code}: dispatch requires a table extraction "
                    f"but extraction_spec declares no table_pattern/table_columns"
                )
            registry[doc_type_code] = fact_cls(
                pool,
                doc_type_code=doc_type_code,
                label_spec=spec.label_spec,
                table_spec=spec.table,
                authority=spec.authority,
                store=store,
            )
            continue

        base_cls = _ARCHETYPE_BASE_CLASS[archetype]
        registry[doc_type_code] = base_cls(
            pool,
            doc_type_code=doc_type_code,
            label_spec=spec.label_spec,
            authority=spec.authority,
            store=store,
        )

    for doc_type_code, register_cls in REGISTER_DOCUMENT_EXTRACTORS.items():
        registry[doc_type_code] = register_cls(pool, store=store)

    return registry


__all__ = ["build_extractor_registry"]
