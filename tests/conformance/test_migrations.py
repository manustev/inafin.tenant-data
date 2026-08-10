"""Migration fan-out runner — the operational cost of schema-per-tenant.

ARCHITECTURE.md 5.8 and PHASE1-PLAN.md 6 both flag this as the item most likely
to be underestimated. These gates cover the four properties that make it more
than "Alembic in a loop": per-schema versioning, checksum pinning, resumability,
and drift detection.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.core.config import Settings
from src.core.errors import MigrationError, SchemaDriftError
from src.core.identifiers import InvalidSlugError, silver_schema
from src.migrate.runner import MigrationRunner, _render
from src.provisioning.service import ProvisioningService

pytestmark = pytest.mark.conformance


def test_new_tenant_is_provisioned_at_head(settings: Settings, provisioned) -> None:
    """A tenant created mid-release must land at head, not at zero."""
    _ = provisioned
    runner = MigrationRunner(settings.pg_migrate_dsn)
    svc = ProvisioningService(migrate_dsn=settings.pg_migrate_dsn)
    slug = f"midrelease{uuid.uuid4().hex[:6]}"
    try:
        svc.provision(slug)
        assert runner.drift_report().get(slug) is None
        assert runner.apply_tenant(slug) == [], "already at head; nothing to apply"
    finally:
        svc.deprovision(slug, drop_schemas=True)


def test_drift_is_detected(settings: Settings, tenant_a: SeededTenant) -> None:
    """Silent drift is how one tenant ends up two migrations behind and nobody
    notices until a query fails in production."""
    runner = MigrationRunner(settings.pg_migrate_dsn)
    assert runner.drift_report() == {}

    head = runner.head()
    assert head is not None
    version_table = sql.Identifier(silver_schema(tenant_a.slug), "__migration_version")

    with psycopg.connect(settings.pg_migrate_dsn, autocommit=True) as conn:
        row = conn.execute(
            sql.SQL("SELECT filename, checksum FROM {} WHERE filename = %s").format(
                version_table
            ),
            (head,),
        ).fetchone()
        assert row is not None
        try:
            conn.execute(
                sql.SQL("DELETE FROM {} WHERE filename = %s").format(version_table),
                (head,),
            )
            drift = runner.drift_report()
            assert drift.get(tenant_a.slug) == [head]
            with pytest.raises(SchemaDriftError, match="not at head"):
                runner.assert_no_drift()
        finally:
            conn.execute(
                sql.SQL("INSERT INTO {} (filename, checksum) VALUES (%s, %s)"
                        " ON CONFLICT (filename) DO NOTHING").format(version_table),
                row,
            )

    assert runner.drift_report() == {}


def test_editing_an_applied_migration_is_a_hard_error(
    settings: Settings, tenant_a: SeededTenant
) -> None:
    """A migration that ran and has since been edited means the database and the
    repository disagree about what was executed. For a file that grants
    privileges, that is not a state anyone should be guessing about."""
    runner = MigrationRunner(settings.pg_migrate_dsn)
    head = runner.head()
    assert head is not None
    version_table = sql.Identifier(silver_schema(tenant_a.slug), "__migration_version")

    with psycopg.connect(settings.pg_migrate_dsn, autocommit=True) as conn:
        original = conn.execute(
            sql.SQL("SELECT checksum FROM {} WHERE filename = %s").format(version_table),
            (head,),
        ).fetchone()
        assert original is not None
        try:
            conn.execute(
                sql.SQL("UPDATE {} SET checksum = %s WHERE filename = %s").format(
                    version_table
                ),
                ("deadbeef" * 8, head),
            )
            with pytest.raises(MigrationError, match="has been edited"):
                runner.apply_tenant(tenant_a.slug)
        finally:
            conn.execute(
                sql.SQL("UPDATE {} SET checksum = %s WHERE filename = %s").format(
                    version_table
                ),
                (original[0], head),
            )

    runner.apply_tenant(tenant_a.slug)  # healthy again


def test_one_failing_tenant_does_not_block_the_others(
    settings: Settings, provisioned
) -> None:
    """Resumability. A single bad schema must not stop the estate migrating."""
    _ = provisioned
    runner = MigrationRunner(settings.pg_migrate_dsn)
    svc = ProvisioningService(migrate_dsn=settings.pg_migrate_dsn)
    broken = f"broken{uuid.uuid4().hex[:6]}"
    try:
        svc.provision(broken)
        # Break exactly one tenant by removing its bookkeeping table.
        with psycopg.connect(settings.pg_migrate_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP TABLE {}").format(
                    sql.Identifier(silver_schema(broken), "__migration_version")
                )
            )

        report = runner.apply_all_tenants(concurrency=4)
        assert not report.ok
        failed = {r.slug for r in report.failed}
        assert failed == {broken}
        assert {r.slug for r in report.results} - failed, "healthy tenants were skipped"
    finally:
        svc.deprovision(broken, drop_schemas=True)


def test_render_rejects_an_invalid_slug() -> None:
    """The template substitution is the one place a slug becomes an identifier.

    Injection is blocked upstream by validate_slug rather than by escaping at
    the substitution site, so this asserts the gate is actually invoked there.
    """
    body = "CREATE TABLE {{silver}}.x (id int);"
    assert '"t_acme_silver"' in _render(body, "acme")
    for hostile in ('acme"; DROP SCHEMA public; --', "ACME", "a", "acme-corp", ""):
        with pytest.raises(InvalidSlugError):
            _render(body, hostile)
