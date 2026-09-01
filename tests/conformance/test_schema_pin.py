"""Schema pinning — the tenant's frozen copy of the contract they were handed.

Verified against the REAL dev MinIO and the REAL published release (`v1`,
published by `scripts/publish_schema_release.py`), not fakes: the whole point
of the pin is that specific bytes reach a specific tenant's bucket, and a fake
store would prove only that the code calls a method.

MUTATION CHECK for these: delete the `ensure_schema_pin` call from
`BronzeIngestionService.receive` and confirm exactly the first two tests fail;
make `put_schema_snapshot` raise and confirm `test_a_broken_store_does_not_fail
_the_upload` still passes while the others fail. Restore.
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.bronze.service import BronzeIngestionService
from src.catalogue.pin import current_pin, roll_forward, roll_forward_all
from src.core.pool import TenantScopedPool
from src.core.tenant import Role
from src.provisioning.objectstore import S3ObjectStore

pytestmark = pytest.mark.conformance

# A TABULAR type with a real published schema. Chosen over a PDF type because
# its schema file is non-trivial (14 DERIVED fields), so a wrong-object bug
# cannot hide behind an almost-empty document.
PINNED_TYPE = "PURCHASE_REGISTER"


def _csv_bytes() -> bytes:
    """Shape is irrelevant here — nothing promotes it. It only has to pass the
    Bronze intake gate, which checks file shape, not schema conformance.

    EVERY CALL RETURNS DIFFERENT BYTES, and that is load-bearing rather than
    tidy. Bronze dedups on `content_hash` (UNIQUE per tenant), so a fixed
    payload makes the second upload a dedup hit that never reaches the pinning
    code at all — which briefly made `test_a_second_upload_does_not_repin` pass
    for the wrong reason (dedup, not pin idempotency) and made
    `test_a_broken_store_does_not_fail_the_upload` fail depending on which
    tests had already run.
    """
    return f"supplier_gstin,invoice_no\n27AABCM4521F1Z5,INV-{uuid.uuid4()}\n".encode()


async def test_first_upload_pins_the_current_release(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    api_object_store: S3ObjectStore,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The pin names the CURRENT release, and the tenant's own bucket holds a
    byte-identical copy of the platform's published file.

    Byte-identity is the assertion that matters. A pin row naming a release
    proves only bookkeeping; the tenant is protected from a later v2 only if
    the BYTES they were handed are the ones frozen in their bucket.
    """
    bronze = BronzeIngestionService(
        app_pool, store=api_object_store, bucket_prefix="inafin-tenant",
    )
    await bronze.receive(
        tenant_a.ctx, entity_id=uuid.uuid4(), data=_csv_bytes(),
        document_type=PINNED_TYPE, filename=f"{uuid.uuid4()}.csv",
    )

    pin = await current_pin(app_pool, tenant_a.ctx, doc_type_code=PINNED_TYPE)
    assert pin is not None, "first upload of a type must pin a release"

    current = admin.execute(
        "SELECT version FROM platform_ref.schema_release WHERE status = 'CURRENT'"
    ).fetchone()
    assert current is not None, "fixture assumption: a release is published"
    assert pin.release_version == str(current[0])

    published = admin.execute(
        "SELECT object_bucket, object_key FROM platform_ref.schema_artifact"
        " WHERE release_version = %s AND kind = 'SCHEMA' AND doc_type_code = %s",
        (pin.release_version, PINNED_TYPE),
    ).fetchone()
    assert published is not None

    platform_copy = api_object_store.get(
        bucket=str(published[0]), key=str(published[1])
    )
    tenant_copy = api_object_store.get(bucket=pin.object_bucket, key=pin.object_key)
    assert tenant_copy == platform_copy

    # ...and it landed in THIS tenant's bucket, not the platform one.
    assert pin.object_bucket.endswith(tenant_a.slug.replace("_", "-"))
    assert pin.object_bucket != str(published[0])


