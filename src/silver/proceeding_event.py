"""Archetype 6 — recording proceeding events in Silver.

Same shape as `src/silver/entitlement.py`, deliberately: persistence only,
extraction is a separate concern (`src/extraction/`). `record`/`supersede`
mirror `EntitlementService` field-for-field where the table's columns line up
(entity_id, batch_id, bronze_ingest_id, bitemporal supersession); see
`migrations/tenant/016_proceeding_event.sql` for what the row means.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass, field

from psycopg import sql

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProceedingEventRecord:
    """One proceeding event, already extracted from its artefact."""

    entity_id: uuid.UUID
    event_type: str
    authority: str
    reference_number: str
    event_date: dt.date
    batch_id: uuid.UUID
    bronze_ingest_id: uuid.UUID
    amount: str | None = None
    status: str = "CLOSED"
    details: dict[str, object] = field(default_factory=dict)


class ProceedingEventService:
    def __init__(self, pool: TenantScopedPool) -> None:
        self._pool = pool

    async def record(self, ctx: TenantContext, rec: ProceedingEventRecord) -> uuid.UUID:
        event_id = uuid.uuid4()
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.proceeding_event (
                        event_id, entity_id, event_type, authority,
                        reference_number, event_date, amount, status, details,
                        batch_id, bronze_ingest_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                ).format(sql.Identifier(ctx.silver_schema)),
                (event_id, rec.entity_id, rec.event_type, rec.authority,
                 rec.reference_number, rec.event_date, rec.amount, rec.status,
                 json.dumps(rec.details), rec.batch_id, rec.bronze_ingest_id),
            )
        logger.info("recorded %s %s for %s", rec.event_type, rec.reference_number, ctx)
        return event_id

    async def supersede(
        self, ctx: TenantContext, *, prior_id: uuid.UUID, rec: ProceedingEventRecord
    ) -> uuid.UUID:
        event_id = uuid.uuid4()
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    "UPDATE {}.proceeding_event SET superseded_at = now()"
                    " WHERE event_id = %s AND superseded_at IS NULL"
                ).format(sql.Identifier(ctx.silver_schema)),
                (prior_id,),
            )
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.proceeding_event (
                        event_id, entity_id, event_type, authority,
                        reference_number, event_date, amount, status, details,
                        supersedes_event_id, batch_id, bronze_ingest_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                ).format(sql.Identifier(ctx.silver_schema)),
                (event_id, rec.entity_id, rec.event_type, rec.authority,
                 rec.reference_number, rec.event_date, rec.amount, rec.status,
                 json.dumps(rec.details), prior_id, rec.batch_id, rec.bronze_ingest_id),
            )
        logger.info("superseded %s with %s for %s", prior_id, event_id, ctx)
        return event_id
