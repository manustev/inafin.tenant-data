"""Mutation checks — ARCHITECTURE.md 10.8.

Deliberately break the boundary, and assert the suite NOTICES. This is the gate
people skip, and skipping it is why broken isolation ships green: a conformance
suite nobody has ever seen fail is indistinguishable from a suite of
``assert True``.

Every mutation is reverted in ``finally``. They run against the dev cluster,
which is rebuilt per run, but leaving a widened grant behind would poison every
later assertion in the session, so the restores are not optional.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql
from scripts.check_isolation import check
from tests.conftest import SeededTenant

from src.core.errors import TenantBoundaryViolation
from src.core.pool import TenantScopedPool
from src.core.tenant import Role

pytestmark = pytest.mark.conformance


def _rules(findings: list) -> set[str]:
    return {f.rule for f in findings}


def test_baseline_is_clean(admin: psycopg.Connection[tuple[object, ...]]) -> None:
    """Control. If the baseline has findings, every mutation below is meaningless."""
    assert check(admin) == []


def test_mutation_cross_tenant_usage_is_detected(
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    a_recon = tenant_a.ctx.role_name(Role.RECON)
    b_silver = tenant_b.ctx.silver_schema
    try:
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )
        assert "no-cross-tenant-usage" in _rules(check(admin))
    finally:
        admin.execute(
            sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )
    assert check(admin) == []


def test_mutation_writable_identity_is_detected(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """The single most dangerous misconfiguration: a role that can rewrite the
    guard's anchor, and so walk past assert_schema_owner unchallenged."""
    role = tenant_a.ctx.role_name(Role.INGEST)
    silver = tenant_a.ctx.silver_schema
    try:
        admin.execute(
            sql.SQL("GRANT UPDATE ON {}.__schema_identity TO {}").format(
                sql.Identifier(silver), sql.Identifier(role)
            )
        )
        assert "identity-immutable" in _rules(check(admin))
    finally:
        admin.execute(
            sql.SQL("REVOKE UPDATE ON {}.__schema_identity FROM {}").format(
                sql.Identifier(silver), sql.Identifier(role)
            )
        )
    assert check(admin) == []


def test_mutation_recon_on_silver_base_table_is_detected(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    role = tenant_a.ctx.role_name(Role.RECON)
    silver = tenant_a.ctx.silver_schema
    try:
        admin.execute(
            sql.SQL("GRANT SELECT ON {}.transaction_document TO {}").format(
                sql.Identifier(silver), sql.Identifier(role)
            )
        )
        assert "recon-no-silver-base-tables" in _rules(check(admin))
    finally:
        admin.execute(
            sql.SQL("REVOKE SELECT ON {}.transaction_document FROM {}").format(
                sql.Identifier(silver), sql.Identifier(role)
            )
        )
    assert check(admin) == []


def test_mutation_security_invoker_view_is_detected(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """security_invoker on a v1_ view collapses the read contract."""
    silver = tenant_a.ctx.silver_schema
    try:
        admin.execute(
            sql.SQL("ALTER VIEW {}.v1_purchase_invoice SET (security_invoker = true)")
            .format(sql.Identifier(silver))
        )
        assert "v1-views-definer-rights" in _rules(check(admin))
    finally:
        admin.execute(
            sql.SQL("ALTER VIEW {}.v1_purchase_invoice RESET (security_invoker)")
            .format(sql.Identifier(silver))
        )
    assert check(admin) == []


def test_mutation_app_login_inherit_is_detected(
    admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    """If app_login ever gains INHERIT it holds every tenant's privileges with
    no SET ROLE, and the shared pool stops being safe."""
    try:
        admin.execute("ALTER ROLE app_login INHERIT")
        assert "app-login-noinherit" in _rules(check(admin))
    finally:
        admin.execute("ALTER ROLE app_login NOINHERIT")
    assert check(admin) == []


async def test_mutation_guard_removal_makes_cross_tenant_read_succeed(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    """The decisive mutation: prove the guard is what stops the read.

    Step 1 widens grants ONLY. The read is still refused — that is defence in
    depth working, and it means gate 10.4 is not merely restating gate 10.1.

    Step 2 additionally neuters app.assert_tenant_context. Now the read
    SUCCEEDS. That is the proof that the guard carries real weight: remove it
    and a boundary that looks correct at the grant layer leaks.
    """
    a_recon = tenant_a.ctx.role_name(Role.RECON)
    b_silver = tenant_b.ctx.silver_schema

    original = admin.execute(
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
        " JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'app' AND p.proname = 'assert_tenant_context'"
    ).fetchone()
    assert original is not None
    original_def = original[0]

    try:
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON {}.v1_purchase_invoice TO {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON {}.__schema_identity TO {}").format(
                sql.Identifier(b_silver), sql.Identifier(a_recon)
            )
        )

        # --- Step 1: grants widened, guard intact. Still refused. ---
        with pytest.raises(TenantBoundaryViolation):
            async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
                await conn.execute(
                    "SELECT app.assert_schema_owner(%s, %s)",
                    (b_silver, tenant_a.ctx.slug),
                )

        # --- Step 2: guard neutered. Now it leaks. ---
        # Parameter names must match the original: CREATE OR REPLACE cannot
        # rename input parameters.
        admin.execute(
            "CREATE OR REPLACE FUNCTION app.assert_tenant_context("
            "  p_slug text, p_schema text) RETURNS void"
            " LANGUAGE sql STABLE AS $$ SELECT NULL::void $$"
        )
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            got = await (
                await conn.execute(
                    sql.SQL("SELECT count(*) FROM {}.v1_purchase_invoice").format(
                        sql.Identifier(b_silver)
                    )
                )
            ).fetchone()
        assert got is not None and got[0] == 1, (
            "guard removed and grants widened, yet the read was still blocked — "
            "the mutation did not take effect, so this gate proved nothing"
        )
    finally:
        admin.execute(original_def)
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

    # And the boundary is restored.
    assert check(admin) == []
    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.v1_purchase_invoice").format(
                    sql.Identifier(b_silver)
                )
            )
