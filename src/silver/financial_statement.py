"""Archetype 4 — recording financial statement extracts in Silver.

Same shape as `src/silver/entitlement.py`. `headline_figures` is expected to
be sparse from tier-1 deterministic extraction — see
`migrations/tenant/018_financial_statement_extract.sql`.
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
class FinancialStatementRecord:
    """One financial-statement extract, already extracted from its artefact."""

    entity_id: uuid.UUID
    statement_type: str
    auditor: str
    batch_id: uuid.UUID
    bronze_ingest_id: uuid.UUID
    financial_year: str | None = None
    filed_date: dt.date | None = None
    status: str = "AUDITED"
    headline_figures: dict[str, object] = field(default_factory=dict)


class FinancialStatementService:
    def __init__(self, pool: TenantScopedPool) -> None:
        self._pool = pool

    async def record(self, ctx: TenantContext, rec: FinancialStatementRecord) -> uuid.UUID:
        """Insert a new current row, with nothing to supersede."""
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            new_id = await self._insert(conn, ctx, rec, prior_id=None)
        logger.info("recorded %s statement for %s", rec.statement_type, ctx)
        return new_id

    async def supersede(
        self, ctx: TenantContext, *, prior_id: uuid.UUID, rec: FinancialStatementRecord
    ) -> uuid.UUID:
        """Close the prior version and append the corrected one, in ONE
        transaction — see `EntitlementService.supersede` for why the pair
        cannot be committed separately."""
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    "UPDATE {}.financial_statement_extract SET superseded_at = now()"
                    " WHERE extract_id = %s AND superseded_at IS NULL"
                ).format(sql.Identifier(ctx.silver_schema)),
                (prior_id,),
            )
            new_id = await self._insert(conn, ctx, rec, prior_id=prior_id)
        logger.info("superseded %s with %s for %s", prior_id, new_id, ctx)
        return new_id

    async def record_or_supersede(
        self, ctx: TenantContext, rec: FinancialStatementRecord
    ) -> uuid.UUID:
        """Record this row, superseding a current one if it exists.

        The key is `financial_statement_extract_current_uq`'s columns exactly — see
        `src/silver/supersede.py`. A CONTENT key: no reference number exists on
        the document to key on, so the artefact itself is the identity
        (migration 018's header). A collision therefore means the SAME
        artefact was dispatched twice, and superseding records a
        RE-EXTRACTION of it — the prior reading closed, the new one current.
        That index is described there as a "resubmission guard"; it still
        guards, but a resubmission is now handled rather than refused.
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            prior_id = await close_current(
                conn,
                schema=ctx.silver_schema,
                table="financial_statement_extract",
                id_column="extract_id",
                key={
                    "entity_id": rec.entity_id,
                    "statement_type": rec.statement_type,
                    "bronze_ingest_id": rec.bronze_ingest_id,
                },
            )
            new_id = await self._insert(conn, ctx, rec, prior_id=prior_id)
        logger.info(
            "recorded %s statement for %s (superseding %s)", rec.statement_type, ctx, prior_id
        )
        return new_id

    async def _insert(
        self,
        conn: psycopg.AsyncConnection[tuple[object, ...]],
        ctx: TenantContext,
        rec: FinancialStatementRecord,
        *,
        prior_id: uuid.UUID | None,
    ) -> uuid.UUID:
        """The one INSERT every entry point shares."""
        new_id = uuid.uuid4()
        await conn.execute(
            sql.SQL(
                """
                INSERT INTO {}.financial_statement_extract (
                    extract_id, entity_id, statement_type, auditor,
                    financial_year, filed_date, headline_figures, status,
                    supersedes_extract_id, batch_id, bronze_ingest_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
            ).format(sql.Identifier(ctx.silver_schema)),
            (new_id, rec.entity_id, rec.statement_type, rec.auditor,
             rec.financial_year, rec.filed_date, json.dumps(rec.headline_figures),
             rec.status, prior_id, rec.batch_id, rec.bronze_ingest_id),
        )
        return new_id
