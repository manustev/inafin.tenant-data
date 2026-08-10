"""Per-tenant column extension gates — TYPED-TABLES-PLAN.md 7, build order step 2.

This is the mechanism the whole typed-table redesign rests on. Table-per-type was
chosen over the archetype tables because a tenant needs to carry additional
columns on a register, and a table shared by 11 or 33 document types across every
tenant cannot support that. If these gates do not hold, the decisive argument for
the redesign does not either.

The extension chain is written to a temporary directory rather than committed
under migrations/tenant_ext/, so no real tenant acquires a column because a test
ran. The runner reads TENANT_EXT_DIR at call time for exactly that reason.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from src.core.config import Settings
from src.core.errors import MigrationError
from src.core.identifiers import silver_schema
from src.migrate import runner as runner_mod
from src.migrate.runner import MigrationRunner
from src.provisioning.service import ProvisioningService

pytestmark = pytest.mark.conformance

# An extension of the shape the redesign exists to allow: one tenant's register
# carries a cost centre, and no other tenant's does.
_EXT_BODY = """
CREATE TABLE IF NOT EXISTS {{silver}}.ext_probe (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cost_centre text NOT NULL
);
"""


@pytest.fixture
def ext_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(runner_mod, "TENANT_EXT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def scratch_tenant(settings: Settings, provisioned: dict[str, object]):
    """A throwaway tenant. Extensions are per slug, so the test needs its own."""
    _ = provisioned
    svc = ProvisioningService(migrate_dsn=settings.pg_migrate_dsn)
    slug = f"ext{uuid.uuid4().hex[:8]}"
    svc.provision(slug)
    try:
        yield slug
    finally:
        svc.deprovision(slug, drop_schemas=True)


def _has_ext_probe(settings: Settings, slug: str) -> bool:
    with psycopg.connect(settings.pg_migrate_dsn, autocommit=True) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM pg_tables WHERE schemaname = %s AND tablename = 'ext_probe'",
                (silver_schema(slug),),
            ).fetchone()
            is not None
        )


def test_an_extension_applies_to_its_tenant_and_no_other(
    settings: Settings, ext_root: Path, scratch_tenant: str, tenant_a
) -> None:
    """The property the redesign is built on, stated as a test.

    A tenant-specific column must reach exactly one schema. If it reaches all of
    them it is a common migration wearing a disguise, and if it reaches none the
    mechanism does not work.
    """
    (ext_root / scratch_tenant).mkdir()
    (ext_root / scratch_tenant / "ext_001_probe.sql").write_text(_EXT_BODY)

    runner = MigrationRunner(settings.pg_migrate_dsn)
    assert runner.apply_tenant(scratch_tenant) == ["ext_001_probe.sql"]

    # Migrate the OTHER tenant too, with the extension already authored. Without
    # this the leak is unreachable — tenant_a would simply never have been asked
    # to migrate while the file existed, and the assertion below would pass for
    # the wrong reason. The mutation check caught exactly that.
    assert runner.apply_tenant(tenant_a.slug) == []

    assert _has_ext_probe(settings, scratch_tenant)
    assert not _has_ext_probe(settings, tenant_a.slug), (
        "an extension leaked into a tenant that does not have one — the base "
        "tables are supposed to stay byte-identical across the estate"
    )


def test_an_extension_is_recorded_and_not_reapplied(
    settings: Settings, ext_root: Path, scratch_tenant: str
) -> None:
    """Same bookkeeping table, same pinning. An extension is not a lesser
    migration that may be re-run."""
    (ext_root / scratch_tenant).mkdir()
    (ext_root / scratch_tenant / "ext_001_probe.sql").write_text(_EXT_BODY)

    runner = MigrationRunner(settings.pg_migrate_dsn)
    runner.apply_tenant(scratch_tenant)
    assert runner.apply_tenant(scratch_tenant) == [], "already at head"

    with psycopg.connect(settings.pg_migrate_dsn, autocommit=True) as conn:
        row = conn.execute(
            sql.SQL("SELECT checksum FROM {} WHERE filename = 'ext_001_probe.sql'").format(
                sql.Identifier(silver_schema(scratch_tenant), "__migration_version")
            )
        ).fetchone()
    assert row is not None, "the extension was applied but not recorded"


def test_editing_an_applied_extension_is_a_hard_error(
    settings: Settings, ext_root: Path, scratch_tenant: str
) -> None:
    """Checksum pinning does not care which chain a file came from.

    Without this, the one chain that varies per tenant would be the one chain
    nobody could prove had run unmodified.
    """
    ext_dir = ext_root / scratch_tenant
    ext_dir.mkdir()
    (ext_dir / "ext_001_probe.sql").write_text(_EXT_BODY)

    runner = MigrationRunner(settings.pg_migrate_dsn)
    runner.apply_tenant(scratch_tenant)

    (ext_dir / "ext_001_probe.sql").write_text(_EXT_BODY + "\n-- edited after the fact\n")
    with pytest.raises(MigrationError, match="has been edited"):
        runner.apply_tenant(scratch_tenant)


def test_drift_is_per_tenant_not_global(
    settings: Settings, ext_root: Path, scratch_tenant: str
) -> None:
    """A tenant with an unapplied extension is drift.

    Before this change drift_report compared every tenant against one expected
    list, so the tenant whose queries were about to fail on a missing column was
    the one reported healthy.
    """
    runner = MigrationRunner(settings.pg_migrate_dsn)
    assert runner.drift_report() == {}, "clean before the extension is authored"

    (ext_root / scratch_tenant).mkdir()
    (ext_root / scratch_tenant / "ext_001_probe.sql").write_text(_EXT_BODY)

    assert runner.drift_report() == {scratch_tenant: ["ext_001_probe.sql"]}

    runner.apply_tenant(scratch_tenant)
    assert runner.drift_report() == {}


def test_unprefixed_extension_filename_is_refused(
    settings: Settings, ext_root: Path, scratch_tenant: str
) -> None:
    """The collision this prevents is silent and nasty.

    Both chains share one __migration_version table keyed by filename. An
    extension called 003_gold.sql would make the runner treat the real 003 as
    already applied for this tenant — skipping it — and then report a checksum
    mismatch on a file nobody edited. Refusing the name is the only point at
    which that is still legible.
    """
    ext_dir = ext_root / scratch_tenant
    ext_dir.mkdir()
    (ext_dir / "003_gold.sql").write_text(_EXT_BODY)

    runner = MigrationRunner(settings.pg_migrate_dsn)
    with pytest.raises(MigrationError, match=r"ext_NNN_description\.sql"):
        runner.apply_tenant(scratch_tenant)

    assert not _has_ext_probe(settings, scratch_tenant), (
        "the chain must be refused whole, not partially applied"
    )


def test_a_tenant_with_no_extension_directory_is_unaffected(
    settings: Settings, ext_root: Path, tenant_a
) -> None:
    """Most tenants will never have one. The common path must not change."""
    runner = MigrationRunner(settings.pg_migrate_dsn)
    assert not (ext_root / tenant_a.slug).exists()
    assert runner.apply_tenant(tenant_a.slug) == []
    assert runner.drift_report() == {}


def test_a_new_tenant_lands_at_head_including_its_extensions(
    settings: Settings, ext_root: Path, provisioned: dict[str, object]
) -> None:
    """A tenant provisioned mid-release must land at head — and head, for a
    tenant that has extensions authored ahead of it, includes them."""
    _ = provisioned
    svc = ProvisioningService(migrate_dsn=settings.pg_migrate_dsn)
    slug = f"ext{uuid.uuid4().hex[:8]}"
    (ext_root / slug).mkdir()
    (ext_root / slug / "ext_001_probe.sql").write_text(_EXT_BODY)
    try:
        svc.provision(slug)
        assert MigrationRunner(settings.pg_migrate_dsn).drift_report().get(slug) is None
        assert _has_ext_probe(settings, slug)
    finally:
        svc.deprovision(slug, drop_schemas=True)
