"""Archetype 8 — recording narrative contracts in Silver.

Same shape as `src/silver/entitlement.py`. Every business field is optional —
see `migrations/tenant/019_narrative_contract.sql`'s header for why: the
MinIO Silver copy is this archetype's primary record, `key_terms` is
best-effort on top of it.
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
class NarrativeContractRecord:
    """One narrative contract, already extracted from its artefact."""

    entity_id: uuid.UUID
    contract_type: str
    batch_id: uuid.UUID
    bronze_ingest_id: uuid.UUID
    counterparty_name: str | None = None
    effective_date: dt.date | None = None
    term_end_date: dt.date | None = None
    status: str = "ACTIVE"
    key_terms: dict[str, object] = field(default_factory=dict)


class NarrativeContractService:
    def __init__(self, pool: TenantScopedPool) -> None:
        self._pool = pool

    async def record(self, ctx: TenantContext, rec: NarrativeContractRecord) -> uuid.UUID:
        contract_id = uuid.uuid4()
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.narrative_contract (
                        contract_id, entity_id, contract_type, counterparty_name,
                        effective_date, term_end_date, key_terms, status,
                        batch_id, bronze_ingest_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                ).format(sql.Identifier(ctx.silver_schema)),
                (contract_id, rec.entity_id, rec.contract_type, rec.counterparty_name,
                 rec.effective_date, rec.term_end_date, json.dumps(rec.key_terms),
                 rec.status, rec.batch_id, rec.bronze_ingest_id),
            )
        logger.info("recorded %s contract for %s", rec.contract_type, ctx)
        return contract_id
