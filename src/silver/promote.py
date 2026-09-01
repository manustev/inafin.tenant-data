"""Bronze -> Silver promotion. Ends Pipeline 1's responsibility.

Writes documents, lines, and the batch manifest in ONE transaction. The manifest
row is what makes the batch visible to Pipeline 2 — so it must not become
visible before the rows it describes. Committing them together is what
guarantees a consumer never sees a READY batch with rows still in flight.

Nothing here writes to Bronze. Promotion and quarantine are both conclusions
drawn from parsing, so they are recorded in Silver: `ingest_batch` carries
`bronze_manifest_ref` back to the artefact, and a refusal lands in
`quarantined_artefact`. Bronze is INSERT-only and holds no opinion about what
we later made of a file.

Kafka publication happens strictly AFTER commit, and is best-effort. If the
broker is down, the batch is already READY in Silver and a polling consumer will
find it. That is the doorbell property (ARCHITECTURE.md 2.1) enforced in code:
a publication failure is logged, never raised.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import cast

from psycopg import sql

from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.silver.contract import FieldContract, parse_contract
from src.silver.validate import ValidationResult, validate_transaction_csv

logger = logging.getLogger(__name__)

#: TYPED-TABLES-PLAN.md 10 step 4. PURCHASE_REGISTER has a typed table
#: (purchase_register, tenant migration 010) and a loader (src/silver/registers)
#: now, so this path must refuse it rather than write a second, orphaned copy
#: into transaction_document. Not a registry column: document_type.archetype
#: cannot carry this — it is required non-NULL whenever in_scope
#: (document_type_scope_ck, shared 003), so it stays permanent classification
#: data and cannot double as a write-path switch. An explicit set here is the
#: honest alternative.
#:
#: Reviewed and extended building the dispatch_mechanism registry column
#: (dispatch item #2): CREDIT_DEBIT_NOTE_REGISTER and
#: HO_COMMON_INPUT_SERVICE_INVOICE are also archetype 1 in the CSV, but both
#: already have a typed table + RegisterSpec entry
#: (`src/silver/registers/catalog.py`) built and DDL-gate-tested in the same
#: "23 Group A1 registers" batch PURCHASE_REGISTER's retirement note refers
#: to — this repo just never added them here. `registry/document_types.csv`'s
#: `dispatch_mechanism=REGISTER_LOADER` for these three is the single source
#: of truth now; this set exists only so a stray direct call into this
#: promotion path can't create a second, orphaned write for a type the
#: register loader already owns.
_RETIRED_ARCHETYPE_1_TYPES: frozenset[str] = frozenset({
    "PURCHASE_REGISTER",
    "CREDIT_DEBIT_NOTE_REGISTER",
    "HO_COMMON_INPUT_SERVICE_INVOICE",
})


@dataclass(frozen=True, slots=True)
class BatchManifest:
    """What Kafka carries. Ids, counts, hashes, a period — no business data.

    ARCHITECTURE.md 2.2: because this is all that goes on the wire, Kafka's
    isolation burden collapses and topic strategy becomes a scaling decision
    rather than a security one.
    """

    slug: str
    batch_id: uuid.UUID
    entity_id: uuid.UUID
    document_type: str
    source_stream: str
    period_start: dt.date
    period_end: dt.date
    row_count: int
    content_hash_hex: str
    bronze_manifest_ref: str
    ready_at: dt.datetime

    def to_json(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "batch_id": str(self.batch_id),
            "entity_id": str(self.entity_id),
            "document_type": self.document_type,
            "source_stream": self.source_stream,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "row_count": self.row_count,
            "content_hash": self.content_hash_hex,
            "bronze_manifest_ref": self.bronze_manifest_ref,
            "ready_at": self.ready_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class DocumentTypeSpec:
    """What the registry says about one document type, read at promotion time."""

    doc_type: str
    stream: str
    contract: FieldContract


class SilverPromotionService:
    def __init__(self, pool: TenantScopedPool) -> None:
        self._pool = pool

    async def _load_spec(self, ctx: TenantContext, document_type: str) -> DocumentTypeSpec:
        """Read the type's contract from the registry.

        Read per promotion rather than cached. A batch is a large unit of work,
        so one indexed lookup is free — and a cache would mean a corrected
        contract taking effect at process-restart time, which is exactly the
        kind of "works on the box that was redeployed" divergence this repo
        already refuses elsewhere.
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            row = await (
                await conn.execute(
                    "SELECT stream, field_contract, archetype"
                    "  FROM platform_ref.document_type"
                    " WHERE doc_type_code = %s AND in_scope",
                    (document_type,),
                )
            ).fetchone()

        if row is None:
            raise ValidationRejected(
                f"{document_type} is not an in-scope document type in the registry"
            )
        stream, raw_contract, archetype = cast("tuple[str, str, int]", row)
        if document_type in _RETIRED_ARCHETYPE_1_TYPES:
            raise ValidationRejected(
                f"{document_type} no longer promotes to transaction_document — "
                f"use src.silver.registers.RegisterLoader"
            )
        if archetype != 1:
            raise ValidationRejected(
                f"{document_type} is archetype {archetype}, not 1 — "
                f"it does not promote to transaction_document"
            )
        return DocumentTypeSpec(
            doc_type=document_type, stream=stream, contract=parse_contract(raw_contract)
        )

    async def promote_transaction_documents(
        self,
        ctx: TenantContext,
        *,
        document_type: str,
        ingest_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: bytes,
        period_start: dt.date,
        period_end: dt.date,
        ingest_run_id: uuid.UUID | None = None,
    ) -> BatchManifest:
        """Promote any archetype-1 artefact from Bronze to Silver.

        `document_type` is a REGISTRY code — PURCHASE_REGISTER, not
        PURCHASE_INVOICE (shared migration 008 settles why). Everything that
        differs between the eleven types is read from the registry here; nothing
        below this line names one.
        """
        silver = ctx.silver_schema
        ingest_run_id = ingest_run_id or uuid.uuid4()
        spec = await self._load_spec(ctx, document_type)

        result = validate_transaction_csv(data, spec.contract)
        if not result.ok:
            await self._quarantine(
                ctx, ingest_id, entity_id, document_type, result, ingest_run_id
            )
            raise ValidationRejected(
                f"{ctx} artefact {ingest_id} rejected: "
                + "; ".join(result.rejections[:5])
                + (f" (+{len(result.rejections) - 5} more)" if len(result.rejections) > 5 else "")
            )

        batch_id = uuid.uuid4()
        content_hash = hashlib.sha256(data).digest()

        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            # Manifest first — the invoices FK to it. Both commit together, so
            # a consumer cannot observe a READY batch with rows still missing.
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.ingest_batch (
                        batch_id, entity_id, document_type, source_stream,
                        period_start, period_end, row_count, content_hash,
                        bronze_manifest_ref, status, ingest_run_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              'READY', %s)
                    """
                ).format(sql.Identifier(silver)),
                (batch_id, entity_id, spec.doc_type, spec.stream,
                 period_start, period_end,
                 result.row_count, content_hash, str(ingest_id), ingest_run_id),
            )

            for doc in result.documents:
                # SUPERSEDE, not blind insert — the ERP upload E2E suite's
                # GTA_INVOICE_CONSIGNMENT_NOTE finding (2026-09-01): this loop
                # always inserted unconditionally, so a second ingest of the
                # SAME document had to violate `transaction_document_natural_
                # key_uq` (tenant migration 007). The bug affects every
                # archetype-1 type, CSV or PDF, equally — only the PDF path
                # (BILL_OF_ENTRY, GTA_INVOICE_CONSIGNMENT_NOTE) had ever
                # actually been exercised twice with the same natural key,
                # because it only became REACHABLE once shared migration 043
                # corrected the catalogue's routing.
                #
                # Keyed on exactly `transaction_document_natural_key_uq`'s
                # columns, in the index's own expression — the
                # `src/silver/supersede.py` rule (a lookup that disagrees
                # with the index does not prevent a duplicate, it just moves
                # where one appears) applied inline rather than through that
                # module's `close_current`, because the index's key is a
                # `COALESCE(counterparty_gstin, '')` EXPRESSION, not a bare
                # column, and `close_current`'s key mapping only compares
                # plain columns.
                prior_row = await (
                    await conn.execute(
                        sql.SQL(
                            "SELECT doc_id FROM {}.transaction_document"
                            " WHERE entity_id = %s AND doc_type = %s"
                            "   AND COALESCE(counterparty_gstin, '')"
                            "     = COALESCE(%s, '')"
                            "   AND doc_number = %s"
                            "   AND superseded_at IS NULL"
                            " FOR UPDATE"
                        ).format(sql.Identifier(silver)),
                        (entity_id, spec.doc_type, doc.counterparty_gstin,
                         doc.doc_number),
                    )
                ).fetchone()
                prior_doc_id = cast("uuid.UUID | None", prior_row[0]) if prior_row else None

                if prior_doc_id is not None:
                    await conn.execute(
                        sql.SQL(
                            "UPDATE {}.transaction_document"
                            " SET superseded_at = now()"
                            " WHERE doc_id = %s AND superseded_at IS NULL"
                        ).format(sql.Identifier(silver)),
                        (prior_doc_id,),
                    )

                doc_id = uuid.uuid4()
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.transaction_document (
                            doc_id, batch_id, entity_id, doc_type, direction,
                            doc_number, doc_date, counterparty_gstin,
                            counterparty_name, total_taxable_value,
                            total_tax_value, total_value, currency, attributes,
                            valid_from, bronze_ingest_id, supersedes_doc_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                  %s, %s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(silver)),
                    (doc_id, batch_id, entity_id, spec.doc_type,
                     spec.contract.direction.value,
                     doc.doc_number, doc.doc_date, doc.counterparty_gstin,
                     doc.counterparty_name, doc.total_taxable_value,
                     doc.total_tax_value, doc.total_value, doc.currency,
                     json.dumps(doc.attributes),
                     doc.doc_date, ingest_id, prior_doc_id),
                )
                for line in doc.lines:
                    await conn.execute(
                        sql.SQL(
                            """
                            INSERT INTO {}.transaction_line (
                                line_id, doc_id, line_number, hsn_sac,
                                description, quantity, unit_of_measure, unit_price,
                                taxable_value, gst_rate, cgst_amount, sgst_amount,
                                igst_amount, cess_amount, itc_amount, attributes
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                      %s, %s, %s, %s, %s)
                            """
                        ).format(sql.Identifier(silver)),
                        (uuid.uuid4(), doc_id, line.line_number, line.hsn_sac,
                         line.description, line.quantity, line.unit_of_measure,
                         line.unit_price, line.taxable_value, line.gst_rate,
                         line.cgst_amount, line.sgst_amount, line.igst_amount,
                         line.cess_amount, line.itc_amount,
                         json.dumps(line.attributes)),
                    )

            # Nothing is written back to Bronze. `bronze_manifest_ref` on the
            # batch above already records the link, and it is the direction that
            # does not require mutating the evidentiary record.
            row = await (
                await conn.execute(
                    sql.SQL("SELECT ready_at FROM {}.ingest_batch WHERE batch_id = %s")
                    .format(sql.Identifier(silver)),
                    (batch_id,),
                )
            ).fetchone()
            ready_at = cast(dt.datetime, row[0]) if row else dt.datetime.now(dt.UTC)

        logger.info(
            "promoted %s -> batch %s (%d rows) for %s",
            ingest_id, batch_id, result.row_count, ctx,
        )
        return BatchManifest(
            slug=ctx.slug, batch_id=batch_id, entity_id=entity_id,
            document_type=spec.doc_type, source_stream=spec.stream,
            period_start=period_start, period_end=period_end,
            row_count=result.row_count, content_hash_hex=content_hash.hex(),
            bronze_manifest_ref=str(ingest_id), ready_at=ready_at,
        )

    async def _quarantine(
        self,
        ctx: TenantContext,
        ingest_id: uuid.UUID,
        entity_id: uuid.UUID,
        document_type: str,
        result: ValidationResult,
        ingest_run_id: uuid.UUID,
    ) -> None:
        """Quarantine, never delete.

        The artefact stays in Bronze under Object Lock, untouched. The verdict
        is written to SILVER: refusing a file is a conclusion drawn from parsing
        it, and Bronze records only what arrived. A rejected file is still
        evidence of what the client sent, and in Forensic Mode that record is
        itself submissible — which is exactly why our opinion of it must not be
        stored on it.

        Re-attempting after a parser fix replaces the verdict (the artefact is
        immutable and identical, so a history of our own retries belongs in
        logs, not in the tenant's data).
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.quarantined_artefact (
                        ingest_id, entity_id, document_type, reason, ingest_run_id
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (ingest_id) DO UPDATE
                       SET reason        = EXCLUDED.reason,
                           rejected_at   = now(),
                           ingest_run_id = EXCLUDED.ingest_run_id
                    """
                ).format(sql.Identifier(ctx.silver_schema)),
                (ingest_id, entity_id, document_type,
                 "; ".join(result.rejections[:20]), ingest_run_id),
            )
        logger.warning(
            "quarantined %s for %s: %d validation error(s)",
            ingest_id, ctx, len(result.rejections),
        )
