"""Pin the schema release a tenant was handed, on their first upload of a type.

THE FLOW (agreed 2026-08-24). A tenant downloads a document type's schema from
the portal, maps their ERP export to it, and uploads. At that moment the
release currently CURRENT is pinned to them and a copy of the exact schema file
is written into their own bucket, so a later platform release cannot change the
contract under an integration already in flight. `inafin-api` then serves that
tenant their pinned release rather than whatever is newest.

BEST EFFORT, DELIBERATELY. `ensure_schema_pin` never raises into the ingestion
path: an upload must not fail because the catalogue is misconfigured or the
platform bucket is briefly unreachable. A tenant who uploads while pinning is
broken simply has no pin yet, and the next upload of that type pins them — the
same "log and swallow" shape `promote.py`'s post-commit publish and
`extraction/base.py`'s Silver copy already use.

That is a real trade and worth naming: the pin is a RECORD of what was handed
over, not a gate on ingestion. Nothing downstream refuses an upload for want of
one.

ROLLING A TENANT FORWARD (`roll_forward`, `tenantctl reschema`). Deliberately
NOT automatic. `schema_pin` is insert-only (the ingest role holds no UPDATE or
DELETE on it — enforced by GRANT, the same invariant `artefact_ledger`
enforces on Bronze) so a roll-forward is a NEW row, and migration 028's
`pinned_by_ingest_id` was made nullable specifically for this: "NULL only for
a deliberate roll-forward ... which is not caused by any artefact". This is
that deliberate act, run by an operator, never by ingestion itself.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

from psycopg import sql

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.provisioning.objectstore import ObjectStorePort

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SchemaPin:
    doc_type_code: str
    release_version: str
    object_bucket: str
    object_key: str


async def current_pin(
    pool: TenantScopedPool, ctx: TenantContext, *, doc_type_code: str
) -> SchemaPin | None:
    """This tenant's current release for one document type, or None.

    THE ONLY PLACE "latest row wins" IS IMPLEMENTED. `schema_pin` is
    INSERT-only (tenant migration 028), so rolling a tenant forward to v2 adds
    a row rather than updating one and the current pin is the newest row per
    document type. Do not re-derive that at a call site: two pins written in
    the same transaction share a timestamp, so `pinned_at` alone is a coin
    flip and the identity column is the only total order available.

    This began as a `v1_schema_pin` VIEW and could not stay one —
    `app.apply_tenant_grants` grants Bronze `relkind = 'r'` only, and its
    `REVOKE ALL ON ALL TABLES` baseline would strip any hand-written grant on
    every provision. Tenant migration 029 withdrew the view and records the
    full reasoning.
    """
    async with pool.transaction(ctx, Role.INGEST) as conn:
        row = await (
            await conn.execute(
                sql.SQL(
                    "SELECT DISTINCT ON (doc_type_code)"
                    " doc_type_code, release_version, object_bucket, object_key"
                    " FROM {}.schema_pin WHERE doc_type_code = %s"
                    " ORDER BY doc_type_code, pinned_at DESC, id DESC"
                ).format(sql.Identifier(ctx.bronze_schema)),
                (doc_type_code,),
            )
        ).fetchone()
    if row is None:
        return None
    return SchemaPin(str(row[0]), str(row[1]), str(row[2]), str(row[3]))


async def ensure_schema_pin(
    pool: TenantScopedPool,
    ctx: TenantContext,
    *,
    doc_type_code: str,
    ingest_id: uuid.UUID,
    store: ObjectStorePort | None,
    pinned_at: dt.datetime | None = None,
) -> SchemaPin | None:
    """Pin the CURRENT release for `doc_type_code` if this tenant has no pin.

    Returns the pin in force afterwards, or None if there was nothing to pin
    (no published schema for the type) or pinning failed. Never raises.
    """
    try:
        existing = await current_pin(pool, ctx, doc_type_code=doc_type_code)
        if existing is not None:
            return existing

        if store is None:
            return None

        pinned_at = pinned_at or dt.datetime.now(dt.UTC)

        # The platform-side artifact for the CURRENT release. Read inside the
        # tenant's own role: platform_ref_reader (shared migration 040) is what
        # makes that possible without a per-tenant grant.
        async with pool.transaction(ctx, Role.INGEST) as conn:
            row = await (
                await conn.execute(
                    "SELECT a.release_version, a.object_bucket, a.object_key"
                    " FROM platform_ref.schema_artifact a"
                    " JOIN platform_ref.schema_release r"
                    "   ON r.version = a.release_version"
                    " WHERE r.status = 'CURRENT' AND a.kind = 'SCHEMA'"
                    "   AND a.doc_type_code = %s",
                    (doc_type_code,),
                )
            ).fetchone()

        if row is None:
            # No published schema for this type. Not an error: 38 in-scope
            # types are UNSPECIFIED and legitimately have nothing to hand over.
            return None

        release, src_bucket, src_key = str(row[0]), str(row[1]), str(row[2])
        body = store.get(bucket=src_bucket, key=src_key)

        stored = store.put_schema_snapshot(
            slug=ctx.slug, release=release, doc_type_code=doc_type_code,
            data=body, pinned_at=pinned_at,
        )

        async with pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    "INSERT INTO {}.schema_pin"
                    " (doc_type_code, release_version, pinned_by_ingest_id,"
                    "  schema_sha256, object_bucket, object_key, pinned_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (doc_type_code, release_version) DO NOTHING"
                ).format(sql.Identifier(ctx.bronze_schema)),
                (doc_type_code, release, ingest_id, stored.content_hash,
                 stored.bucket, stored.key, pinned_at),
            )
        logger.info(
            "pinned %s to schema release %s for tenant %s",
            doc_type_code, release, ctx.slug,
        )
        return SchemaPin(doc_type_code, release, stored.bucket, stored.key)

    except Exception:
        # See the module docstring: an upload must never fail because pinning
        # did. Logged at WARNING with the traceback so it is visible, not
        # swallowed into silence.
        logger.warning(
            "could not pin a schema release for %s (tenant %s) — the upload is "
            "unaffected and the next upload of this type will retry",
            doc_type_code, ctx.slug, exc_info=True,
        )
        return None


async def roll_forward(
    pool: TenantScopedPool,
    ctx: TenantContext,
    *,
    doc_type_code: str,
    store: ObjectStorePort,
    pinned_at: dt.datetime | None = None,
) -> SchemaPin | None:
    """Pin this tenant to the CURRENT release for `doc_type_code`, replacing
    whatever they were pinned to before.

    Unlike `ensure_schema_pin`, this runs unconditionally — it is an
    operator's deliberate act (`tenantctl reschema`), not something ingestion
    calls, so it does not check for an existing pin first. `pinned_by_ingest_id`
    is NULL (migration 028's header names this exact case): the row records
    that an operator moved the tenant, not that an artefact did.

    Returns the new pin, or None if this tenant had no existing pin AND there
    is nothing published for the type — there is genuinely nothing to roll.
    Rolling a NEVER-pinned type forward is a no-op for the same reason
    `ensure_schema_pin` would also find nothing: the tenant has not touched
    that type yet, so `current_pin` on their next upload picks up CURRENT on
    its own.

    Raises whatever the store or the database raises — deliberately NOT
    swallowed the way `ensure_schema_pin` swallows failures. That function's
    silence is safe because ingestion must not fail for want of a pin; an
    operator running this command explicitly wants to know if it did not
    work.
    """
    existing = await current_pin(pool, ctx, doc_type_code=doc_type_code)

    async with pool.transaction(ctx, Role.INGEST) as conn:
        row = await (
            await conn.execute(
                "SELECT a.release_version, a.object_bucket, a.object_key"
                " FROM platform_ref.schema_artifact a"
                " JOIN platform_ref.schema_release r"
                "   ON r.version = a.release_version"
                " WHERE r.status = 'CURRENT' AND a.kind = 'SCHEMA'"
                "   AND a.doc_type_code = %s",
                (doc_type_code,),
            )
        ).fetchone()

    if row is None:
        return existing  # nothing published for this type; nothing to roll to

    release, src_bucket, src_key = str(row[0]), str(row[1]), str(row[2])
    if existing is not None and existing.release_version == release:
        return existing  # already current — a second run of this is a no-op

    pinned_at = pinned_at or dt.datetime.now(dt.UTC)
    body = store.get(bucket=src_bucket, key=src_key)
    stored = store.put_schema_snapshot(
        slug=ctx.slug, release=release, doc_type_code=doc_type_code,
        data=body, pinned_at=pinned_at,
    )

    async with pool.transaction(ctx, Role.INGEST) as conn:
        await conn.execute(
            sql.SQL(
                "INSERT INTO {}.schema_pin"
                " (doc_type_code, release_version, pinned_by_ingest_id,"
                "  schema_sha256, object_bucket, object_key, pinned_at)"
                " VALUES (%s, %s, NULL, %s, %s, %s, %s)"
                " ON CONFLICT (doc_type_code, release_version) DO NOTHING"
            ).format(sql.Identifier(ctx.bronze_schema)),
            (doc_type_code, release, stored.content_hash,
             stored.bucket, stored.key, pinned_at),
        )
    logger.info(
        "rolled %s forward to schema release %s for tenant %s",
        doc_type_code, release, ctx.slug,
    )
    return SchemaPin(doc_type_code, release, stored.bucket, stored.key)


async def roll_forward_all(
    pool: TenantScopedPool,
    ctx: TenantContext,
    *,
    store: ObjectStorePort,
    pinned_at: dt.datetime | None = None,
) -> list[SchemaPin]:
    """`roll_forward` for every document type this tenant currently has a
    pin for. The set of TYPES to roll comes from the tenant's own
    `schema_pin` history, not the full registry — an operator rolling a
    tenant forward is bringing what they already have up to date, not
    handing them types they have never touched.
    """
    async with pool.transaction(ctx, Role.INGEST) as conn:
        rows = await (
            await conn.execute(
                sql.SQL(
                    "SELECT DISTINCT doc_type_code FROM {}.schema_pin ORDER BY 1"
                ).format(sql.Identifier(ctx.bronze_schema))
            )
        ).fetchall()
    codes = [str(r[0]) for r in rows]

    pins: list[SchemaPin] = []
    for code in codes:
        pin = await roll_forward(pool, ctx, doc_type_code=code, store=store, pinned_at=pinned_at)
        if pin is not None:
            pins.append(pin)
    return pins


__all__ = [
    "SchemaPin", "current_pin", "ensure_schema_pin", "roll_forward", "roll_forward_all",
]
