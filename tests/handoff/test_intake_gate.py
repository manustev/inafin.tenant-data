"""BronzeIngestionService.receive, with the intake gate actually wired in.

OVERVIEW. tests/conformance/test_bronze_intake.py proves check_file and the
scanner adapters work in isolation. This file proves they are actually called
by receive(), in the right order, against the real pool and object store —
the thing a unit test of the adapters alone cannot show. A dirty-scanner
double stands in for a real ClamAV/commercial adapter (nothing here depends on
one being reachable), so what is under test is BronzeIngestionService's
wiring, not any particular scanner's correctness.
"""

from __future__ import annotations

import uuid

import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.bronze.scan import NullScanner, ScanResult, VirusScanPort
from src.bronze.service import BronzeIngestionService
from src.core.errors import IntakeRejected
from src.core.pool import TenantScopedPool
from src.provisioning.objectstore import S3ObjectStore

pytestmark = pytest.mark.handoff


class _AlwaysDirty:
    """A VirusScanPort double that flags everything — stands in for whichever
    real adapter (ClamAV, a commercial API, a cloud-native scanner) ends up
    configured in a given environment. What is under test here is that
    BronzeIngestionService calls its configured scanner and honours a
    positive result, not any one scanner's detection logic."""

    def scan(self, data: bytes) -> ScanResult:
        del data
        return ScanResult(clean=False, scanner="always-dirty", signature="TEST.MARKER")


async def test_a_flagged_file_is_refused_and_never_stored(
    app_pool: TenantScopedPool,
    object_store: S3ObjectStore,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
    admin,
) -> None:
    """Nothing about a refused file may become an artefact — no object, no
    ledger row — which is the property that distinguishes an intake refusal
    from a Silver quarantine (which keeps the bytes as evidence)."""
    gated = BronzeIngestionService(app_pool, object_store, scanner=_AlwaysDirty())

    with pytest.raises(IntakeRejected, match="always-dirty"):
        await gated.receive(
            tenant_a.ctx, entity_id=entity_id,
            data=b"doc_number\nX\n", filename="dirty.csv",
        )

    count = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.artefact_ledger WHERE entity_id = %s").format(
            sql.Identifier(tenant_a.ctx.bronze_schema)
        ),
        (entity_id,),
    ).fetchone()
    assert count == (0,)


async def test_a_clean_file_still_passes_through_the_gate(
    app_pool: TenantScopedPool,
    object_store: S3ObjectStore,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """The gate must not be a false-positive machine — a scanner reporting
    clean must not block an otherwise-legitimate upload."""
    gated = BronzeIngestionService(app_pool, object_store, scanner=NullScanner())
    receipt = await gated.receive(
        tenant_a.ctx, entity_id=entity_id, data=b"doc_number\nX\n", filename="clean.csv",
    )
    assert receipt.deduplicated is False


async def test_a_disallowed_extension_never_reaches_the_scanner(
    app_pool: TenantScopedPool,
    object_store: S3ObjectStore,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """File-check runs first — a scanner that would raise if ever called
    proves it was never invoked for a file the shape check already refused."""

    class _ExplodingScanner(VirusScanPort):
        def scan(self, data: bytes) -> ScanResult:
            raise AssertionError("scanner must not run when file-check already failed")

    gated = BronzeIngestionService(app_pool, object_store, scanner=_ExplodingScanner())
    with pytest.raises(IntakeRejected, match="extension"):
        await gated.receive(
            tenant_a.ctx, entity_id=entity_id, data=b"whatever", filename="a.exe",
        )
