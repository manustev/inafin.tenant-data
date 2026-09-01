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

import psycopg
from psycopg import sql

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.silver.supersede import close_current

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
        """Insert a new current row, with nothing to supersede."""
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            new_id = await self._insert(conn, ctx, rec, prior_id=None)
        logger.info("recorded %s contract for %s", rec.contract_type, ctx)
        return new_id

    async def supersede(
        self, ctx: TenantContext, *, prior_id: uuid.UUID, rec: NarrativeContractRecord
    ) -> uuid.UUID:
        """Close the prior version and append the corrected one, in ONE
        transaction — see `EntitlementService.supersede` for why the pair
        cannot be committed separately."""
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    "UPDATE {}.narrative_contract SET superseded_at = now()"
                    " WHERE contract_id = %s AND superseded_at IS NULL"
                ).format(sql.Identifier(ctx.silver_schema)),
                (prior_id,),
            )
            new_id = await self._insert(conn, ctx, rec, prior_id=prior_id)
        logger.info("superseded %s with %s for %s", prior_id, new_id, ctx)
        return new_id

    async def record_or_supersede(
        self, ctx: TenantContext, rec: NarrativeContractRecord
    ) -> uuid.UUID:
        """Record this row, superseding a current one if it exists.

        The key is `narrative_contract_current_uq`'s columns exactly — see
        `src/silver/supersede.py`. A CONTENT key: no reference number exists on
        the document to key on, so the artefact itself is the identity
        (migration 019's header). A collision therefore means the SAME
        artefact was dispatched twice, and superseding records a
        RE-EXTRACTION of it — the prior reading closed, the new one current.
        That index is described there as a "resubmission guard"; it still
        guards, but a resubmission is now handled rather than refused.
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            prior_id = await close_current(
                conn,
                schema=ctx.silver_schema,
                table="narrative_contract",
                id_column="contract_id",
                key={
                    "entity_id": rec.entity_id,
                    "contract_type": rec.contract_type,
                    "bronze_ingest_id": rec.bronze_ingest_id,
                },
            )
            new_id = await self._insert(conn, ctx, rec, prior_id=prior_id)
        logger.info(
            "recorded %s contract for %s (superseding %s)", rec.contract_type, ctx, prior_id
        )
        return new_id

    async def _insert(
        self,
        conn: psycopg.AsyncConnection[tuple[object, ...]],
        ctx: TenantContext,
        rec: NarrativeContractRecord,
        *,
        prior_id: uuid.UUID | None,
    ) -> uuid.UUID:
        """The one INSERT every entry point shares."""
        new_id = uuid.uuid4()
        await conn.execute(
            sql.SQL(
                """
                INSERT INTO {}.narrative_contract (
                    contract_id, entity_id, contract_type, counterparty_name,
                    effective_date, term_end_date, key_terms, status,
                    supersedes_contract_id, batch_id, bronze_ingest_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
            ).format(sql.Identifier(ctx.silver_schema)),
            (new_id, rec.entity_id, rec.contract_type, rec.counterparty_name,
             rec.effective_date, rec.term_end_date, json.dumps(rec.key_terms),
             rec.status, prior_id, rec.batch_id, rec.bronze_ingest_id),
        )
        return new_id