async def test_a_second_upload_does_not_repin(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    api_object_store: S3ObjectStore,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Pinning happens once per document type, not once per upload.

    Depends on the previous test having run OR on pinning happening here —
    either way the invariant is the same, which is why this counts rows rather
    than asserting a fresh pin. `schema_pin` is INSERT-only, so a re-pin would
    show up as a second row and would be exactly the bug: a tenant silently
    moved to a new release by their own second upload.
    """
    bronze = BronzeIngestionService(
        app_pool, store=api_object_store, bucket_prefix="inafin-tenant",
    )
    for _ in range(2):
        await bronze.receive(
            tenant_a.ctx, entity_id=uuid.uuid4(), data=_csv_bytes(),
            document_type=PINNED_TYPE, filename=f"{uuid.uuid4()}.csv",
        )

    n = admin.execute(
        f"SELECT count(*) FROM t_{tenant_a.slug}_bronze.schema_pin"
        " WHERE doc_type_code = %s",
        (PINNED_TYPE,),
    ).fetchone()
    assert n is not None and int(str(n[0])) == 1


async def test_a_pending_type_is_still_pinned_and_says_so(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    api_object_store: S3ObjectStore,
) -> None:
    """A type with no field list is published anyway, and the file says why.

    This test originally asserted the opposite — that an UNSPECIFIED type pins
    nothing — and it was wrong about the design, not about the code. The
    catalogue covers all 125 in-scope types deliberately (the alternative was
    showing a tenant two-thirds of the registry and silently omitting the
    rest), so `COURT_STAY_ORDER` gets a real schema file whose contents are an
    honest "no field list yet, send us the document": schema_kind UNSPECIFIED,
    provenance PENDING, `fields` empty.

    That is the useful answer for a tenant. An absent file would be
    indistinguishable from a broken pipeline.
    """
    bronze = BronzeIngestionService(
        app_pool, store=api_object_store, bucket_prefix="inafin-tenant",
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=uuid.uuid4(), data=_csv_bytes(),
        document_type="COURT_STAY_ORDER", filename=f"{uuid.uuid4()}.csv",
    )
    assert receipt.ingest_id is not None

    pin = await current_pin(
        app_pool, tenant_a.ctx, doc_type_code="COURT_STAY_ORDER"
    )
    assert pin is not None

    body = json.loads(
        api_object_store.get(bucket=pin.object_bucket, key=pin.object_key)
    )
    assert body["doc_type_code"] == "COURT_STAY_ORDER"
    assert body["provenance"] == "PENDING"
    assert body["fields"] == []


async def test_a_broken_store_does_not_fail_the_upload(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    api_object_store: S3ObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning is a record, not a gate — see `pin.py`'s module docstring.

    A catalogue misconfiguration or an unreachable platform bucket must cost
    the tenant a pin, never their upload. Without this test the best-effort
    try/except is indistinguishable from an untested happy path.
    """
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("platform bucket unreachable")

    monkeypatch.setattr(api_object_store, "put_schema_snapshot", _boom)

    bronze = BronzeIngestionService(
        app_pool, store=api_object_store, bucket_prefix="inafin-tenant",
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=uuid.uuid4(), data=_csv_bytes(),
        document_type="SALES_REGISTER", filename=f"{uuid.uuid4()}.csv",
    )
    assert receipt.ingest_id is not None
    assert receipt.deduplicated is False


async def test_roll_forward_replaces_the_pin_and_never_touches_the_prior_row(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    api_object_store: S3ObjectStore,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """`tenantctl reschema` — Step 4c of the ERP upload E2E remediation
    (2026-09-01): the constraint columns (migration 041/042) only reach a
    tenant that re-pins, and `schema_pin` is insert-only, so an operator
    command has to add a NEW row rather than editing the old one. That is
    exactly what migration 028 built `pinned_by_ingest_id` nullable for.

    Simulates a tenant already pinned to an OLDER release by inserting the
    row directly rather than going through `ensure_schema_pin` — that
    function always pins CURRENT, so it cannot itself produce a tenant who
    needs rolling forward once only one release has ever existed. A real
    superseded release (`v1`, still present in `schema_artifact` — releases
    are never deleted) makes this a realistic prior state, not a fake one.
    """
    doc_type = "ADVANCE_RECEIPT_REGISTER"  # untouched by other pin tests

    # Read the actual CURRENT release rather than assuming "v2" — releases
    # are published outside this test's control (`scripts/publish_schema_
    # release.py`, run by an operator or another team's environment setup),
    # so a hardcoded version string is a test bug waiting for the next
    # publish. What must hold is "rolled forward to whatever IS current",
    # not "rolled forward to the release that was current when this test was
    # written". v3 broke exactly this assumption the first time it was
    # published, which is how this was found.
    (current_version,) = admin.execute(
        "SELECT version FROM platform_ref.schema_release WHERE status = 'CURRENT'"
    ).fetchone()

    async with app_pool.transaction(tenant_a.ctx, Role.INGEST) as conn:
        await conn.execute(
            sql.SQL(
                "INSERT INTO {}.schema_pin"
                " (doc_type_code, release_version, schema_sha256,"
                "  object_bucket, object_key, pinned_at)"
                " VALUES (%s, 'v1', '\\x00', 'x', 'x', now())"
            ).format(sql.Identifier(tenant_a.ctx.bronze_schema)),
            (doc_type,),
        )

    before = await current_pin(app_pool, tenant_a.ctx, doc_type_code=doc_type)
    assert before is not None and before.release_version == "v1"

    rolled = await roll_forward(
        app_pool, tenant_a.ctx, doc_type_code=doc_type, store=api_object_store,
    )
    assert rolled is not None
    assert rolled.release_version == current_version

    after = await current_pin(app_pool, tenant_a.ctx, doc_type_code=doc_type)
    assert after is not None and after.release_version == current_version

    # The v1 row must still be there — insert-only, never edited or replaced.
    rows = admin.execute(
        sql.SQL(
            "SELECT release_version, pinned_by_ingest_id"
            "  FROM {}.schema_pin WHERE doc_type_code = %s ORDER BY release_version"
        ).format(sql.Identifier(tenant_a.ctx.bronze_schema)),
        (doc_type,),
    ).fetchall()
    assert [r[0] for r in rows] == ["v1", current_version]
    # NULL, not the artefact that (falsely) would imply this pin came from an
    # upload — migration 028's exact case for a nullable column.
    assert rows[1][1] is None

    body = api_object_store.get(bucket=rolled.object_bucket, key=rolled.object_key)
    assert json.loads(body)["release"] == current_version


async def test_roll_forward_twice_is_a_no_op(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    api_object_store: S3ObjectStore,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """An operator re-running `tenantctl reschema` after a tenant is already
    current must not append a redundant row every time."""
    doc_type = "UNBILLED_REVENUE_SCHEDULE"
    first = await roll_forward(
        app_pool, tenant_a.ctx, doc_type_code=doc_type, store=api_object_store,
    )
    second = await roll_forward(
        app_pool, tenant_a.ctx, doc_type_code=doc_type, store=api_object_store,
    )
    assert first is not None and second is not None
    assert first.release_version == second.release_version

    count = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.schema_pin WHERE doc_type_code = %s"
        ).format(sql.Identifier(tenant_a.ctx.bronze_schema)),
        (doc_type,),
    ).fetchone()
    assert count is not None and count[0] == 1


async def test_roll_forward_all_only_touches_types_this_tenant_has_pinned(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    api_object_store: S3ObjectStore,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """`--doc-type` omitted means "everything this tenant already has", not
    "every in-scope type in the registry" — a tenant who has never uploaded
    GSTR_9C should not be handed a pin for it just because an operator ran
    this without an argument."""
    doc_type = "JOBWORK_DISPATCH_REGISTER"
    await roll_forward(
        app_pool, tenant_a.ctx, doc_type_code=doc_type, store=api_object_store,
    )

    pinned_types = {
        str(r[0])
        for r in admin.execute(
            sql.SQL("SELECT DISTINCT doc_type_code FROM {}.schema_pin").format(
                sql.Identifier(tenant_a.ctx.bronze_schema)
            )
        ).fetchall()
    }

    rolled = await roll_forward_all(app_pool, tenant_a.ctx, store=api_object_store)
    rolled_types = {p.doc_type_code for p in rolled}

    assert rolled_types == pinned_types
    assert "GSTR_9C" not in rolled_types
