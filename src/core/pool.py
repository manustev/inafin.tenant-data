"""TenantScopedPool — the only way to obtain a database connection.

ARCHITECTURE.md 5.2. The mechanics, and why each part is load-bearing:

    BEGIN;
      SET LOCAL ROLE t_acme_recon;                  -- (1) reverts at COMMIT
      SELECT app.assert_tenant_context('acme', 't_acme_silver');   -- (2)
      SELECT ... FROM "t_acme_silver".v1_purchase_invoice ...;     -- (3)
    COMMIT;

(1) ``SET LOCAL`` is transaction-scoped. PgBouncer pools by (user, database), so
    every tenant shares one ``app_login`` pool — but a pooled server connection
    cannot carry one tenant's role into another tenant's transaction, because
    the role is discarded at COMMIT. Session-scoped ``SET ROLE`` would leak
    across tenants on a recycled backend; it is banned and CI greps for it.

    ``app_login`` is NOINHERIT and holds membership WITH INHERIT FALSE, so
    between transactions it has no privileges at all. Fail-closed: a code path
    that forgets to assume a role cannot read anything, rather than reading
    everything.

(2) The preamble asserts both the assumed role and the target schema belong to
    the intended tenant. One round trip.

(3) Every statement is fully qualified. ``search_path`` is '' cluster-wide, so
    an unqualified reference raises rather than resolving — which is what makes
    the psycopg3 prepared-statement hazard (ARCHITECTURE.md 5.4) structurally
    impossible instead of merely discouraged.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType

import psycopg
from psycopg import AsyncConnection, sql
from psycopg_pool import AsyncConnectionPool

from src.core.errors import TenantBoundaryViolation
from src.core.tenant import Role, TenantContext

logger = logging.getLogger(__name__)

# SQLSTATE 42501. Raised both by our guard and by Postgres itself on a denied
# grant. Both are mapped to TenantBoundaryViolation: in a system where every
# privilege is granted declaratively from a matrix, neither should ever occur
# in normal operation, so both deserve an alert rather than a retry.
_INSUFFICIENT_PRIVILEGE = "42501"


class TenantScopedPool:
    """Wraps an AsyncConnectionPool. The raw pool is never exported."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        application_name: str = "inafin-tenant-data",
    ) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={
                "autocommit": True,
                "application_name": application_name,
                # MANDATORY under PgBouncer transaction pooling. psycopg3
                # auto-prepares a statement after prepare_threshold executions,
                # on whichever server backend it happened to be using. The next
                # transaction is routed to a different backend, which has never
                # heard of it:
                #
                #   psycopg.errors.InvalidSqlStatementName:
                #     prepared statement "_pg3_0" does not exist
                #
                # It surfaces only under load — the first few executions of any
                # query work fine — which makes it a production-only failure.
                #
                # Disabling also avoids unbounded cache growth: SQL here is
                # fully qualified, so every tenant produces distinct query text
                # and the per-connection cache would otherwise scale with the
                # tenant count.
                #
                # (PgBouncer >= 1.21 can track prepared statements via
                # max_prepared_statements. Not relied upon: correctness should
                # not depend on a pooler setting the app cannot see.)
                "prepare_threshold": None,
            },
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30.0)

    async def close(self) -> None:
        await self._pool.close()

    async def __aenter__(self) -> TenantScopedPool:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @asynccontextmanager
    async def transaction(
        self, ctx: TenantContext, role: Role
    ) -> AsyncIterator[AsyncConnection[tuple[object, ...]]]:
        """Yield a connection scoped to one tenant for one transaction.

        ``ctx`` is required and positional — there is no default and no ambient
        fallback, so a caller cannot accidentally get an unscoped connection.
        """
        role_name = ctx.role_name(role)
        guard_schema = ctx.guard_schema(role)

        async with self._pool.connection() as conn:
            try:
                async with conn.transaction():
                    await conn.execute(
                        sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role_name))
                    )
                    await conn.execute(
                        "SELECT app.assert_tenant_context(%s, %s)",
                        (ctx.slug, guard_schema),
                    )
                    yield conn
            except psycopg.Error as exc:
                if exc.sqlstate == _INSUFFICIENT_PRIVILEGE:
                    logger.error(
                        "tenant boundary violation: slug=%s role=%s schema=%s: %s",
                        ctx.slug,
                        role_name,
                        guard_schema,
                        exc,
                    )
                    raise TenantBoundaryViolation(
                        f"{ctx} role={role_name}: {exc}"
                    ) from exc
                raise


@asynccontextmanager
async def unscoped_admin_connection(dsn: str) -> AsyncIterator[AsyncConnection[tuple[object, ...]]]:
    """A connection with NO tenant scope. Migrations, provisioning, tests only.

    Deliberately named so that its appearance in a request-serving code path is
    obvious in review, and greppable in CI. It takes a DSN directly rather than
    reading configuration, so it cannot be reached with the runtime credential
    by accident.
    """
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        yield conn
    finally:
        await conn.close()
