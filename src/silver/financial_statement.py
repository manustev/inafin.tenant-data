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

from psycopg import sql

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext

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
        extract_id = uuid.uuid4()
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.financial_statement_extract (
                        extract_id, entity_id, statement_type, auditor,
                        financial_year, filed_date, headline_figures, status,
                        batch_id, bronze_ingest_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                ).format(sql.Identifier(ctx.silver_schema)),
                (extract_id, rec.entity_id, rec.statement_type, rec.auditor,
                 rec.financial_year, rec.filed_date, json.dumps(rec.headline_figures),
                 rec.status, rec.batch_id, rec.bronze_ingest_id),
            )
        logger.info("recorded %s statement for %s", rec.statement_type, ctx)
        return extract_id
