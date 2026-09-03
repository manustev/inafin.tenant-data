"""inafin-reconciliation-engine's grant surface — docs/adr/0001, docs/adr/0002.

t_<slug>_recon_engine is not part of this repo's own pipeline (it is not in
`src.core.tenant.Role` — the engine is an external service, not a caller of
`TenantScopedPool`), so these tests assume the role directly on the
privileged `admin` connection with `SET ROLE`, the same way
`test_isolation.py`'s `raw_app_conn` tests assert on the statement rather
than on catalogue privileges. `admin` is function-scoped (a fresh connection
per test), so no `RESET ROLE` bookkeeping is needed between tests.

Same two rules as `test_isolation.py`: a positive control on every gate (the
row must be provably there before an empty result means anything), and
these tenants collide on natural keys, so a leak cannot hide behind
naturally distinct data.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.core.identifiers import recon_engine_role, reconciliation_schema

pytestmark = pytest.mark.conformance

_APPROVED_VIEWS = (
    "v1_rcm_payroll_tds_evidence",
    "v1_rcm_director_evidence",
    "v1_rcm_purchase_candidate",
    "v1_rcm_registration_history",
    "v1_ingest_batch",
)


def _as_recon_engine(
    conn: psycopg.Connection[tuple[object, ...]], slug: str
) -> None:
    conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(recon_engine_role(slug))))


def test_recon_engine_reads_every_approved_view(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """Positive control: the approved surface actually works, not just 'is granted'."""
    for view in _APPROVED_VIEWS:
        # POSITIVE CONTROL as admin first — an empty approved view would make
        # the recon_engine assertion below pass vacuously.
        admin.execute(
            sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(tenant_a.ctx.silver_schema), sql.Identifier(view)
            )
        )

    _as_recon_engine(admin, tenant_a.ctx.slug)
    for view in _APPROVED_VIEWS:
        admin.execute(
            sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(tenant_a.ctx.silver_schema), sql.Identifier(view)
            )
        )


def test_recon_engine_cannot_read_non_allowlisted_view(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """The allowlist is real: a v1_ view outside it stays out of reach.

    v1_sales_register exists and recon/support can read it — recon_engine
    must not, since the request doc asks for an approved SUBSET, not the
    full Silver v1_ surface.
    """
    _as_recon_engine(admin, tenant_a.ctx.slug)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(
            sql.SQL("SELECT count(*) FROM {}.v1_sales_register").format(
                sql.Identifier(tenant_a.ctx.silver_schema)
            )
        )


def test_recon_engine_cannot_read_silver_base_table(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """recon_engine's entire read contract is the allowlisted views, same as recon."""
    _as_recon_engine(admin, tenant_a.ctx.slug)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(
            sql.SQL("SELECT count(*) FROM {}.purchase_register").format(
                sql.Identifier(tenant_a.ctx.silver_schema)
            )
        )


def test_recon_engine_cannot_read_gold(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """Gold is inafinplatform/v2's — docs/adr/0001 gives the engine its own schema instead."""
    _as_recon_engine(admin, tenant_a.ctx.slug)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(
            sql.SQL("SELECT count(*) FROM {}.reconciliation_result").format(
                sql.Identifier(tenant_a.ctx.gold_schema)
            )
        )


def test_recon_engine_reads_the_one_gold_exception(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """docs/adr/0001's follow-up, shared migration 063: v1_reconciliation_tenant_setting
    is the ONE deliberate Gold exception. Positive control first."""
    admin.execute(
        sql.SQL("SELECT count(*) FROM {}.v1_reconciliation_tenant_setting").format(
            sql.Identifier(tenant_a.ctx.gold_schema)
        )
    )
    _as_recon_engine(admin, tenant_a.ctx.slug)
    admin.execute(
        sql.SQL("SELECT count(*) FROM {}.v1_reconciliation_tenant_setting").format(
            sql.Identifier(tenant_a.ctx.gold_schema)
        )
    )


def test_recon_engine_gold_exception_does_not_widen_to_other_tables(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """The allowlist is v1_reconciliation_% only — tenant_setting's own BASE
    table stays out of reach, same as every other v1_ contract in this repo."""
    _as_recon_engine(admin, tenant_a.ctx.slug)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(
            sql.SQL("SELECT count(*) FROM {}.tenant_setting").format(
                sql.Identifier(tenant_a.ctx.gold_schema)
            )
        )


def test_recon_engine_reads_the_category_bridge_view(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """Tenant migration 039: a second Gold exception, covered by the same
    v1_reconciliation_% allowlist as tenant_setting — no new grant needed."""
    admin.execute(
        sql.SQL("SELECT count(*) FROM {}.v1_reconciliation_category_bridge").format(
            sql.Identifier(tenant_a.ctx.gold_schema)
        )
    )
    _as_recon_engine(admin, tenant_a.ctx.slug)
    admin.execute(
        sql.SQL("SELECT count(*) FROM {}.v1_reconciliation_category_bridge").format(
            sql.Identifier(tenant_a.ctx.gold_schema)
        )
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(
            sql.SQL("SELECT count(*) FROM {}.gl_category_bridge").format(
                sql.Identifier(tenant_a.ctx.gold_schema)
            )
        )


def test_other_roles_cannot_read_reconciliation_schema(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """The reconciliation schema belongs to recon_engine alone."""
    schema = reconciliation_schema(tenant_a.ctx.slug)
    for role in ("_ingest", "_recon", "_support"):
        admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(f"t_{tenant_a.ctx.slug}{role}")))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            admin.execute(
                sql.SQL("SELECT count(*) FROM {}.__schema_identity").format(
                    sql.Identifier(schema)
                )
            )
        admin.execute("RESET ROLE")


def test_recon_engine_cross_tenant_denied(
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    """t_acme_recon_engine must not reach t_globex_reconciliation or t_globex_silver."""
    _as_recon_engine(admin, tenant_a.ctx.slug)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(
            sql.SQL("SELECT count(*) FROM {}.__schema_identity").format(
                sql.Identifier(reconciliation_schema(tenant_b.ctx.slug))
            )
        )


def test_recon_engine_can_create_in_own_schema(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """docs/adr/0001, reversed by shared migration 061: the engine owns its
    own table DDL inside its own schema now."""
    schema = reconciliation_schema(tenant_a.ctx.slug)
    _as_recon_engine(admin, tenant_a.ctx.slug)
    try:
        admin.execute(sql.SQL("CREATE TABLE {}.probe (id int)").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("INSERT INTO {}.probe VALUES (1)").format(sql.Identifier(schema)))
    finally:
        admin.execute("RESET ROLE")
        admin.execute(sql.SQL("DROP TABLE IF EXISTS {}.probe").format(sql.Identifier(schema)))


def test_recon_engine_cannot_create_in_another_tenants_schema(
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    """CREATE is scoped to the engine's own tenant, not a blanket engine-wide grant."""
    _as_recon_engine(admin, tenant_a.ctx.slug)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(
            sql.SQL("CREATE TABLE {}.probe (id int)").format(
                sql.Identifier(reconciliation_schema(tenant_b.ctx.slug))
            )
        )
