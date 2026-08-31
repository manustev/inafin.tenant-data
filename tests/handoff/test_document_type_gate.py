"""The intake-time document-type gate — `BronzeIngestionService.receive`
refuses a `document_type` the registry does not recognise as in-scope.

FOUND THE TWELFTH SESSION. Before this gate, nothing stopped a caller
declaring a type this codebase can never process — the ledger's FK checks
`declared_document_type` against `platform_ref.universal_master`'s free
vocabulary (132 values, including five retired Phase-1 codes that predate the
registry and are not rows in `platform_ref.document_type` at all), never
against the 125 in-scope registry rows. A tenant could get a 201 for a type
that would sit in Bronze forever, undispatchable, discovered only much later.

Three shapes, three tests: unknown code, known-but-out-of-scope code,
known-and-in-scope-but-undispatchable code (which must be ACCEPTED — this gate
checks existence in the registry, not readiness to dispatch).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from tests.conftest import SeededTenant

from src.bronze.service import BronzeIngestionService
from src.core.errors import IntakeRejected
from src.core.pool import TenantScopedPool
from src.provisioning.objectstore import S3ObjectStore

pytestmark = pytest.mark.conformance


async def test_an_unknown_document_type_is_refused(
    app_pool: TenantScopedPool, object_store: S3ObjectStore, tenant_a: SeededTenant,
) -> None:
    bronze = BronzeIngestionService(app_pool, object_store)
    with pytest.raises(IntakeRejected, match="not a recognised document type"):
        await bronze.receive(
            tenant_a.ctx, entity_id=uuid.uuid4(), data=b"doc_number\nX\n",
            document_type="THIS_TYPE_DOES_NOT_EXIST", filename="a.csv",
        )


async def test_a_retired_phase_one_code_is_refused(
    app_pool: TenantScopedPool, object_store: S3ObjectStore, tenant_a: SeededTenant,
) -> None:
    """`PURCHASE_INVOICE` — the FK vocabulary (`universal_master`) still
    accepts it (Bronze is insert-only and old rows reference it), but it is
    not a row in `platform_ref.document_type` at all. This is the exact type
    the removed `document_type = "PURCHASE_INVOICE"` default used to hand a
    caller who declared nothing — the bug this gate closes."""
    bronze = BronzeIngestionService(app_pool, object_store)
    with pytest.raises(IntakeRejected, match="not a recognised document type"):
        await bronze.receive(
            tenant_a.ctx, entity_id=uuid.uuid4(), data=b"doc_number\nX\n",
            document_type="PURCHASE_INVOICE", filename="a.csv",
        )


async def test_an_out_of_scope_registry_row_is_refused(
    app_pool: TenantScopedPool,
    object_store: S3ObjectStore,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A code that IS a row but `in_scope = false` — the CORPUS-owned rows are
    the real example (`INAFIN_CORPUS`, confirmed against `registry/README.md`
    §2 to belong to `inafin-gst-corpus`, never uploadable here)."""
    row = admin.execute(
        "SELECT doc_type_code FROM platform_ref.document_type"
        " WHERE NOT in_scope LIMIT 1"
    ).fetchone()
    assert row is not None, "fixture assumption: at least one out-of-scope row exists"
    code = str(row[0])

    bronze = BronzeIngestionService(app_pool, object_store)
    with pytest.raises(IntakeRejected, match="out of scope"):
        await bronze.receive(
            tenant_a.ctx, entity_id=uuid.uuid4(), data=b"doc_number\nX\n",
            document_type=code, filename="a.csv",
        )


async def test_an_in_scope_type_with_no_dispatch_mechanism_is_accepted(
    app_pool: TenantScopedPool, object_store: S3ObjectStore, tenant_a: SeededTenant,
) -> None:
    """GSTR_1 — Stream A, GSTN-API-polled, `dispatch_mechanism` empty — is
    in-scope and must be ACCEPTED at intake. This gate checks whether the
    registry knows the type, not whether it is dispatchable yet; conflating
    the two would refuse every one of the 38 UNSPECIFIED types this session's
    own catalogue work found, none of which are wrong to upload."""
    bronze = BronzeIngestionService(app_pool, object_store)
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=uuid.uuid4(), data=b"whatever\n",
        document_type="GSTR_1", filename="gstr1.csv",
    )
    assert receipt.ingest_id is not None
