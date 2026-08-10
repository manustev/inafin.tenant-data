"""GraphQL read surface — the "reader role" over `v1_` views.

Wraps `SilverReader`/`EntitlementReader` exactly as they exist for Pipeline 2;
no new read queries are invented here beyond what those classes already
expose. Both already run under `Role.RECON`, which holds SELECT on `v1_`
views only and nothing else in Silver (`migrations/shared/001_app.sql` step
5) — that grant shape IS "the reader role" the agreed ingestion-surface
design calls for, so no new Postgres role was needed for this layer.

Tenant resolution reuses `src.api.auth`, via `Context` built by
`context_getter` in `src/api/app.py` — REST and GraphQL share one auth path,
so there is exactly one place a request becomes a `TenantContext`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import strawberry
from fastapi import HTTPException, Request
from strawberry.fastapi import BaseContext

from src.core.errors import AuthenticationError
from src.core.tenant import TenantContext
from src.reader.entitlement_reader import EntitlementReader
from src.reader.silver_reader import SilverReader


@dataclass
class Context(BaseContext):
    tenant: TenantContext
    silver_reader: SilverReader
    entitlement_reader: EntitlementReader


async def context_getter(request: Request) -> Context:
    """Same auth path as REST (`src.api.deps.get_tenant`) — one place a
    request becomes a tenant. `context_getter` is a FastAPI dependency under
    strawberry's FastAPI integration, so an `HTTPException` here is handled
    exactly like it would be from a normal route dependency (401, not a
    GraphQL-shaped error) — the request never reaches a resolver."""
    try:
        tenant = request.app.state.auth.resolve(request)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return Context(
        tenant=tenant,
        silver_reader=request.app.state.silver_reader,
        entitlement_reader=request.app.state.entitlement_reader,
    )


@strawberry.type
class RowRejection:
    source_line: int
    column_name: str | None
    message: str


@strawberry.type
class ArtefactOutcome:
    bronze_ingest_id: uuid.UUID
    status: str
    document_type: str | None
    batch_id: uuid.UUID | None
    row_count: int | None
    accepted_count: int | None
    rejected_count: int
    quarantine_reason: str | None
    rejections: list[RowRejection]


@strawberry.type
class Instrument:
    instrument_id: uuid.UUID
    instrument_type: str
    issuing_authority: str
    instrument_number: str
    valid_from: dt.date
    valid_to: dt.date
    status: str
    scope_hsn: list[str] | None
    bronze_ingest_id: uuid.UUID


@strawberry.type
class Query:
    @strawberry.field
    async def artefact_outcome(
        self, info: strawberry.Info[Context], bronze_ingest_id: uuid.UUID
    ) -> ArtefactOutcome:
        ctx = info.context
        outcome = await ctx.silver_reader.artefact_outcome(ctx.tenant, bronze_ingest_id)
        return ArtefactOutcome(
            bronze_ingest_id=outcome.bronze_ingest_id,
            status=outcome.status,
            document_type=outcome.document_type,
            batch_id=outcome.batch_id,
            row_count=outcome.row_count,
            accepted_count=outcome.accepted_count,
            rejected_count=outcome.rejected_count,
            quarantine_reason=outcome.quarantine_reason,
            rejections=[
                RowRejection(
                    source_line=r.source_line, column_name=r.column_name, message=r.message
                )
                for r in outcome.rejections
            ],
        )

    @strawberry.field
    async def entitlement(
        self,
        info: strawberry.Info[Context],
        entity_id: uuid.UUID,
        instrument_type: str,
        as_of: dt.date,
        hsn: str | None = None,
    ) -> list[Instrument]:
        ctx = info.context
        found = await ctx.entitlement_reader.find(
            ctx.tenant,
            entity_id=entity_id,
            instrument_type=instrument_type,
            as_of=as_of,
            hsn=hsn,
        )
        return [
            Instrument(
                instrument_id=i.instrument_id,
                instrument_type=i.instrument_type,
                issuing_authority=i.issuing_authority,
                instrument_number=i.instrument_number,
                valid_from=i.valid_from,
                valid_to=i.valid_to,
                status=i.status,
                scope_hsn=i.scope_hsn,
                bronze_ingest_id=i.bronze_ingest_id,
            )
            for i in found
        ]


schema = strawberry.Schema(query=Query)
