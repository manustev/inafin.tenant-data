"""Tenant provisioning.

PHASE1-PLAN.md 4.3 — under schema-per-tenant, "provision a tenant" stops being
an INSERT and becomes a first-class, tested operation. It runs in production, it
can fail partway, and **a half-provisioned tenant is a security state, not
merely a broken one**: schemas can exist without their grants.

Ordering rule, and the reason for it:

    bucket  ->  schemas + roles + identity  ->  migrations + grants  ->  ACTIVE

The bucket is created FIRST because S3 cannot join a Postgres transaction. A
bucket without schemas is inert — nothing can address it. Schemas without a
bucket would accept ingests that have nowhere to land, and the failure would
surface at write time, after the platform had already told a client its upload
was accepted.

Everything inside step 2 is one Postgres transaction (DDL is transactional), so
a failure there leaves no schemas, no roles, and no identity rows behind.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import psycopg

from src.core.errors import ProvisioningError
from src.core.identifiers import (
    all_roles,
    all_schemas,
    bucket_name,
    recon_engine_role,
    reconciliation_schema,
    validate_slug,
)
from src.migrate.runner import MigrationRunner
from src.provisioning.objectstore import ObjectStorePort

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    slug: str
    tenant_id: uuid.UUID
    bucket: str
    schemas: tuple[str, str, str]
    roles: tuple[str, str, str]
    migrations_applied: list[str]


class ProvisioningService:
    def __init__(
        self,
        *,
        migrate_dsn: str,
        object_store: ObjectStorePort | None = None,
        bucket_prefix: str = "inafin-tenant",
    ) -> None:
        self._dsn = migrate_dsn
        self._store = object_store
        self._bucket_prefix = bucket_prefix
        self._runner = MigrationRunner(migrate_dsn)

    def provision(self, slug: str, tenant_id: uuid.UUID | None = None) -> ProvisionedTenant:
        validate_slug(slug)
        tenant_id = tenant_id or uuid.uuid4()

        # --- 1. Bucket. Idempotent, and outside the DB transaction. ----------
        if self._store is not None:
            bucket = self._store.ensure_bucket(slug)
        else:
            # Permitted only for DB-only tests; a runtime without an object
            # store cannot accept Bronze artefacts, and the ledger's NOT NULL
            # object_bucket will stop it trying.
            bucket = bucket_name(slug, self._bucket_prefix)
            logger.warning("provisioning %s with no object store configured", slug)

        # --- 2. Schemas, roles, identity rows. One transaction. --------------
        try:
            with psycopg.connect(self._dsn) as conn, conn.transaction():
                conn.execute(
                    """
                    INSERT INTO app.tenant_registry (slug, tenant_id, bucket, status)
                    VALUES (%s, %s, %s, 'PROVISIONING')
                    ON CONFLICT (slug) DO UPDATE
                       SET bucket = EXCLUDED.bucket,
                           status = CASE
                               WHEN app.tenant_registry.status = 'ACTIVE' THEN 'ACTIVE'
                               ELSE 'PROVISIONING' END
                    """,
                    (slug, tenant_id, bucket),
                )
                conn.execute(
                    "SELECT app.provision_tenant_schemas(%s, %s)", (slug, tenant_id)
                )
        except psycopg.Error as exc:
            raise ProvisioningError(
                f"failed to create schemas/roles for {slug}: {exc}"
            ) from exc

        # --- 3. Tenant migrations, then the grant matrix. --------------------
        # apply_tenant() re-asserts grants unconditionally at the end, so this
        # step is what turns bare schemas into a usable, correctly-privileged
        # tenant. Until it succeeds the tenant stays PROVISIONING.
        try:
            applied = self._runner.apply_tenant(slug)
        except Exception as exc:
            raise ProvisioningError(
                f"schemas exist for {slug} but migrations failed — tenant left "
                f"PROVISIONING and MUST NOT be routed traffic: {exc}"
            ) from exc

        # --- 4. Activate. ----------------------------------------------------
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.tenant_registry"
                "   SET status = 'ACTIVE', activated_at = now()"
                " WHERE slug = %s",
                (slug,),
            )

        logger.info("provisioned tenant %s (%s)", slug, tenant_id)
        return ProvisionedTenant(
            slug=slug,
            tenant_id=tenant_id,
            bucket=bucket,
            schemas=all_schemas(slug),
            roles=all_roles(slug),
            migrations_applied=applied,
        )

    def deprovision(self, slug: str, *, drop_schemas: bool = False) -> None:
        """Mark a tenant DEPROVISIONED; optionally drop its schemas and roles.

        ``drop_schemas`` defaults to False and should stay that way outside
        tests. Tenant data is subject to CGST s.36 retention, and the Bronze
        bucket is under compliance-mode Object Lock which cannot be deleted
        early by anyone. Erasure is crypto-shredding (Phase 5), not DROP.
        """
        validate_slug(slug)
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.tenant_registry SET status = 'DEPROVISIONED' WHERE slug = %s",
                (slug,),
            )
            if drop_schemas:
                # reconciliation/recon_engine (docs/adr/0001) are not part of
                # all_schemas/all_roles — see identifiers.py — so they are
                # named explicitly here, or a test tenant's teardown leaks
                # them (found 2026-09-03: dozens of orphaned
                # t_<random>_reconciliation schemas from prior test runs).
                for schema in (*all_schemas(slug), reconciliation_schema(slug)):
                    conn.execute(
                        psycopg.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                            psycopg.sql.Identifier(schema)
                        )
                    )
                for role in (*all_roles(slug), recon_engine_role(slug)):
                    conn.execute(
                        psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(
                            psycopg.sql.Identifier(role)
                        )
                    )
                conn.execute("DELETE FROM app.tenant_registry WHERE slug = %s", (slug,))
