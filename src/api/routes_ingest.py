"""REST routes — upload, trigger, status.

Every handler takes `TenantDep` and nothing else names the tenant: the slug
comes only from the resolved auth context, never from a path parameter,
query string, or request body (ARCHITECTURE.md 5.6). `entity_id` IS a request
parameter — it is business data, the customer's customer
(TYPED-TABLES-PLAN.md 2), not tenant identity.
"""

from __future__ import annotations

import uuid

import psycopg
from fastapi import APIRouter, Form, HTTPException, UploadFile

from src.api.deps import (
    BatchPublisherDep,
    BronzeServiceDep,
    PdfTextReaderDep,
    PoolDep,
    SilverReaderDep,
    StoreDep,
    TenantDep,
)
from src.api.schemas import (
    BatchUploadItem,
    BatchUploadResponse,
    RowRejectionDetail,
    StatusResponse,
    TriggerRequest,
    TriggerResponse,
    UploadResponse,
)
from src.core.errors import IntakeRejected
from src.dispatch.trigger import record_and_dispatch_trigger

router = APIRouter(prefix="/artefacts", tags=["ingest"])

#: A batch call is one multipart request holding N independent uploads. Bounded
#: so one request cannot hold the connection open indefinitely or let a caller
#: use this route to bypass ordinary rate limiting one request at a time — not
#: because any single file is more expensive here than through the singular
#: route, which has no cap of its own beyond `check_file`'s per-file size limit.
MAX_BATCH_FILES = 100


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_artefact(
    tenant: TenantDep,
    bronze: BronzeServiceDep,
    file: UploadFile,
    entity_id: uuid.UUID = Form(...),
    document_type: str = Form(...),
) -> UploadResponse:
    """Runs the real intake gate (`BronzeIngestionService.receive`) — file-check,
    document-type check, hash, dedup, virus scan, PUT, ledger INSERT. Not a stub.

    `document_type` has no default — see `BronzeIngestionService.receive`'s
    docstring for why a silently-defaulted, unvalidated value was itself the
    bug this closed. A caller must declare what they are sending; FastAPI
    turns a missing field into a 422 before this handler body ever runs.

    `received_from="PORTAL"` names this specific route as the source, distinct
    from the default `BronzeIngestionService.receive` otherwise applies. This
    is the only provenance the ledger records for how an artefact arrived —
    worth keeping accurate now that a second door (a source connector pulling
    from GSTN/ICEGATE/etc, `src/connectors/`) exists and will write through
    the same service with its own `received_from` value once it has a caller.
    """
    data = await file.read()
    try:
        receipt = await bronze.receive(
            tenant,
            entity_id=entity_id,
            data=data,
            document_type=document_type,
            filename=file.filename,
            received_from="PORTAL",
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


@router.post("/batch", response_model=BatchUploadResponse, status_code=200)
async def upload_artefact_batch(
    tenant: TenantDep,
    bronze: BronzeServiceDep,
    files: list[UploadFile],
    entity_id: uuid.UUID = Form(...),
    document_type: str = Form(...),
) -> BatchUploadResponse:
    """Upload several files in one request, all the SAME `document_type` and
    `entity_id` — a tenant dropping twelve months of one register export, or
    a batch of BILL_OF_ENTRY PDFs, is the ordinary shape this exists for.

    A MIXED-TYPE batch is deliberately not this route's job: `document_type`
    is a single form field, not a parallel array keyed to `files`, because a
    parallel-array multipart shape is exactly the kind of API surface that
    silently misaligns (file 3 gets file 4's type) with nothing but manual
    testing to catch it. A portal batching several document TYPES makes
    several calls to this route, one per type — each one still gets its own
    independent per-file outcome below.

    EVERY FILE IS INDEPENDENT. One bad file (wrong extension, a virus hit, an
    out-of-scope `document_type`) does not fail the request or the files
    around it — `BronzeIngestionService.receive` already treats each upload
    as its own unit of work, and this route does the same at the batch grain:
    always 200, `items` carries the real per-file outcome. That is the same
    "the body carries the outcome" shape `dispatch_load`'s PARTIAL status
    already uses for a register upload where some ROWS succeed and some fail
    — this is that principle one level up, at file grain instead of row grain.
    """
    if not files:
        raise HTTPException(status_code=422, detail="no files in batch")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"batch of {len(files)} files exceeds the {MAX_BATCH_FILES}-file limit",
        )

    items: list[BatchUploadItem] = []
    for file in files:
        data = await file.read()
        try:
            receipt = await bronze.receive(
                tenant,
                entity_id=entity_id,
                data=data,
                document_type=document_type,
                filename=file.filename,
                received_from="PORTAL",
            )
        except IntakeRejected as exc:
            items.append(BatchUploadItem(filename=file.filename, ok=False, error=str(exc)))
            continue
        items.append(
            BatchUploadItem(
                filename=file.filename,
                ok=True,
                ingest_id=receipt.ingest_id,
                bucket=receipt.bucket,
                object_key=receipt.object_key,
                size_bytes=receipt.size_bytes,
                deduplicated=receipt.deduplicated,
            )
        )

    accepted = sum(1 for i in items if i.ok)
    return BatchUploadResponse(
        accepted_count=accepted, rejected_count=len(items) - accepted, items=items,
    )


@router.post("/{ingest_id}/trigger", response_model=TriggerResponse, status_code=202)
async def trigger_load(
    ingest_id: uuid.UUID, body: TriggerRequest, tenant: TenantDep, pool: PoolDep,
    bronze: BronzeServiceDep, store: StoreDep, pdf_text_reader: PdfTextReaderDep,
    batch_publisher: BatchPublisherDep,
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
    try:
        result = await record_and_dispatch_trigger(
            pool, tenant, bronze,
            ingest_id=ingest_id, doc_type_code=body.doc_type_code,
            period_start=body.period_start, period_end=body.period_end, gstin=body.gstin,
            store=store, reader=pdf_text_reader, publisher=batch_publisher,
        )
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(
            status_code=404, detail=f"no artefact {ingest_id} for this tenant"
        ) from exc
    except ValueError as exc:
        # Covers both MissingDispatchFieldError and infer_content_format's
        # plain ValueError (an unrecognised extension) — both are the caller
        # having asked for something this trigger cannot satisfy, same 422
        # this route always gave each. UnknownExtractorError is also a
        # ValueError subclass (src/extraction/dispatch.py), so it lands here
        # too rather than needing its own except clause.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return TriggerResponse(
        trigger_id=result.trigger_id, ingest_id=result.ingest_id,
        doc_type_code=result.doc_type_code, requested_at=result.requested_at,
        status=result.status, mechanism=result.mechanism, batch_id=result.batch_id,
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
