"""SilverReader — the v2-facing library. Pipeline 2's entire access to Silver.

This is what `inafinplatform/v2` imports. It lands behind v2's existing
`StoragePort`/`RetrievalPort` protocols, which already take `tenant_id` on every
method, so the reconciliation core never learns that its data source changed.

`artefact_outcome` is the one method here that is not reconciliation-core
traffic — it answers the customer-facing "did my upload succeed" question a
support surface or status endpoint would ask. It lives in this class anyway
rather than a separate reader, because the answer is a read against the same
v1_ views and the same `Role.RECON` boundary as everything else here; a
second reader class would just be this isolation model, duplicated.

Two mechanics carry the design of the batch-polling methods, and both are
easy to get subtly wrong:

**The overlap window.** A naive `ready_at > watermark` cursor silently skips
batches. A transaction can take its `ready_at` default early and commit late, so
it becomes visible *after* the watermark has already advanced past its
timestamp. Nothing errors; the batch is simply never seen again. The fix is to
re-read a safety window on every poll and discard what was already done — which
only works because dedupe is exact.

**The dedupe key.** Not `batch_id` alone. Re-running a batch after a rule change
is a legitimate new execution, not a duplicate, so the key is
``(batch_id, rule_catalog_version, corpus_version, tenant_pack_version)``. That
makes retries safe, makes backfill an ordinary operation rather than a special
tool, and makes "which corpus version produced this finding" answerable — the
question a CA is asked in a hearing.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import cast

from psycopg import sql

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext

logger = logging.getLogger(__name__)

DEFAULT_OVERLAP = dt.timedelta(hours=1)
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


@dataclass(frozen=True, slots=True)
class EngineVersions:
    """The version tuple an execution is pinned to.

    Mirrors the effective-dated configuration already in v2
    (`domain/rule_catalog.py`, `domain/tenant_pack.py`). Bumping any component
    makes a re-run a new, legitimate execution rather than a duplicate.
    """

    rule_catalog_version: str
    corpus_version: str
    tenant_pack_version: str


@dataclass(frozen=True, slots=True)
class BatchRef:
    batch_id: uuid.UUID
    entity_id: uuid.UUID
    document_type: str
    source_stream: str
    period_start: dt.date
    period_end: dt.date
    row_count: int
    ready_at: dt.datetime
    bronze_manifest_ref: str


@dataclass(frozen=True, slots=True)
class InvoiceRow:
    invoice_id: uuid.UUID
    invoice_number: str
    supplier_gstin: str
    invoice_date: dt.date
    total_taxable_value: object
    total_tax_value: object
    total_value: object
    bronze_ingest_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RowRejectionDetail:
    """One reason a single row, within an otherwise-accepted upload, did not
    land. Mirrors `{{silver}}.rejected_row` — see that table's docstring for
    why row-level rejection exists at all (row 1 of tenant migration 012)."""

    source_line: int
    column_name: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ArtefactOutcome:
    """The answer to "did my upload succeed" for one Bronze artefact.

    `status` is one of four values, in the order a caller should check them —
    each corresponds to a distinct place in the pipeline the artefact could
    have stopped:

        PENDING     — received into Bronze, not yet promoted or quarantined.
                      Either still queued, or promotion has not run yet.
        QUARANTINED — the file itself could not be parsed at all (a missing
                      required column, unreadable encoding, zero data rows).
                      No batch exists; `quarantine_reason` explains why.
        ACCEPTED    — every row promoted; `rejected_count` is 0.
        PARTIAL     — the file parsed and a batch exists, but one or more
                      rows were dropped; `rejections` lists each one.

    Deliberately NOT a boolean success/failure: PARTIAL is the outcome the
    row-level rejection redesign exists to make representable at all — a
    500-row file with 3 bad rows is neither a clean success nor a total
    failure, and collapsing it to one or the other is the exact loss of
    information a customer-facing report must not repeat.
    """

    bronze_ingest_id: uuid.UUID
    status: str
    document_type: str | None
    batch_id: uuid.UUID | None
    row_count: int | None
    """Rows the source file actually contained — `accepted_count +
    rejected_count`. NOT `ingest_batch.row_count`: that column already means
    "rows accepted into the batch" (see RegisterLoader.load and
    RegisterOutcome.row_count), so this field derives the total by adding the
    rejections back rather than reusing that name for a different quantity."""
    accepted_count: int | None
    rejected_count: int
    quarantine_reason: str | None
    rejections: tuple[RowRejectionDetail, ...]


class SilverReader:
    def __init__(self, pool: TenantScopedPool, *, consumer_name: str = "v2-recon") -> None:
        self._pool = pool
        self._consumer = consumer_name

    async def pending_batches(
        self,
        ctx: TenantContext,
        versions: EngineVersions,
        *,
        # A REGISTRY code. Shared migration 008 retired the row-type vocabulary
        # this used to default to ('PURCHASE_INVOICE'), and a stale default here
        # would be invisible: pending_batches would simply return nothing and
        # Pipeline 2 would look idle rather than broken.
        document_type: str = "PURCHASE_REGISTER",
        limit: int = 100,
        overlap: dt.timedelta = DEFAULT_OVERLAP,
    ) -> list[BatchRef]:
        """Batches that are READY and not yet executed under `versions`.

        Discovers work from Silver, not from Kafka. A Kafka message only makes
        this run sooner — so a purged topic, a broker outage, or a consumer that
        was down for a week all cost latency and nothing else.
        """
        async with self._pool.transaction(ctx, Role.RECON) as conn:
            wm_row = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT last_ready_at FROM {}.consumer_watermark"
                        " WHERE consumer_name = %s AND document_type = %s"
                    ).format(sql.Identifier(ctx.gold_schema)),
                    (self._consumer, document_type),
                )
            ).fetchone()
            watermark = cast(dt.datetime, wm_row[0]) if wm_row else EPOCH

            rows = await (
                await conn.execute(
                    sql.SQL(
                        """
                        SELECT b.batch_id, b.entity_id, b.document_type,
                               b.source_stream, b.period_start, b.period_end,
                               b.row_count, b.ready_at, b.bronze_manifest_ref
                        FROM {silver}.v1_ingest_batch b
                        WHERE b.status = 'READY'
                          AND b.document_type = %s
                          AND b.ready_at > %s
                          AND NOT EXISTS (
                              SELECT 1 FROM {gold}.batch_execution e
                              WHERE e.batch_id = b.batch_id
                                AND e.rule_catalog_version = %s
                                AND e.corpus_version = %s
                                AND e.tenant_pack_version = %s
                          )
                        ORDER BY b.ready_at
                        LIMIT %s
                        """
                    ).format(
                        silver=sql.Identifier(ctx.silver_schema),
                        gold=sql.Identifier(ctx.gold_schema),
                    ),
                    (document_type, watermark - overlap,
                     versions.rule_catalog_version, versions.corpus_version,
                     versions.tenant_pack_version, limit),
                )
            ).fetchall()

        # Cast at the DB boundary: psycopg returns untyped rows, and the shape
        # asserted here is exactly the SELECT list above.
        typed = cast(
            "list[tuple[uuid.UUID, uuid.UUID, str, str, dt.date, dt.date, int, "
            "dt.datetime, str]]",
            rows,
        )
        return [BatchRef(*r) for r in typed]

    async def read_invoices(
        self, ctx: TenantContext, batch_id: uuid.UUID
    ) -> list[InvoiceRow]:
        """Read one archetype-1 batch's documents, generically.

        Sourced from v1_transaction_document, not v1_purchase_invoice.
        v1_purchase_invoice moved off transaction_document onto purchase_register
        (TYPED-TABLES-PLAN.md 10 step 4) and now serves PURCHASE_REGISTER only —
        it would silently return nothing for the other 10 archetype-1 types.
        v1_transaction_document still covers all of them, doc_type-agnostic,
        which is what a batch-scoped read (already pinned to one document_type
        via `ingest_batch`) should be. InvoiceRow's shape is unchanged; only the
        source view is.
        """
        async with self._pool.transaction(ctx, Role.RECON) as conn:
            rows = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT doc_id, doc_number, counterparty_gstin,"
                        "       doc_date, total_taxable_value, total_tax_value,"
                        "       total_value, bronze_ingest_id"
                        "  FROM {}.v1_transaction_document"
                        " WHERE batch_id = %s AND superseded_at IS NULL"
                        " ORDER BY doc_number"
                    ).format(sql.Identifier(ctx.silver_schema)),
                    (batch_id,),
                )
            ).fetchall()
        typed = cast(
            "list[tuple[uuid.UUID, str, str, dt.date, object, object, object, uuid.UUID]]",
            rows,
        )
        return [InvoiceRow(*r) for r in typed]

    async def artefact_outcome(
        self, ctx: TenantContext, bronze_ingest_id: uuid.UUID
    ) -> ArtefactOutcome:
        """Answer "did this upload succeed" for one Bronze artefact.

        Checked in the order an artefact actually moves through the pipeline:
        quarantine (file-level, no batch was ever created) before batch
        lookup (file-level success, row-level detail). This is a read against
        the same v1_ views Pipeline 2 uses (see this module's docstring) —
        recon holds SELECT on those and nothing else in Silver, so a
        support/reporting surface built on this method inherits the same
        isolation boundary rather than needing a new one.

        `ingest_batch.bronze_manifest_ref` is a text column holding the
        artefact's `ingest_id` as a string, not a typed FK (tenant migration
        005 explains why: an FK would make Bronze undroppable independently
        of Silver) — hence the explicit cast on that lookup.
        """
        silver = ctx.silver_schema
        async with self._pool.transaction(ctx, Role.RECON) as conn:
            quarantine_row = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT reason FROM {}.v1_quarantined_artefact WHERE ingest_id = %s"
                    ).format(sql.Identifier(silver)),
                    (bronze_ingest_id,),
                )
            ).fetchone()
            if quarantine_row is not None:
                reason = cast("tuple[str]", quarantine_row)[0]
                return ArtefactOutcome(
                    bronze_ingest_id=bronze_ingest_id, status="QUARANTINED",
                    document_type=None, batch_id=None, row_count=None,
                    accepted_count=None, rejected_count=0,
                    quarantine_reason=reason, rejections=(),
                )

            batch_row = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT batch_id, document_type, row_count"
                        " FROM {}.v1_ingest_batch WHERE bronze_manifest_ref = %s"
                    ).format(sql.Identifier(silver)),
                    (str(bronze_ingest_id),),
                )
            ).fetchone()
            if batch_row is None:
                return ArtefactOutcome(
                    bronze_ingest_id=bronze_ingest_id, status="PENDING",
                    document_type=None, batch_id=None, row_count=None,
                    accepted_count=None, rejected_count=0,
                    quarantine_reason=None, rejections=(),
                )
            # accepted_count, not row_count — see RowRejectionDetail note above.
            batch_id, document_type, accepted_count = cast(
                "tuple[uuid.UUID, str, int]", batch_row
            )

            rejection_rows = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT source_line, column_name, message"
                        " FROM {}.v1_rejected_row"
                        " WHERE bronze_ingest_id = %s ORDER BY source_line"
                    ).format(sql.Identifier(silver)),
                    (bronze_ingest_id,),
                )
            ).fetchall()

        typed_rejections = cast("list[tuple[int, str | None, str]]", rejection_rows)
        rejections = tuple(RowRejectionDetail(*r) for r in typed_rejections)
        rejected_count = len(rejections)

        return ArtefactOutcome(
            bronze_ingest_id=bronze_ingest_id,
            status="ACCEPTED" if rejected_count == 0 else "PARTIAL",
            document_type=document_type, batch_id=batch_id,
            row_count=accepted_count + rejected_count, accepted_count=accepted_count,
            rejected_count=rejected_count,
            quarantine_reason=None, rejections=rejections,
        )

    async def record_execution(
        self,
        ctx: TenantContext,
        batch: BatchRef,
        versions: EngineVersions,
        *,
        facts: list[tuple[uuid.UUID, str, str, str, str, uuid.UUID]] | None = None,
    ) -> bool:
        """Record the execution and its facts atomically. False if already done.

        Idempotent by primary key rather than by a prior existence check: two
        workers that both claimed the same batch cannot both write facts,
        because the loser's INSERT conflicts and its whole transaction — facts
        included — rolls back.
        """
        async with self._pool.transaction(ctx, Role.RECON) as conn:
            inserted = await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.batch_execution (
                        batch_id, rule_catalog_version, corpus_version,
                        tenant_pack_version, row_count
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """
                ).format(sql.Identifier(ctx.gold_schema)),
                (batch.batch_id, versions.rule_catalog_version, versions.corpus_version,
                 versions.tenant_pack_version, batch.row_count),
            )
            if inserted.rowcount == 0:
                return False

            for fact in facts or []:
                invoice_id, rule_id, fact_type, severity, detail, bronze_ingest_id = fact
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.fact_record (
                            fact_id, batch_id, invoice_id, rule_id, fact_type,
                            severity, detail, bronze_ingest_id,
                            rule_catalog_version, corpus_version
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(ctx.gold_schema)),
                    (uuid.uuid4(), batch.batch_id, invoice_id, rule_id, fact_type,
                     severity, detail, bronze_ingest_id,
                     versions.rule_catalog_version, versions.corpus_version),
                )

            # Watermark advances only on success, and only forward. GREATEST
            # keeps an out-of-order completion from rewinding it.
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.consumer_watermark (
                        consumer_name, document_type, last_ready_at, last_batch_id
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (consumer_name, document_type) DO UPDATE
                       SET last_ready_at = GREATEST(
                               consumer_watermark.last_ready_at, EXCLUDED.last_ready_at),
                           last_batch_id = EXCLUDED.last_batch_id,
                           updated_at    = now()
                    """
                ).format(sql.Identifier(ctx.gold_schema)),
                (self._consumer, batch.document_type, batch.ready_at, batch.batch_id),
            )
        return True
