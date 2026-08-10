"""REST routes — upload, trigger (stub), status.

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
from psycopg import sql

from src.api.deps import BronzeServiceDep, PoolDep, SilverReaderDep, TenantDep
from src.api.schemas import (
    RowRejectionDetail,
    StatusResponse,
    TriggerRequest,
    TriggerResponse,
    UploadResponse,
)
from src.core.errors import IntakeRejected
from src.core.tenant import Role

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
    ingest_id: uuid.UUID, body: TriggerRequest, tenant: TenantDep, pool: PoolDep
) -> TriggerResponse:
    """STUB. Records that a load was requested; does not run one.

    There is no doc_type_code -> loader dispatcher yet (register spec vs
    promote.py's archetype-1 path vs SalesRegisterLoader vs a future
    entitlement path). See migrations/tenant/015_load_trigger.sql and
    TODO.md. Foreign-keys against artefact_ledger, so this 404s for an
    ingest_id this tenant never uploaded — the same isolation guarantee
    every other write in this codebase gets from GRANT, not from a WHERE
    clause the caller could get wrong.
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
    trigger_id, resolved_ingest_id, doc_type_code, requested_at = row
    return TriggerResponse(
        trigger_id=trigger_id,
        ingest_id=resolved_ingest_id,
        doc_type_code=doc_type_code,
        requested_at=requested_at,
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
