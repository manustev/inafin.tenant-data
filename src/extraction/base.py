"""DocumentExtractor ABC, and one to_silver() hook per archetype base class.

Corrective refactor (2026-08-11, `TYPED-TABLES-PLAN.md`-style CSV-not-code
fix — see `src/extraction/spec.py`'s module docstring for the full story):
`doc_type_code`, `label_spec` and each archetype's fixed vocabulary value
(`issuing_authority`, proceeding `authority`, ...) used to be `ClassVar`s a
concrete per-type subclass declared, one class per document type. They are
now CONSTRUCTOR ARGUMENTS, supplied at runtime by
`src/extraction/registry.py`'s `build_extractor_registry`, which reads them
from `platform_ref.document_type.extraction_spec`
(`src/extraction/spec.py:parse_extraction_spec`). `extract()` is still never
overridden by the archetype-3/4/6/7/8 shape — it is exactly
`parse_label_value(pages, self.label_spec)`, which is what keeps "add the Nth
type" a data problem (the `RegisterSpec` precedent,
`src/silver/registers/spec.py`) — the same instance attribute, now set in
`__init__` instead of declared on a subclass.

Two archetypes still have concrete per-type Python subclasses on purpose,
because real business logic — not just a label:value binding — lives there:
`TransactionDocumentExtractor`'s two subclasses (`src/extraction/
transaction_types.py`) implement `_csv_row` (GST-rate computation, cast
handling), and now take `doc_type_code`/`label_spec` as constructor input the
same way the other five archetype bases do. `RegisterDocumentExtractor`'s
three subclasses (`src/extraction/register_types.py`) implement bespoke
per-type table parsing and are explicitly OUT of this refactor's scope
(`extract()` is overridden per type, so there is no label:value spec to move
to data) — `doc_type_code` and `spec: ClassVar[RegisterSpec]` stay `ClassVar`s
there, unchanged.

ONE-DOCUMENT-ONE-BATCH. Every archetype base here writes its own
`ingest_batch` row before the archetype table's row, because unlike a CSV
register (`RegisterLoader`) or a multi-line transaction file (`promote.py`),
a PDF extraction always produces at most one Silver row per artefact.
`_create_single_document_batch` is the one place that manifest row is built,
shared across every archetype base so the shape (row_count=1, the artefact's
own content hash, `bronze_manifest_ref = ingest_id`) cannot drift between
them. It is NOT wrapped in the same transaction as the archetype row's own
insert (`EntitlementService.record` opens its own) — a real gap against the
"manifest and rows commit together" invariant `promote.py`'s header states,
accepted here because nothing currently reads "rows belonging to this batch"
for these archetypes the way Pipeline 2 reads `transaction_document` rows
against `ingest_batch`; see `TODO.md` for this as a named limitation.

ESCALATION SINK. Every `to_silver` reuses `SilverPromotionService._quarantine`
(`src/silver/promote.py`) for `NoTextLayer` and a `Partial` with a missing
required field — `TODO.md`'s agreed design: one document per artefact makes
"quarantine the whole artefact" the correct granularity, the same call
`promote.py` and `RegisterLoader` already make for their own FATAL cases.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, cast

from psycopg import sql

from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.extraction.labelvalue import (
    Extracted,
    ExtractionOutcome,
    LabelSpec,
    NoTextLayer,
    Partial,
    parse_label_value,
)
from src.provisioning.objectstore import ObjectStorePort
from src.silver.entitlement import EntitlementService, InstrumentRecord
from src.silver.entity_master import EntityMasterRecord, EntityMasterService
from src.silver.financial_statement import FinancialStatementRecord, FinancialStatementService
from src.silver.narrative_contract import NarrativeContractRecord, NarrativeContractService
from src.silver.proceeding_event import ProceedingEventRecord, ProceedingEventService
from src.silver.promote import SilverPromotionService
from src.silver.registers import RegisterLoader, RegisterSpec
from src.silver.validate import ValidationResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SilverWriteResult:
    """What one extraction+write attempt produced, for the caller to log or
    assert on. Not a Silver row itself — `record_id` names one when
    `written` is True.

    `batch_id` names the `ingest_batch` row `create_single_document_batch`
    wrote alongside it — every archetype's `to_silver()` creates one, even
    though `record_id` (the archetype table's own row) is a different id.
    `src/dispatch/router.py` reads this to publish a `BatchManifest` the
    same way it does for the other three dispatch mechanisms; a `to_silver`
    that returns `written=True` without a `batch_id` would be a manifest
    Pipeline 2 can never learn about."""

    written: bool
    record_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    reason: str | None = None


async def create_single_document_batch(
    pool: TenantScopedPool,
    ctx: TenantContext,
    *,
    document_type: str,
    entity_id: uuid.UUID,
    ingest_id: uuid.UUID,
    pdf_bytes: bytes,
    period: dt.date,
    ingest_run_id: uuid.UUID,
    row_count: int = 1,
) -> uuid.UUID:
    """Insert the one `ingest_batch` row a single-PDF extraction needs.

    `period_start = period_end = period`: a PDF extraction has no natural
    "period" the way a monthly register export does, so the document's own
    primary date (validity start, event date, `as_of_date`, ...) stands in
    for both ends — the same choice `RegisterLoader` makes when a register
    has no `valid_from_column`, falling back to a single anchor date.
    """
    batch_id = uuid.uuid4()
    content_hash = hashlib.sha256(pdf_bytes).digest()
    async with pool.transaction(ctx, Role.INGEST) as conn:
        await conn.execute(
            sql.SQL(
                """
                INSERT INTO {}.ingest_batch (
                    batch_id, entity_id, document_type, source_stream,
                    period_start, period_end, row_count, content_hash,
                    bronze_manifest_ref, status, ingest_run_id
                ) VALUES (%s, %s, %s, 'B', %s, %s, %s, %s, %s, 'READY', %s)
                """
            ).format(sql.Identifier(ctx.silver_schema)),
            (
                batch_id, entity_id, document_type, period, period,
                row_count, content_hash, str(ingest_id), ingest_run_id,
            ),
        )
    return batch_id


def _put_silver_copy(
    store: ObjectStorePort | None,
    ctx: TenantContext,
    *,
    doc_type: str,
    ingest_id: uuid.UUID,
    pdf_bytes: bytes,
) -> None:
    """Write the Silver-side MinIO copy, best-effort.

    `store=None` (the default in every extractor's constructor) means "no
    object store configured" and is a silent no-op — the same shape
    `ProvisioningService.provision` already tolerates for DB-only tests
    (`src/provisioning/service.py`). A configured store that FAILS mid-write
    is logged and swallowed rather than raised: the typed Silver row already
    committed by the time this runs, and Kafka publication after a Silver
    commit is already best-effort in `promote.py` for the identical
    reason — a storage hiccup on the convenience copy must not make an
    otherwise-successful promotion look like a failure to the caller.
    """
    if store is None:
        return
    try:
        store.put_silver(
            slug=ctx.slug, doc_type=doc_type, ingest_id=str(ingest_id),
            data=pdf_bytes, suffix="pdf", promoted_at=dt.datetime.now(dt.UTC),
        )
    except Exception:
        logger.warning(
            "failed to write Silver MinIO copy for %s %s", doc_type, ingest_id, exc_info=True
        )


class DocumentExtractor(ABC):
    """One document type's extraction rule.

    `doc_type_code` is a `platform_ref.document_type.doc_type_code` value —
    the same registry code every other Silver write path uses, so a
    quarantine record or a batch row this package writes reads exactly like
    one `promote.py` or `RegisterLoader` would have written.

    `doc_type_code` and `label_spec` are set by a concrete subclass's
    `__init__` (constructor arguments, sourced from `platform_ref.document_
    type.extraction_spec` at registry-build time) rather than declared as
    `ClassVar`s — the corrective refactor this module's docstring describes.
    `RegisterDocumentExtractor`'s subclasses are the one exception, declaring
    `doc_type_code` as a real `ClassVar` (out of scope, see module docstring).
    """

    doc_type_code: str
    label_spec: LabelSpec

    def extract(self, pages: list[str]) -> ExtractionOutcome:
        return parse_label_value(pages, self.label_spec)

    @abstractmethod
    async def to_silver(
        self,
        outcome: ExtractionOutcome,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        pdf_bytes: bytes,
        ingest_run_id: uuid.UUID | None = None,
    ) -> SilverWriteResult:
        """Turn one `ExtractionOutcome` into a Silver write (or a
        quarantine). Implemented once per archetype base class, never per
        concrete type."""


class EntitlementExtractor(DocumentExtractor):
    """Archetype 3 base — turns `Extracted` into an `InstrumentRecord` and
    calls `EntitlementService.record` UNCHANGED (`src/silver/entitlement.py`).

    Constructed with `doc_type_code`, `label_spec` and `issuing_authority` (a
    fixed `Issuing_Authority` vocabulary value — WHO issues this document
    never varies by instance, only WHICH instance does) — all three read from
    `platform_ref.document_type.extraction_spec` by `src/extraction/
    registry.py`'s `build_extractor_registry`. There is no separate
    `instrument_type`: every sampled type sets it identical to
    `doc_type_code` (confirmed across all 24 entitlement classes before this
    refactor), so `to_silver` uses `self.doc_type_code` directly.
    """

    #: Some specimens carry only an issue/registration date, not a window —
    #: an unrestricted-duration instrument. `entitlement_instrument`'s
    #: default is the same open-ended sentinel (tenant migration 006).
    default_valid_to: ClassVar[dt.date] = dt.date(9999, 12, 31)

    def __init__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> None:
        # `authority` is a required clause for archetype 3 in practice — see
        # `_check_extraction_specs` in scripts/gen_registry_seed.py — but
        # typed as optional so every archetype base class shares the exact
        # same constructor shape, which is what lets
        # `build_extractor_registry` call all of them uniformly.
        self.doc_type_code = doc_type_code
        self.label_spec = label_spec
        self.issuing_authority = authority or ""
        self._pool = pool
        self._service = EntitlementService(pool)
        self._promoter = SilverPromotionService(pool)
        self._store = store

    async def to_silver(
        self,
        outcome: ExtractionOutcome,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        pdf_bytes: bytes,
        ingest_run_id: uuid.UUID | None = None,
    ) -> SilverWriteResult:
        ingest_run_id = ingest_run_id or uuid.uuid4()

        if isinstance(outcome, NoTextLayer):
            await self._quarantine(
                ctx, ingest_id, entity_id, ["no extractable text layer (NoTextLayer)"],
                ingest_run_id,
            )
            return SilverWriteResult(written=False, reason="NoTextLayer")

        if isinstance(outcome, Partial):
            reasons = []
            if outcome.missing:
                reasons.append(f"missing required field(s): {', '.join(outcome.missing)}")
            if outcome.failed:
                reasons.append(f"failed to parse field(s): {', '.join(outcome.failed)}")
            await self._quarantine(ctx, ingest_id, entity_id, reasons, ingest_run_id)
            return SilverWriteResult(
                written=False,
                reason=f"Partial: missing={outcome.missing} failed={outcome.failed}",
            )

        assert isinstance(outcome, Extracted)  # exhaustiveness check, not a test assertion
        fields = outcome.fields
        valid_from: dt.date
        valid_to: dt.date
        if "validity" in fields:
            valid_from, valid_to = fields["validity"]  # type: ignore[misc]
        else:
            valid_from = fields["valid_from"]  # type: ignore[assignment]
            valid_to = fields.get("valid_to", self.default_valid_to)  # type: ignore[assignment]

        batch_id = await create_single_document_batch(
            self._pool, ctx,
            document_type=self.doc_type_code, entity_id=entity_id,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes, period=valid_from,
            ingest_run_id=ingest_run_id,
        )
        rec = InstrumentRecord(
            entity_id=entity_id,
            instrument_type=self.doc_type_code,
            issuing_authority=self.issuing_authority,
            instrument_number=fields["instrument_number"],  # type: ignore[arg-type]
            valid_from=valid_from,
            valid_to=valid_to,
            batch_id=batch_id,
            bronze_ingest_id=ingest_id,
            scope_hsn=fields.get("scope_hsn"),  # type: ignore[arg-type]
            scope=dict(fields.get("scope") or {}),  # type: ignore[call-overload]
            obligation=dict(fields.get("obligation") or {}),  # type: ignore[call-overload]
        )
        instrument_id = await self._service.record_or_supersede(ctx, rec)
        _put_silver_copy(
            self._store, ctx, doc_type=self.doc_type_code,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes,
        )
        return SilverWriteResult(written=True, record_id=instrument_id, batch_id=batch_id)

    async def _quarantine(
        self,
        ctx: TenantContext,
        ingest_id: uuid.UUID,
        entity_id: uuid.UUID,
        reasons: list[str],
        ingest_run_id: uuid.UUID,
    ) -> None:
        await self._promoter._quarantine(  # the documented reuse point (module docstring)
            ctx, ingest_id, entity_id, self.doc_type_code,
            ValidationResult(rejections=reasons or ["extraction produced no usable fields"]),
            ingest_run_id,
        )


def _partial_reasons(outcome: Partial) -> list[str]:
    """The `_quarantine` reason text shared by every archetype base below —
    kept in one place so a Partial's message reads identically regardless of
    which archetype table produced it."""
    reasons = []
    if outcome.missing:
        reasons.append(f"missing required field(s): {', '.join(outcome.missing)}")
    if outcome.failed:
        reasons.append(f"failed to parse field(s): {', '.join(outcome.failed)}")
    return reasons


class ProceedingEventExtractor(DocumentExtractor):
    """Archetype 6 base — turns `Extracted` into a `ProceedingEventRecord` and
    calls `ProceedingEventService.record` (`src/silver/proceeding_event.py`).

    Constructed with `doc_type_code` (== `event_type` — the registry code IS
    the event type, same convention as archetype 3), `authority` (a fixed
    `Proceeding_Authority` vocabulary value) and `label_spec`, all read from
    `platform_ref.document_type.extraction_spec`.
    """

    def __init__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> None:
        self.doc_type_code = doc_type_code
        self.label_spec = label_spec
        self.authority = authority or ""
        self._pool = pool
        self._service = ProceedingEventService(pool)
        self._promoter = SilverPromotionService(pool)
        self._store = store

    async def to_silver(
        self,
        outcome: ExtractionOutcome,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        pdf_bytes: bytes,
        ingest_run_id: uuid.UUID | None = None,
    ) -> SilverWriteResult:
        ingest_run_id = ingest_run_id or uuid.uuid4()

        if isinstance(outcome, NoTextLayer):
            await self._quarantine(ctx, ingest_id, entity_id, ["NoTextLayer"], ingest_run_id)
            return SilverWriteResult(written=False, reason="NoTextLayer")
        if isinstance(outcome, Partial):
            await self._quarantine(
                ctx, ingest_id, entity_id, _partial_reasons(outcome), ingest_run_id
            )
            return SilverWriteResult(
                written=False,
                reason=f"Partial: missing={outcome.missing} failed={outcome.failed}",
            )

        assert isinstance(outcome, Extracted)  # exhaustiveness check, not a test assertion
        fields = outcome.fields
        event_date: dt.date = fields["event_date"]  # type: ignore[assignment]
        batch_id = await create_single_document_batch(
            self._pool, ctx,
            document_type=self.doc_type_code, entity_id=entity_id,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes, period=event_date,
            ingest_run_id=ingest_run_id,
        )
        rec = ProceedingEventRecord(
            entity_id=entity_id,
            event_type=self.doc_type_code,
            authority=self.authority,
            reference_number=fields["reference_number"],  # type: ignore[arg-type]
            event_date=event_date,
            amount=fields.get("amount"),  # type: ignore[arg-type]
            batch_id=batch_id,
            bronze_ingest_id=ingest_id,
        )
        event_id = await self._service.record_or_supersede(ctx, rec)
        _put_silver_copy(
            self._store, ctx, doc_type=self.doc_type_code,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes,
        )
        return SilverWriteResult(written=True, record_id=event_id, batch_id=batch_id)

    async def _quarantine(
        self, ctx: TenantContext, ingest_id: uuid.UUID, entity_id: uuid.UUID,
        reasons: list[str], ingest_run_id: uuid.UUID,
    ) -> None:
        await self._promoter._quarantine(
            ctx, ingest_id, entity_id, self.doc_type_code,
            ValidationResult(rejections=reasons or ["extraction produced no usable fields"]),
            ingest_run_id,
        )


class EntityMasterExtractor(DocumentExtractor):
    """Archetype 7 base — turns `Extracted` into an `EntityMasterRecord` and
    calls `EntityMasterService.record` (`src/silver/entity_master.py`).

    Several sampled types (a shareholding table, a KMP roster) have no single
    external reference number of their own; the `label_spec` this type is
    constructed with binds whatever the specimen DOES state as `Label: Value`
    — several legitimately land as a named `Partial` rather than `Extracted`
    (`migrations/tenant/017_entity_master_record.sql`'s header). No
    `authority` — this archetype has none.
    """

    def __init__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> None:
        _ = authority  # this archetype has no authority clause; kept for a uniform constructor
        self.doc_type_code = doc_type_code
        self.label_spec = label_spec
        self._pool = pool
        self._service = EntityMasterService(pool)
        self._promoter = SilverPromotionService(pool)
        self._store = store

    async def to_silver(
        self,
        outcome: ExtractionOutcome,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        pdf_bytes: bytes,
        ingest_run_id: uuid.UUID | None = None,
    ) -> SilverWriteResult:
        ingest_run_id = ingest_run_id or uuid.uuid4()

        if isinstance(outcome, NoTextLayer):
            await self._quarantine(ctx, ingest_id, entity_id, ["NoTextLayer"], ingest_run_id)
            return SilverWriteResult(written=False, reason="NoTextLayer")
        if isinstance(outcome, Partial):
            await self._quarantine(
                ctx, ingest_id, entity_id, _partial_reasons(outcome), ingest_run_id
            )
            return SilverWriteResult(
                written=False,
                reason=f"Partial: missing={outcome.missing} failed={outcome.failed}",
            )

        assert isinstance(outcome, Extracted)  # exhaustiveness check, not a test assertion
        fields = outcome.fields
        as_of_date: dt.date = fields["as_of_date"]  # type: ignore[assignment]
        batch_id = await create_single_document_batch(
            self._pool, ctx,
            document_type=self.doc_type_code, entity_id=entity_id,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes, period=as_of_date,
            ingest_run_id=ingest_run_id,
        )
        rec = EntityMasterRecord(
            entity_id=entity_id,
            master_type=self.doc_type_code,
            reference_number=fields["reference_number"],  # type: ignore[arg-type]
            as_of_date=as_of_date,
            batch_id=batch_id,
            bronze_ingest_id=ingest_id,
        )
        record_id = await self._service.record_or_supersede(ctx, rec)
        _put_silver_copy(
            self._store, ctx, doc_type=self.doc_type_code,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes,
        )
        return SilverWriteResult(written=True, record_id=record_id, batch_id=batch_id)

    async def _quarantine(
        self, ctx: TenantContext, ingest_id: uuid.UUID, entity_id: uuid.UUID,
        reasons: list[str], ingest_run_id: uuid.UUID,
    ) -> None:
        await self._promoter._quarantine(
            ctx, ingest_id, entity_id, self.doc_type_code,
            ValidationResult(rejections=reasons or ["extraction produced no usable fields"]),
            ingest_run_id,
        )


class FinancialStatementExtractor(DocumentExtractor):
    """Archetype 4 base — turns `Extracted` into a `FinancialStatementRecord`
    and calls `FinancialStatementService.record`
    (`src/silver/financial_statement.py`). `headline_figures` is left empty by
    every sampled type in this batch — sparse tier-1 binding is the accepted
    limitation `migrations/tenant/018_financial_statement_extract.sql`'s
    header names. No `authority` — this archetype has none.
    """

    def __init__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> None:
        _ = authority  # this archetype has no authority clause; kept for a uniform constructor
        self.doc_type_code = doc_type_code
        self.label_spec = label_spec
        self._pool = pool
        self._service = FinancialStatementService(pool)
        self._promoter = SilverPromotionService(pool)
        self._store = store

    async def to_silver(
        self,
        outcome: ExtractionOutcome,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        pdf_bytes: bytes,
        ingest_run_id: uuid.UUID | None = None,
    ) -> SilverWriteResult:
        ingest_run_id = ingest_run_id or uuid.uuid4()

        if isinstance(outcome, NoTextLayer):
            await self._quarantine(ctx, ingest_id, entity_id, ["NoTextLayer"], ingest_run_id)
            return SilverWriteResult(written=False, reason="NoTextLayer")
        if isinstance(outcome, Partial):
            await self._quarantine(
                ctx, ingest_id, entity_id, _partial_reasons(outcome), ingest_run_id
            )
            return SilverWriteResult(
                written=False,
                reason=f"Partial: missing={outcome.missing} failed={outcome.failed}",
            )

        assert isinstance(outcome, Extracted)  # exhaustiveness check, not a test assertion
        fields = outcome.fields
        # No period-defining date is guaranteed here (`filed_date` is
        # optional — several specimens never state it as a clean label). The
        # ingestion date stands in, same fallback narrative_contract uses.
        period: dt.date = fields.get("filed_date") or dt.datetime.now(dt.UTC).date()  # type: ignore[assignment]
        batch_id = await create_single_document_batch(
            self._pool, ctx,
            document_type=self.doc_type_code, entity_id=entity_id,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes, period=period,
            ingest_run_id=ingest_run_id,
        )
        rec = FinancialStatementRecord(
            entity_id=entity_id,
            statement_type=self.doc_type_code,
            auditor=fields["auditor"],  # type: ignore[arg-type]
            financial_year=fields.get("financial_year"),  # type: ignore[arg-type]
            filed_date=fields.get("filed_date"),  # type: ignore[arg-type]
            batch_id=batch_id,
            bronze_ingest_id=ingest_id,
        )
        extract_id = await self._service.record_or_supersede(ctx, rec)
        _put_silver_copy(
            self._store, ctx, doc_type=self.doc_type_code,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes,
        )
        return SilverWriteResult(written=True, record_id=extract_id, batch_id=batch_id)

    async def _quarantine(
        self, ctx: TenantContext, ingest_id: uuid.UUID, entity_id: uuid.UUID,
        reasons: list[str], ingest_run_id: uuid.UUID,
    ) -> None:
        await self._promoter._quarantine(
            ctx, ingest_id, entity_id, self.doc_type_code,
            ValidationResult(rejections=reasons or ["extraction produced no usable fields"]),
            ingest_run_id,
        )


class NarrativeContractExtractor(DocumentExtractor):
    """Archetype 8 base — turns `Extracted` into a `NarrativeContractRecord`
    and calls `NarrativeContractService.record`
    (`src/silver/narrative_contract.py`).

    Every field this archetype's label specs declare is OPTIONAL
    (`migrations/tenant/019_narrative_contract.sql`'s header explains why) —
    a document with zero bound facts still writes a row with an empty
    `key_terms`, because the MinIO Silver copy (not this row) is the primary
    record for a prose contract. Only `NoTextLayer` refuses to write. No
    `authority` — this archetype has none. `label_spec` is legitimately empty
    for every one of the eleven sampled narrative-contract types (registry
    `extraction_spec` cell is `fields=`) — none use `Label: Value` form
    anywhere; see `registry/document_types.csv` and this refactor's removal
    of the old `NARRATIVE_CONTRACT_DOC_TYPES` hardcoded tuple.
    """

    def __init__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> None:
        _ = authority  # this archetype has no authority clause; kept for a uniform constructor
        self.doc_type_code = doc_type_code
        self.label_spec = label_spec
        self._pool = pool
        self._service = NarrativeContractService(pool)
        self._promoter = SilverPromotionService(pool)
        self._store = store

    async def to_silver(
        self,
        outcome: ExtractionOutcome,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        pdf_bytes: bytes,
        ingest_run_id: uuid.UUID | None = None,
    ) -> SilverWriteResult:
        ingest_run_id = ingest_run_id or uuid.uuid4()

        if isinstance(outcome, NoTextLayer):
            await self._quarantine(ctx, ingest_id, entity_id, ["NoTextLayer"], ingest_run_id)
            return SilverWriteResult(written=False, reason="NoTextLayer")

        # Partial is reachable only if a future subclass declares a required
        # field that fails to coerce — none in this batch do (see class
        # docstring) — so this still quarantines rather than silently
        # dropping the failed field, for whichever type eventually needs one.
        if isinstance(outcome, Partial) and outcome.missing:
            await self._quarantine(
                ctx, ingest_id, entity_id, _partial_reasons(outcome), ingest_run_id
            )
            return SilverWriteResult(
                written=False,
                reason=f"Partial: missing={outcome.missing} failed={outcome.failed}",
            )

        fields = outcome.fields if isinstance(outcome, Extracted | Partial) else {}
        period: dt.date = fields.get("effective_date") or dt.datetime.now(dt.UTC).date()  # type: ignore[assignment]
        batch_id = await create_single_document_batch(
            self._pool, ctx,
            document_type=self.doc_type_code, entity_id=entity_id,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes, period=period,
            ingest_run_id=ingest_run_id,
        )
        # Dates go into their own typed columns AND into key_terms as ISO
        # text — jsonb has no date type, and NarrativeContractService.record
        # json.dumps()s this dict directly, so a bare `date` object would
        # raise at write time rather than at extraction time.
        key_terms = {
            k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in fields.items()
        }
        rec = NarrativeContractRecord(
            entity_id=entity_id,
            contract_type=self.doc_type_code,
            counterparty_name=fields.get("counterparty_name"),  # type: ignore[arg-type]
            effective_date=fields.get("effective_date"),  # type: ignore[arg-type]
            term_end_date=fields.get("term_end_date"),  # type: ignore[arg-type]
            key_terms=key_terms,
            batch_id=batch_id,
            bronze_ingest_id=ingest_id,
        )
        contract_id = await self._service.record_or_supersede(ctx, rec)
        _put_silver_copy(
            self._store, ctx, doc_type=self.doc_type_code,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes,
        )
        return SilverWriteResult(written=True, record_id=contract_id, batch_id=batch_id)

    async def _quarantine(
        self, ctx: TenantContext, ingest_id: uuid.UUID, entity_id: uuid.UUID,
        reasons: list[str], ingest_run_id: uuid.UUID,
    ) -> None:
        await self._promoter._quarantine(
            ctx, ingest_id, entity_id, self.doc_type_code,
            ValidationResult(rejections=reasons or ["extraction produced no usable fields"]),
            ingest_run_id,
        )


class TransactionDocumentExtractor(DocumentExtractor):
    """Archetype 1 base — turns `Extracted` into a SINGLE-ROW CSV matching the
    type's own `field_contract` (`src/silver/contract.py`,
    `registry/document_types.csv`) and hands it, unchanged, to the EXISTING
    `SilverPromotionService.promote_transaction_documents`
    (`src/silver/promote.py`). No new persistence code — this class's whole
    job is text -> one CSV row; validation, quarantine and the
    `transaction_document`/`transaction_line` write are exactly the code path
    every CSV-uploaded archetype-1 artefact already goes through.

    A concrete subclass implements `_csv_row` (real business logic — GST-rate
    computation, cast handling — stays code, `src/extraction/
    transaction_types.py`), returning the row as a `dict[str, str]` (column
    name -> already-stringified value) plus the date that anchors the
    batch's period — everything else is handled here. `doc_type_code` and
    `label_spec` are constructor arguments, read from `platform_ref.document_
    type.extraction_spec` the same way the other five archetype bases are —
    only `_csv_row` is per-type Python now, not the label:value binding.
    """

    def __init__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> None:
        _ = authority  # this archetype has no authority clause; kept for a uniform constructor
        self.doc_type_code = doc_type_code
        self.label_spec = label_spec
        self._promoter = SilverPromotionService(pool)
        self._store = store

    def _csv_row(self, fields: dict[str, object]) -> tuple[dict[str, str], dt.date]:
        """Turn bound label:value fields into one CSV row + its period date.
        Implemented once per concrete type — the only per-type code in this
        archetype's extraction path."""
        raise NotImplementedError

    async def to_silver(
        self,
        outcome: ExtractionOutcome,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        pdf_bytes: bytes,
        ingest_run_id: uuid.UUID | None = None,
    ) -> SilverWriteResult:
        ingest_run_id = ingest_run_id or uuid.uuid4()

        if isinstance(outcome, NoTextLayer):
            await self._quarantine(ctx, ingest_id, entity_id, ["NoTextLayer"], ingest_run_id)
            return SilverWriteResult(written=False, reason="NoTextLayer")
        if isinstance(outcome, Partial):
            await self._quarantine(
                ctx, ingest_id, entity_id, _partial_reasons(outcome), ingest_run_id
            )
            return SilverWriteResult(
                written=False,
                reason=f"Partial: missing={outcome.missing} failed={outcome.failed}",
            )

        assert isinstance(outcome, Extracted)  # exhaustiveness check, not a test assertion
        row, period = self._csv_row(outcome.fields)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        csv_bytes = buf.getvalue().encode("utf-8")

        try:
            manifest = await self._promoter.promote_transaction_documents(
                ctx, document_type=self.doc_type_code, ingest_id=ingest_id,
                entity_id=entity_id, data=csv_bytes,
                period_start=period, period_end=period, ingest_run_id=ingest_run_id,
            )
        except ValidationRejected as exc:
            # promote_transaction_documents already quarantined internally
            # before raising — see its own docstring. Nothing more to do here.
            return SilverWriteResult(written=False, reason=str(exc))
        _put_silver_copy(
            self._store, ctx, doc_type=self.doc_type_code,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes,
        )
        return SilverWriteResult(
            written=True, record_id=manifest.batch_id, batch_id=manifest.batch_id,
        )

    async def _quarantine(
        self, ctx: TenantContext, ingest_id: uuid.UUID, entity_id: uuid.UUID,
        reasons: list[str], ingest_run_id: uuid.UUID,
    ) -> None:
        await self._promoter._quarantine(
            ctx, ingest_id, entity_id, self.doc_type_code,
            ValidationResult(rejections=reasons or ["extraction produced no usable fields"]),
            ingest_run_id,
        )


class RegisterDocumentExtractor(DocumentExtractor):
    """Archetype 2 base for the three NEW register types added by this batch
    (`src/silver/registers/catalog.py`'s 23 existing A1 specs are untouched —
    this is the same `RegisterSpec`/`RegisterLoader` mechanism, applied to
    three more types, not a new one).

    Unlike the point-fact archetypes, a register's `Extracted` outcome
    carries `fields["rows"]: list[dict[str, str]]` — one already-CSV-ready
    dict per parsed table row, in the target `RegisterSpec.column_names`
    order. A concrete subclass overrides `extract()` directly (table parsing
    is genuinely per-type work — a payroll table and an FX-realisation table
    share no columns) rather than supplying a `label_spec`; `to_silver` here
    is the one shared piece: rows -> CSV bytes -> `RegisterLoader.load(...,
    content_format="csv")`, UNCHANGED.

    `entity_gstin` is a fixed class constant, not extracted — none of these
    three specimens carry their own GSTIN as a table column, the same reason
    `RegisterLoader.load`'s `gstin` is a caller-supplied parameter rather
    than a CSV column for every OTHER register (`src/silver/registers/
    loader.py`'s docstring). A real deployment would resolve it from the
    entity's registration record, which does not exist as a lookup in this
    codebase yet — named here as a simplification, not hidden.
    """

    # Redeclared as ClassVar at this level (rather than inheriting
    # `DocumentExtractor.doc_type_code: str` as an instance attribute) so a
    # concrete subclass's `doc_type_code: ClassVar[str] = "..."` overrides a
    # ClassVar with a ClassVar — mypy flags overriding an instance attribute
    # with a class variable, which is exactly what every OTHER archetype base
    # in this module now does on purpose (constructor arg, not ClassVar).
    # This one archetype is the deliberate exception (module docstring).
    doc_type_code: ClassVar[str]  # type: ignore[misc]
    spec: ClassVar[RegisterSpec]
    entity_gstin: ClassVar[str] = "27AABCM4521F1Z5"

    def __init__(self, pool: TenantScopedPool, store: ObjectStorePort | None = None) -> None:
        self._loader = RegisterLoader(pool, self.spec)
        self._promoter = SilverPromotionService(pool)
        self._store = store

    async def to_silver(
        self,
        outcome: ExtractionOutcome,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        pdf_bytes: bytes,
        ingest_run_id: uuid.UUID | None = None,
    ) -> SilverWriteResult:
        ingest_run_id = ingest_run_id or uuid.uuid4()

        if isinstance(outcome, NoTextLayer):
            await self._quarantine(ctx, ingest_id, entity_id, ["NoTextLayer"], ingest_run_id)
            return SilverWriteResult(written=False, reason="NoTextLayer")
        if isinstance(outcome, Partial):
            await self._quarantine(
                ctx, ingest_id, entity_id, _partial_reasons(outcome), ingest_run_id
            )
            return SilverWriteResult(
                written=False,
                reason=f"Partial: missing={outcome.missing} failed={outcome.failed}",
            )

        assert isinstance(outcome, Extracted)  # exhaustiveness check, not a test assertion
        rows = cast("list[dict[str, str]]", outcome.fields["rows"])
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(self.spec.column_names))
        writer.writeheader()
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode("utf-8")

        today = dt.datetime.now(dt.UTC).date()
        try:
            result = await self._loader.load(
                ctx, entity_id=entity_id, gstin=self.entity_gstin, ingest_id=ingest_id,
                data=csv_bytes, period_start=today, period_end=today,
                doc_type_code=self.doc_type_code, ingest_run_id=ingest_run_id,
                content_format="csv",
            )
        except ValidationRejected as exc:
            return SilverWriteResult(written=False, reason=str(exc))
        _put_silver_copy(
            self._store, ctx, doc_type=self.doc_type_code,
            ingest_id=ingest_id, pdf_bytes=pdf_bytes,
        )
        return SilverWriteResult(
            written=True, record_id=result.batch_id, batch_id=result.batch_id,
            reason=f"inserted={result.inserted} unchanged={result.unchanged}"
                   f" superseded={result.superseded} rejected={result.rejected}",
        )

    async def _quarantine(
        self, ctx: TenantContext, ingest_id: uuid.UUID, entity_id: uuid.UUID,
        reasons: list[str], ingest_run_id: uuid.UUID,
    ) -> None:
        await self._promoter._quarantine(
            ctx, ingest_id, entity_id, self.doc_type_code,
            ValidationResult(rejections=reasons or ["extraction produced no usable fields"]),
            ingest_run_id,
        )
