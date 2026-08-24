"""`read_batch_manifest` — the one place a `batch_id` becomes the
`BatchManifest` Kafka carries, generically across every dispatch mechanism.

WHY THIS IS GENERIC, NOT PER-MECHANISM. All four mechanisms
(`PDF_EXTRACTION` via `create_single_document_batch` in `src/extraction/
base.py`, `SALES_REGISTER`, `REGISTER_LOADER`, `ARCHETYPE1_PROMOTE`) already
write one `{silver}.ingest_batch` row per successful write — that table is
the queue of record (`migrations/tenant/002_silver.sql`'s own docstring),
and `src/silver/promote.py`'s `BatchManifest` is already shaped to match its
columns exactly. So the manifest for ANY mechanism's output is a read of
that one row, keyed by the `batch_id` every mechanism's outcome already
carries (`src/dispatch/router.py`'s `DispatchOutcome.batch_id`) — not a
field one-off constructed by each mechanism's own return type, which is why
`RegisterOutcome`/`UpsertOutcome` do not need to grow the manifest's fields
themselves.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import cast

from psycopg import sql

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.silver.promote import BatchManifest


class BatchNotFoundError(LookupError):
    """`batch_id` matches no row in this tenant's `ingest_batch` — would
    mean a mechanism returned a `batch_id` without having actually committed
    the row, which every mechanism's own transaction already guarantees
    cannot happen (manifest row and business rows commit together)."""


async def read_batch_manifest(
    pool: TenantScopedPool, ctx: TenantContext, batch_id: uuid.UUID,
) -> BatchManifest:
    silver = ctx.silver_schema
    async with pool.transaction(ctx, Role.INGEST) as conn:
        row = await (
            await conn.execute(
                sql.SQL(
                    "SELECT entity_id, document_type, source_stream,"
                    " period_start, period_end, row_count, content_hash,"
                    " bronze_manifest_ref, ready_at"
                    " FROM {}.ingest_batch WHERE batch_id = %s"
                ).format(sql.Identifier(silver)),
                (batch_id,),
            )
        ).fetchone()
    if row is None:
        raise BatchNotFoundError(f"no ingest_batch row for batch_id={batch_id} in {ctx}")

    entity_id, document_type, source_stream, period_start, period_end, \
        row_count, content_hash, bronze_manifest_ref, ready_at = cast(
            "tuple[uuid.UUID, str, str, dt.date, dt.date, int, bytes, str, dt.datetime]",
            row,
        )
    return BatchManifest(
        slug=ctx.slug, batch_id=batch_id, entity_id=entity_id,
        document_type=document_type, source_stream=source_stream,
        period_start=period_start, period_end=period_end,
        row_count=row_count, content_hash_hex=content_hash.hex(),
        bronze_manifest_ref=bronze_manifest_ref, ready_at=ready_at,
    )
