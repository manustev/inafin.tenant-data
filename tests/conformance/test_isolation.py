"""Tenant isolation conformance gates — ARCHITECTURE.md 10.1 to 10.8.

This suite is the entire justification for Phase 1. Two rules it follows
throughout, both learned from suites that pass while the boundary is broken:

  * **Positive control on every gate.** Asserting "the query returned nothing"
    proves nothing if the seed failed. Every gate also asserts, via a privileged
    connection, that the rows it could not see genuinely exist.

  * **Colliding natural keys.** Both tenants hold the same invoice number, the
    same supplier GSTIN, the same content hash. A leak cannot hide behind
    naturally distinct data.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import COLLIDING_INVOICE_NUMBER, SeededTenant

from src.core.errors import TenantBoundaryViolation
from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext

pytestmark = pytest.mark.conformance


# =============================================================================
# 10.1 — Cross-tenant read is impossible
# =============================================================================


async def test_cross_tenant_read_is_denied(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """acme's recon role cannot read globex's Silver, by any path."""
    # POSITIVE CONTROL: globex's row exists and is findable by a privileged user.
    control = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.transaction_document WHERE doc_number = %s")
        .format(sql.Identifier(tenant_b.ctx.silver_schema)),
        (COLLIDING_INVOICE_NUMBER,),
    ).fetchone()
    assert control is not None and control[0] == 1, "seed failed — gate is vacuous"

    # ... and acme cannot reach it.
    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.v1_purchase_invoice").format(
                    sql.Identifier(tenant_b.ctx.silver_schema)
                )
            )

    # And the base table, not just the view.
    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.transaction_document").format(
                    sql.Identifier(tenant_b.ctx.silver_schema)
                )
            )


async def test_cross_tenant_admin_table_read_is_denied(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """admin_role (tenant migration 025) is a NEW gold table, added after
    apply_tenant_grants (001_app.sql) was written. It gets isolated with no
    hand-written grant of its own — step 6 sweeps every non-`__` gold table
    generically. This proves that sweep actually reached it, rather than
    trusting the mechanism by inspection alone.
    """
    role_name = f"globex-only-role-{uuid.uuid4()}"
    async with app_pool.transaction(tenant_b.ctx, Role.RECON) as conn:
        await conn.execute(
            sql.SQL(
                "INSERT INTO {}.admin_role (role_name, description) "
                "VALUES (%s, 'exists only in globex')"
            ).format(sql.Identifier(tenant_b.ctx.gold_schema)),
            (role_name,),
        )

    # POSITIVE CONTROL: the row genuinely exists.
    control = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.admin_role WHERE role_name = %s")
        .format(sql.Identifier(tenant_b.ctx.gold_schema)),
        (role_name,),
    ).fetchone()
    assert control is not None and control[0] == 1, "seed failed — gate is vacuous"

    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.admin_role").format(
                    sql.Identifier(tenant_b.ctx.gold_schema)
                )
            )


async def test_own_tenant_read_succeeds(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """The mirror of 10.1: isolation that also blocks legitimate reads is not isolation."""
    async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
        row = await (
            await conn.execute(
                sql.SQL(
                    "SELECT invoice_number, supplier_gstin FROM {}.v1_purchase_invoice"
                ).format(sql.Identifier(tenant_a.ctx.silver_schema))
            )
        ).fetchone()
    assert row is not None
    assert row[0] == COLLIDING_INVOICE_NUMBER


# =============================================================================
# 10.2 — No role assumed means no privileges (NOINHERIT is doing its job)
# =============================================================================


def test_app_login_without_set_role_has_nothing(
    raw_app_conn: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
) -> None:
    """Fail-closed: a code path that forgets SET LOCAL ROLE reads nothing.

    This is the property that makes the shared pool safe. app_login holds
    membership of every tenant role, but NOINHERIT + WITH INHERIT FALSE means
    membership confers nothing until it is explicitly assumed.
    """
    assert raw_app_conn.execute("SELECT current_user").fetchone() == ("app_login",)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw_app_conn.execute(
            sql.SQL("SELECT count(*) FROM {}.v1_purchase_invoice").format(
                sql.Identifier(tenant_a.ctx.silver_schema)
            )
        )


def test_app_login_cannot_reach_any_tenant_schema(
    raw_app_conn: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    """Behavioural, not catalogue-based, and the distinction matters.

    ``has_schema_privilege('app_login', ...)`` returns TRUE here: the catalogue
    functions count privileges reachable through role membership regardless of
    INHERIT, because the role *could* SET ROLE to get them. That is a correct
    answer to a different question. What must hold is that without an explicit
    SET LOCAL ROLE, an actual statement is refused — so this asserts on the
    statement, not on the catalogue.
    """
    for t in (tenant_a, tenant_b):
        for schema in (t.ctx.bronze_schema, t.ctx.silver_schema, t.ctx.gold_schema):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                # autocommit: each failed statement is its own transaction, so
                # one refusal does not poison the next assertion.
                raw_app_conn.execute(
                    sql.SQL("SELECT count(*) FROM {}.__schema_identity").format(
                        sql.Identifier(schema)
                    )
                )


# =============================================================================
# 10.3 — Role must not leak across pooled transactions  [MUST run via PgBouncer]
# =============================================================================


@pytest.mark.pooler
async def test_role_does_not_survive_commit(
    app_pool: TenantScopedPool,
    raw_app_conn: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
) -> None:
    """SET LOCAL ROLE must revert at COMMIT.

    Proves nothing against a direct connection: the hazard is a *pooled server
    backend* carrying one tenant's role into the next tenant's transaction.
    docker/pgbouncer sets default_pool_size = 1 so the two must share one
    backend rather than drifting onto separate ones.
    """
    async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
        who = await (await conn.execute("SELECT current_user")).fetchone()
        assert who is not None and who[0] == tenant_a.ctx.role_name(Role.RECON)

    # New transaction on (necessarily) the same backend, no role assumed.
    assert raw_app_conn.execute("SELECT current_user").fetchone() == ("app_login",)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw_app_conn.execute(
            sql.SQL("SELECT count(*) FROM {}.v1_purchase_invoice").format(
                sql.Identifier(tenant_a.ctx.silver_schema)
            )
        )


@pytest.mark.pooler
async def test_interleaved_tenants_do_not_bleed(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, tenant_b: SeededTenant
) -> None:
    """A, then B, then A again — each sees only itself, on one shared backend."""
    for ctx, expected in (
        (tenant_a.ctx, tenant_a.ctx.role_name(Role.RECON)),
        (tenant_b.ctx, tenant_b.ctx.role_name(Role.RECON)),
        (tenant_a.ctx, tenant_a.ctx.role_name(Role.RECON)),
    ):
        async with app_pool.transaction(ctx, Role.RECON) as conn:
            who = await (await conn.execute("SELECT current_user")).fetchone()
            assert who is not None and who[0] == expected
            rows = await (
                await conn.execute(
                    sql.SQL("SELECT entity_id FROM {}.v1_purchase_invoice").format(
                        sql.Identifier(ctx.silver_schema)
                    )
                )
            ).fetchall()
            assert len(rows) == 1


# =============================================================================
# 10.4 — The identity guard catches grant misconfiguration
# =============================================================================


async def test_identity_guard_fires_when_grants_are_wrong(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Deliberately misconfigure grants, then prove the guard still stops it.

    Grants stop a *deliberate* wrong-schema query. They do not stop a
    provisioning mistake. Here acme's recon role is given exactly the access it
    would have if someone fat-fingered a GRANT — and the boundary still holds,
    because globex's identity row says 'globex'.
    """
    a_recon = tenant_a.ctx.role_name(Role.RECON)
    b_silver = tenant_b.ctx.silver_schema
    try:
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON {}.__schema_identity TO {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON {}.v1_purchase_invoice TO {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )

        # The grant layer would now allow this read. The guard must not.
        with pytest.raises(TenantBoundaryViolation, match="belongs to globex"):
            async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
                await conn.execute(
                    "SELECT app.assert_schema_owner(%s, %s)", (b_silver, tenant_a.ctx.slug)
                )
    finally:
        admin.execute(
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )
        admin.execute(
            sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )


async def test_guard_rejects_role_from_another_tenant(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, tenant_b: SeededTenant
) -> None:
    """Right schema, wrong role. Grants alone would not catch this."""
    mismatched = TenantContext(slug=tenant_a.ctx.slug, tenant_id=tenant_a.ctx.tenant_id)
    async with app_pool.transaction(mismatched, Role.RECON) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="is not a role of"):
            await conn.execute(
                "SELECT app.assert_tenant_context(%s, %s)",
                (tenant_b.ctx.slug, tenant_b.ctx.silver_schema),
            )


