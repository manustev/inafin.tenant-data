"""REST routes — upload, trigger, status.

Every handler takes `TenantDep` and nothing else names the tenant: the slug
comes only from the resolved auth context, never from a path parameter,
query string, or request body (ARCHITECTURE.md 5.6). `entity_id` IS a request
parameter — it is business data, the customer's customer
(TYPED-TABLES-PLAN.md 2), not tenant identity.
"""

from __future__ import annotations

import uuid
from typing import cast

import psycopg
from fastapi import APIRouter, Form, HTTPException, UploadFile
from psycopg import sql

from src.api.deps import (
    BronzeServiceDep,
    PdfTextReaderDep,
    PoolDep,
    SilverReaderDep,
    StoreDep,
    TenantDep,
)
from src.api.schemas import (
    RowRejectionDetail,
    StatusResponse,
    TriggerRequest,
    TriggerResponse,
    UploadResponse,
)
from src.core.errors import IntakeRejected, ValidationRejected
from src.core.tenant import Role
from src.dispatch.content_format import infer_content_format
from src.dispatch.router import (
    MissingDispatchFieldError,
    NoDispatchMechanismError,
    dispatch_load,
)
from src.extraction.dispatch import UnknownExtractorError

router = APIRouter(prefix="/artefacts", tags=["ingest"])


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_artefact(
    tenant: TenantDep,
    bronze: BronzeServiceDep,
    file: UploadFile,
    entity_id: uuid.UUID = Form(...),
    document_type: str = Form("PURCHASE_INVOICE"),
) -> UploadResponse:
    """Runs the real intake gate (`BronzeIngestionService.receive`) — file-check,
    hash, dedup, virus scan, PUT, ledger INSERT. Not a stub."""
    data = await file.read()
    try:
        receipt = await bronze.receive(
            tenant,
            entity_id=entity_id,
            data=data,
            document_type=document_type,
            filename=file.filename,
        )
    except IntakeRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return UploadResponse(
        ingest_id=receipt.ingest_id,
        bucket=receipt.bucket,
        object_key=receipt.object_key,
        size_bytes=receipt.size_bytes,
        deduplicated=receipt.deduplicated,
    )


@router.post("/{ingest_id}/trigger", response_model=TriggerResponse, status_code=202)
async def trigger_load(
    ingest_id: uuid.UUID, body: TriggerRequest, tenant: TenantDep, pool: PoolDep,
    bronze: BronzeServiceDep, store: StoreDep, pdf_text_reader: PdfTextReaderDep,
) -> TriggerResponse:
    """Record the trigger, then dispatch it synchronously, in-process.

    `load_trigger` (migrations/tenant/015) is still written first and
    unconditionally — it is the durable record that a trigger was requested,
    independent of whether dispatch succeeds. Foreign-keys against
    artefact_ledger, so this 404s for an ingest_id this tenant never
    uploaded — the same isolation guarantee every other write in this
    codebase gets from GRANT, not from a WHERE clause the caller could get
    wrong.

    Dispatch itself is `src/dispatch/router.py`'s `dispatch_load` — see
    `src/api/app.py`'s docstring for why calling it here does not reopen
    "the upsert must NOT go behind an API".
    """
    async with pool.transaction(tenant, Role.INGEST) as conn:
        try:
            row = await (
                await conn.execute(
                    sql.SQL(
                        "INSERT INTO {}.load_trigger (ingest_id, doc_type_code)"
                        " VALUES (%s, %s) RETURNING id, ingest_id, doc_type_code, requested_at"
                    ).format(sql.Identifier(tenant.bronze_schema)),
                    (ingest_id, body.doc_type_code),
                )
            ).fetchone()
        except psycopg.errors.ForeignKeyViolation as exc:
            raise HTTPException(
                status_code=404, detail=f"no artefact {ingest_id} for this tenant"
            ) from exc

    assert row is not None
    trigger_id, resolved_ingest_id, raw_doc_type_code, requested_at = row
    doc_type_code = cast(str, raw_doc_type_code)

    entry = await bronze.ledger_entry(tenant, ingest_id)

    def _recorded_only(status: str) -> TriggerResponse:
        return TriggerResponse(
            trigger_id=trigger_id, ingest_id=resolved_ingest_id, doc_type_code=doc_type_code,
            requested_at=requested_at, status=status,
        )

    try:
        content_format = infer_content_format(entry.original_filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    data = await bronze.fetch(tenant, ingest_id)
    try:
        outcome = await dispatch_load(
            tenant, pool,
            ingest_id=ingest_id, entity_id=entry.entity_id, doc_type_code=doc_type_code,
            data=data, content_format=content_format,
            period_start=body.period_start, period_end=body.period_end, gstin=body.gstin,
            store=store, reader=pdf_text_reader,
        )
    except NoDispatchMechanismError:
        # A real, honest outcome, not an error: this doc_type_code has no
        # dispatch_mechanism yet (unbuilt loader, or a Stream A polled type
        # that was never meant to be upload-triggered). The trigger is still
        # durably recorded above.
        return _recorded_only("UNROUTED")
    except MissingDispatchFieldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnknownExtractorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValidationRejected:
        return _recorded_only("QUARANTINED")

    return TriggerResponse(
        trigger_id=trigger_id, ingest_id=resolved_ingest_id, doc_type_code=doc_type_code,
        requested_at=requested_at, status=outcome.status, mechanism=outcome.mechanism,
        batch_id=outcome.batch_id,
    )


@router.get("/{ingest_id}/status", response_model=StatusResponse)
async def artefact_status(
    ingest_id: uuid.UUID, tenant: TenantDep, reader: SilverReaderDep
) -> StatusResponse:
    """Wraps `SilverReader.artefact_outcome` — the customer-facing
    "did my upload succeed" query, unchanged from the library form."""
    outcome = await reader.artefact_outcome(tenant, ingest_id)
    return StatusResponse(
        bronze_ingest_id=outcome.bronze_ingest_id,
        status=outcome.status,
        document_type=outcome.document_type,
        batch_id=outcome.batch_id,
        row_count=outcome.row_count,
        accepted_count=outcome.accepted_count,
        rejected_count=outcome.rejected_count,
        quarantine_reason=outcome.quarantine_reason,
        rejections=[
            RowRejectionDetail(
                source_line=r.source_line, column_name=r.column_name, message=r.message
            )
            for r in outcome.rejections
        ],
    )
