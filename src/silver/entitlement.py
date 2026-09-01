"""Archetype 3 — recording entitlement instruments in Silver.

Persistence only. Turning a LUT PDF into these fields is EXTRACTION, and
extraction is per-document-type work that belongs to the Stream A/B/C connectors
(ARCHITECTURE.md 7). Keeping the two apart is what lets thirty-three document
types share one table: the extractor differs per type, the row does not.

Corrections never overwrite. `supersede` closes the prior version by stamping
`superseded_at` and inserts a new row pointing back at it, so "what did we
believe when we released that report" stays answerable — which is the whole
point of the bitemporal design (ARCHITECTURE.md 8), and is what a CA is asked in
a hearing.
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
class InstrumentRecord:
    """One entitlement instrument, already extracted from its artefact."""

    entity_id: uuid.UUID
    instrument_type: str
    issuing_authority: str
    instrument_number: str
    valid_from: dt.date
    batch_id: uuid.UUID
    bronze_ingest_id: uuid.UUID
    valid_to: dt.date = dt.date(9999, 12, 31)
    status: str = "ACTIVE"
    # None means UNRESTRICTED — a LUT covers all exports. Distinct from an empty
    # list, which the CHECK constraint rejects because no instrument covers
    # nothing. See migrations/tenant/006.
    scope_hsn: list[str] | None = None
    scope: dict[str, object] = field(default_factory=dict)
    obligation: dict[str, object] = field(default_factory=dict)


class EntitlementService:
    def __init__(self, pool: TenantScopedPool) -> None:
        self._pool = pool

    async def record(self, ctx: TenantContext, rec: InstrumentRecord) -> uuid.UUID:
        """Insert a new current instrument, with nothing to supersede."""
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            instrument_id = await self._insert(conn, ctx, rec, prior_id=None)
        logger.info(
            "recorded %s %s for %s", rec.instrument_type, rec.instrument_number, ctx
        )
        return instrument_id

    async def supersede(
        self, ctx: TenantContext, *, prior_id: uuid.UUID, rec: InstrumentRecord
    ) -> uuid.UUID:
        """Close the prior version and append the corrected one.

        Both in ONE transaction. Committed separately, a reader between them
        would see either two current instruments (the unique index would in
        fact refuse the second insert) or none at all — and "none" would
        silently withdraw an entitlement the taxpayer holds.
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    "UPDATE {}.entitlement_instrument SET superseded_at = now()"
                    " WHERE instrument_id = %s AND superseded_at IS NULL"
                ).format(sql.Identifier(ctx.silver_schema)),
                (prior_id,),
            )
            instrument_id = await self._insert(conn, ctx, rec, prior_id=prior_id)
        logger.info("superseded %s with %s for %s", prior_id, instrument_id, ctx)
        return instrument_id

    async def record_or_supersede(
        self, ctx: TenantContext, rec: InstrumentRecord
    ) -> uuid.UUID:
        """Record this instrument, superseding a current one if it exists.

        The key is `entitlement_instrument_current_uq`'s columns exactly —
        see `src/silver/supersede.py` on why it must be the index's columns
        and not "the business key" in the abstract. A NATURAL key: the
        document carries its own identifier, so a re-issued licence with the
        same number is a correction of the one on file.
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            prior_id = await close_current(
                conn,
                schema=ctx.silver_schema,
                table="entitlement_instrument",
                id_column="instrument_id",
                key={
                    "entity_id": rec.entity_id,
                    "instrument_type": rec.instrument_type,
                    "instrument_number": rec.instrument_number,
                },
            )
            instrument_id = await self._insert(conn, ctx, rec, prior_id=prior_id)
        logger.info(
            "recorded %s %s for %s (superseding %s)",
            rec.instrument_type, rec.instrument_number, ctx, prior_id,
        )
        return instrument_id

    async def _insert(
        self,
        conn: psycopg.AsyncConnection[tuple[object, ...]],
        ctx: TenantContext,
        rec: InstrumentRecord,
        *,
        prior_id: uuid.UUID | None,
    ) -> uuid.UUID:
        """The one INSERT all three entry points share.

        Extracted because `record` and `supersede` previously carried two
        copies of it that differed only in one column — the shape where a
        fix applied to one survives in the other.
        """
        instrument_id = uuid.uuid4()
        await conn.execute(
            sql.SQL(
                """
                INSERT INTO {}.entitlement_instrument (
                    instrument_id, entity_id, instrument_type,
                    issuing_authority, instrument_number, valid_from,
                    valid_to, status, scope_hsn, scope, obligation,
                    supersedes_instrument_id, batch_id, bronze_ingest_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
            ).format(sql.Identifier(ctx.silver_schema)),
            (instrument_id, rec.entity_id, rec.instrument_type,
             rec.issuing_authority, rec.instrument_number, rec.valid_from,
             rec.valid_to, rec.status, rec.scope_hsn,
             json.dumps(rec.scope), json.dumps(rec.obligation),
             prior_id, rec.batch_id, rec.bronze_ingest_id),
        )
        return instrument_id