# =============================================================================
# 10.5 — The prepared-statement / search_path hazard is structurally impossible
# =============================================================================


async def test_unqualified_reference_raises_rather_than_resolving(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """ARCHITECTURE.md 5.4 — the most dangerous failure mode, made mechanical.

    With a non-empty search_path, psycopg3 auto-prepares after prepare_threshold
    executions and the cached plan binds to whichever schema resolved AT PREPARE
    TIME. A later transaction with a different search_path then reads the wrong
    tenant's table: no error, correct-looking rows, wrong tenant.

    search_path is '' cluster-wide, so an unqualified reference cannot resolve
    to anything at all. Executed well past prepare_threshold to be sure the
    behaviour holds for prepared statements too, not just the first parse.
    """
    async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
        for _ in range(10):
            # Each attempt gets its own savepoint: the first failure aborts the
            # transaction, and without this the remaining iterations would raise
            # InFailedSqlTransaction and assert nothing about resolution.
            with pytest.raises(psycopg.errors.UndefinedTable):
                async with conn.transaction():
                    await conn.execute("SELECT count(*) FROM v1_purchase_invoice")


async def test_search_path_is_empty_under_the_pool(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
        got = await (await conn.execute("SELECT current_setting('search_path')")).fetchone()
    assert got is not None and got[0] in ("", '""')


# =============================================================================
# 10.6 — The privilege matrix (ARCHITECTURE.md 5.1), table-driven
# =============================================================================


async def test_recon_cannot_write_silver(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """Pipeline 2 must not be able to do Pipeline 1's job."""
    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL(
                    "UPDATE {}.transaction_document SET doc_number = 'TAMPERED'"
                ).format(sql.Identifier(tenant_a.ctx.silver_schema))
            )


async def test_ingest_cannot_read_gold(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """Pipeline 1 must not be able to read findings."""
    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.fact_record").format(
                    sql.Identifier(tenant_a.ctx.gold_schema)
                )
            )


async def test_recon_cannot_read_bronze(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.artefact_ledger").format(
                    sql.Identifier(tenant_a.ctx.bronze_schema)
                )
            )


