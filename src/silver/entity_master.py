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

import psycopg
from psycopg import sql

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.silver.supersede import close_current

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
        """Insert a new current row, with nothing to supersede."""
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            new_id = await self._insert(conn, ctx, rec, prior_id=None)
        logger.info("recorded %s %s for %s", rec.master_type, rec.reference_number, ctx)
        return new_id

    async def supersede(
        self, ctx: TenantContext, *, prior_id: uuid.UUID, rec: EntityMasterRecord
    ) -> uuid.UUID:
        """Close the prior version and append the corrected one, in ONE
        transaction — see `EntitlementService.supersede` for why the pair
        cannot be committed separately."""
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    "UPDATE {}.entity_master_record SET superseded_at = now()"
                    " WHERE record_id = %s AND superseded_at IS NULL"
                ).format(sql.Identifier(ctx.silver_schema)),
                (prior_id,),
            )
            new_id = await self._insert(conn, ctx, rec, prior_id=prior_id)
        logger.info("superseded %s with %s for %s", prior_id, new_id, ctx)
        return new_id

    async def record_or_supersede(self, ctx: TenantContext, rec: EntityMasterRecord) -> uuid.UUID:
        """Record this row, superseding a current one if it exists.

        The key is `entity_master_record_current_uq`'s columns exactly — see
        `src/silver/supersede.py`. A NATURAL key: the master record's own
        reference number (a CIN, a DIN, a registration number). A refreshed
        snapshot of the same registration supersedes.
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            prior_id = await close_current(
                conn,
                schema=ctx.silver_schema,
                table="entity_master_record",
                id_column="record_id",
                key={
                    "entity_id": rec.entity_id,
                    "master_type": rec.master_type,
                    "reference_number": rec.reference_number,
                },
            )
            new_id = await self._insert(conn, ctx, rec, prior_id=prior_id)
        logger.info(
            "recorded %s %s for %s (superseding %s)",
            rec.master_type, rec.reference_number, ctx, prior_id,
        )
        return new_id

    async def _insert(
        self,
        conn: psycopg.AsyncConnection[tuple[object, ...]],
        ctx: TenantContext,
        rec: EntityMasterRecord,
        *,
        prior_id: uuid.UUID | None,
    ) -> uuid.UUID:
        """The one INSERT all three entry points share. `record` and
        `supersede` previously carried two copies differing only in the
        `supersedes_record_id` column."""
        new_id = uuid.uuid4()
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
            (new_id, rec.entity_id, rec.master_type, rec.reference_number,
             rec.as_of_date, json.dumps(rec.details), rec.status,
             prior_id, rec.batch_id, rec.bronze_ingest_id),
        )
        return new_id
