"""Archetype 7 — recording entity/counterparty master records in Silver.

Same shape as `src/silver/entitlement.py` — see that module's docstring for
the reasoning shared across every archetype-persistence service in this
codebase. `migrations/tenant/017_entity_master_record.sql` for the row shape.
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
class EntityMasterRecord:
    """One master-record snapshot, already extracted from its artefact."""

    entity_id: uuid.UUID
    master_type: str
    reference_number: str
    as_of_date: dt.date
    batch_id: uuid.UUID
    bronze_ingest_id: uuid.UUID
    status: str = "ACTIVE"
    details: dict[str, object] = field(default_factory=dict)


class EntityMasterService:
    def __init__(self, pool: TenantScopedPool) -> None:
        self._pool = pool

    async def record(self, ctx: TenantContext, rec: EntityMasterRecord) -> uuid.UUID:
        record_id = uuid.uuid4()
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.entity_master_record (
                        record_id, entity_id, master_type, reference_number,
                        as_of_date, details, status, batch_id, bronze_ingest_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                ).format(sql.Identifier(ctx.silver_schema)),
                (record_id, rec.entity_id, rec.master_type, rec.reference_number,
                 rec.as_of_date, json.dumps(rec.details), rec.status,
                 rec.batch_id, rec.bronze_ingest_id),
            )
        logger.info("recorded %s %s for %s", rec.master_type, rec.reference_number, ctx)
        return record_id

    async def supersede(
        self, ctx: TenantContext, *, prior_id: uuid.UUID, rec: EntityMasterRecord
    ) -> uuid.UUID:
        record_id = uuid.uuid4()
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    "UPDATE {}.entity_master_record SET superseded_at = now()"
                    " WHERE record_id = %s AND superseded_at IS NULL"
                ).format(sql.Identifier(ctx.silver_schema)),
                (prior_id,),
            )
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.entity_master_record (
                        record_id, entity_id, master_type, reference_number,
                        as_of_date, details, status, supersedes_record_id,
                        batch_id, bronze_ingest_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                ).format(sql.Identifier(ctx.silver_schema)),
                (record_id, rec.entity_id, rec.master_type, rec.reference_number,
                 rec.as_of_date, json.dumps(rec.details), rec.status,
                 prior_id, rec.batch_id, rec.bronze_ingest_id),
            )
        logger.info("superseded %s with %s for %s", prior_id, record_id, ctx)
        return record_id