async def test_bronze_is_insert_only(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The evidentiary record cannot be revised by the role that writes it.

    Bronze indexes objects held under Object Lock COMPLIANCE for 2190 days. A
    ledger row that could be UPDATEd or DELETEd would let the index drift from
    the store it describes — and "we amended our record of what the client sent"
    is not a sentence that survives a GST proceeding.

    `ingest` needs INSERT here, so this is the one place where the role that
    legitimately writes must still be unable to rewrite. Enforced by GRANT
    (shared migration 006), not by the absence of an UPDATE statement in code.
    """
    # POSITIVE CONTROL: ingest genuinely holds this table and can read it.
    async with app_pool.transaction(tenant_a.ctx, Role.INGEST) as conn:
        got = await (
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.artefact_ledger").format(
                    sql.Identifier(tenant_a.ctx.bronze_schema)
                )
            )
        ).fetchone()
        assert got is not None and got[0] > 0, "seed failed — gate is vacuous"

    for stmt in (
        "UPDATE {}.artefact_ledger SET received_from = 'tampered'",
        "DELETE FROM {}.artefact_ledger",
    ):
        with pytest.raises(TenantBoundaryViolation):
            async with app_pool.transaction(tenant_a.ctx, Role.INGEST) as conn:
                await conn.execute(
                    sql.SQL(stmt).format(
                        sql.Identifier(tenant_a.ctx.bronze_schema)
                    )
                )

    # POSITIVE CONTROL: the rows are still exactly as seeded.
    after = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.artefact_ledger WHERE received_from = 'tampered'")
        .format(sql.Identifier(tenant_a.ctx.bronze_schema))
    ).fetchone()
    assert after is not None and after[0] == 0


async def test_no_tenant_role_can_write_schema_identity(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """The guard's anchor must be immutable to every runtime role.

    A role that could UPDATE its own __schema_identity could rewrite tenant_slug
    and walk straight past assert_schema_owner — which would make the guard
    decorative. Checked for all three roles, in every layer they can reach.
    """
    targets = [
        (Role.INGEST, tenant_a.ctx.bronze_schema),
        (Role.INGEST, tenant_a.ctx.silver_schema),
        (Role.RECON, tenant_a.ctx.silver_schema),
        (Role.RECON, tenant_a.ctx.gold_schema),
        (Role.SUPPORT, tenant_a.ctx.silver_schema),
    ]
    for role, schema in targets:
        with pytest.raises(TenantBoundaryViolation):
            async with app_pool.transaction(tenant_a.ctx, role) as conn:
                await conn.execute(
                    sql.SQL(
                        "UPDATE {}.__schema_identity SET tenant_slug = 'globex'"
                    ).format(sql.Identifier(schema))
                )


async def test_support_is_read_only(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """SUPPORT may SELECT but never write.

    The read half asserts against the SEEDED ROW BY ITS PRIMARY KEY, not a
    table-wide `count(*)`. An absolute count is not a property of the SUPPORT
    grant at all — it is a property of whichever other tests happened to run
    first in the same session, and every one of them writes under a fresh
    `entity_id` precisely so it cannot collide with the seed. Counting the
    whole table defeated that: `test_transaction.py` and
    `test_api_ingest.py` each promote one legitimate row of their own, so the
    count was 3 by the time alphabetical collection reached this file, and 1
    only when this file ran alone. Pinning the seeded `doc_id` is both
    order-independent and a stronger assertion — it proves SUPPORT can read
    the row the seed actually wrote, where a count proved only that SUPPORT
    could read *something*.
    """
    async with app_pool.transaction(tenant_a.ctx, Role.SUPPORT) as conn:
        got = await (
            await conn.execute(
                sql.SQL(
                    "SELECT doc_number FROM {}.transaction_document WHERE doc_id = %s"
                ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
                (tenant_a.invoice_id,),
            )
        ).fetchone()
        assert got is not None and got[0] == COLLIDING_INVOICE_NUMBER

    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.SUPPORT) as conn:
            await conn.execute(
                sql.SQL("DELETE FROM {}.transaction_document").format(
                    sql.Identifier(tenant_a.ctx.silver_schema)
                )
            )


async def test_tenant_roles_cannot_write_platform_ref(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """Shared reference data is SELECT-only. One tenant must not be able to
    change a value every other tenant resolves against."""
    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.INGEST) as conn:
            await conn.execute(
                "INSERT INTO platform_ref.universal_master "
                "(value_type, value, description) VALUES ('Domain', 'EVIL', 'x')"
            )
