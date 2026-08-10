"""Archetype 3 gates — the entitlement instrument.

Thirty-three document types collapse into one table, so a defect here is a
defect in a third of the register. The gates fall into four groups:

  * the query that matters — type, date, HSN, standing
  * HSN hierarchy — the inverted-containment trick must actually work
  * bitemporal correction — supersession must not withdraw historical truth
  * the boundary — instruments are tenant data like any other
"""

from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.core.errors import TenantBoundaryViolation
from src.core.pool import TenantScopedPool
from src.core.tenant import Role
from src.reader.entitlement_reader import EntitlementReader, hsn_prefixes
from src.silver.entitlement import EntitlementService, InstrumentRecord

pytestmark = pytest.mark.conformance

FY25 = dt.date(2024, 4, 1)
FY25_END = dt.date(2025, 3, 31)
IN_FY25 = dt.date(2024, 9, 15)


def _batch_id(
    admin: psycopg.Connection[tuple[object, ...]], tenant: SeededTenant
) -> uuid.UUID:
    row = admin.execute(
        sql.SQL("SELECT batch_id FROM {}.ingest_batch LIMIT 1").format(
            sql.Identifier(tenant.ctx.silver_schema)
        )
    ).fetchone()
    assert row is not None, "seed failed — no batch to anchor instruments to"
    return uuid.UUID(str(row[0]))


async def _lut(
    svc: EntitlementService,
    tenant: SeededTenant,
    batch: uuid.UUID,
    **over: object,
) -> uuid.UUID:
    fields: dict[str, object] = {
        "entity_id": tenant.entity_id,
        "instrument_type": "LUT",
        "issuing_authority": "GSTN",
        # Unique per call: the tenant fixture is session-scoped, and
        # entitlement_instrument_current_uq (correctly) refuses a second current
        # version of the same instrument. Tests that assert on the number set it.
        "instrument_number": f"AD29042400{uuid.uuid4().hex[:8]}",
        "valid_from": FY25,
        "valid_to": FY25_END,
        "batch_id": batch,
        "bronze_ingest_id": uuid.uuid4(),
    }
    fields.update(over)
    return await svc.record(tenant.ctx, InstrumentRecord(**fields))  # type: ignore[arg-type]


# =============================================================================
# The query that matters
# =============================================================================


async def test_active_instrument_confers_entitlement_on_a_date_in_window(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    svc, reader = EntitlementService(app_pool), EntitlementReader(app_pool)
    await _lut(svc, tenant_a, _batch_id(admin, tenant_a))

    assert await reader.is_entitled(
        tenant_a.ctx, entity_id=tenant_a.entity_id,
        instrument_type="LUT", as_of=IN_FY25,
    )


async def test_entitlement_is_denied_outside_the_validity_window(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The date of the SUPPLY decides, not today.

    An export invoice dated one day after the LUT expired is not covered by it,
    even though the taxpayer plainly holds a LUT. This is the A8 finding the
    whole archetype exists to produce.
    """
    svc, reader = EntitlementService(app_pool), EntitlementReader(app_pool)
    await _lut(svc, tenant_a, _batch_id(admin, tenant_a))

    for outside in (FY25 - dt.timedelta(days=1), FY25_END + dt.timedelta(days=1)):
        assert not await reader.is_entitled(
            tenant_a.ctx, entity_id=tenant_a.entity_id,
            instrument_type="LUT", as_of=outside,
        ), f"{outside} is outside the window but was treated as entitled"


async def test_suspended_instrument_inside_its_window_confers_nothing(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Standing and expiry are independent axes.

    A SEZ unit with a rejected Annual Performance Report loses its zero-rating
    from the suspension date while its LoA validity window runs on. Reading
    entitlement off `valid_to` alone would keep granting it.
    """
    svc, reader = EntitlementService(app_pool), EntitlementReader(app_pool)
    await _lut(
        svc, tenant_a, _batch_id(admin, tenant_a),
        instrument_number="SUSPENDED-001", status="SUSPENDED",
    )

    found = await reader.find(
        tenant_a.ctx, entity_id=tenant_a.entity_id,
        instrument_type="LUT", as_of=IN_FY25,
    )
    assert all(i.instrument_number != "SUSPENDED-001" for i in found)


# =============================================================================
# HSN hierarchy
# =============================================================================


def test_hsn_prefixes_expand_to_every_level() -> None:
    assert hsn_prefixes("84713010") == ["84", "8471", "847130", "84713010"]
    assert hsn_prefixes("8471") == ["84", "8471"]
    assert hsn_prefixes("") == []
    # 10-digit customs tariff item keeps its full form as the final prefix, so an
    # exactly-scoped instrument still matches.
    assert hsn_prefixes("8471301000")[-1] == "8471301000"


async def test_instrument_scoped_to_a_heading_covers_its_tariff_items(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """An EPCG licence scoped to '8471' must cover supply of '84713010'.

    This is the inverted-containment design: the caller expands the queried HSN
    and the stored array is tested for overlap. Getting it backwards would deny
    every instrument scoped above tariff-item level — which is most of them.
    """
    svc, reader = EntitlementService(app_pool), EntitlementReader(app_pool)
    await _lut(
        svc, tenant_a, _batch_id(admin, tenant_a),
        instrument_type="EPCG_LICENCE", issuing_authority="DGFT",
        instrument_number="EPCG-0001", scope_hsn=["8471"],
    )

    assert await reader.is_entitled(
        tenant_a.ctx, entity_id=tenant_a.entity_id,
        instrument_type="EPCG_LICENCE", as_of=IN_FY25, hsn="84713010",
    ), "heading-scoped instrument did not cover its tariff item"

    assert not await reader.is_entitled(
        tenant_a.ctx, entity_id=tenant_a.entity_id,
        instrument_type="EPCG_LICENCE", as_of=IN_FY25, hsn="61091000",
    ), "instrument covered an HSN outside its scope"


async def test_null_hsn_scope_means_unrestricted(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A LUT covers all exports; it is not scoped by HSN.

    NULL must match every queried HSN. Treating NULL as "covers nothing" would
    deny every LUT-backed export in the platform.
    """
    svc, reader = EntitlementService(app_pool), EntitlementReader(app_pool)
    await _lut(
        svc, tenant_a, _batch_id(admin, tenant_a), instrument_number="LUT-UNRESTRICTED"
    )

    for hsn in ("84713010", "61091000", "99831"):
        assert await reader.is_entitled(
            tenant_a.ctx, entity_id=tenant_a.entity_id,
            instrument_type="LUT", as_of=IN_FY25, hsn=hsn,
        ), f"unrestricted LUT failed to cover {hsn}"


# =============================================================================
# Bitemporal correction
# =============================================================================


async def test_supersession_replaces_current_belief_without_losing_the_prior(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A correction closes the prior version and appends; nothing is overwritten.

    The superseded row must remain in the table — it is what we believed when we
    released an earlier report, and in a hearing that is the question asked.
    """
    svc, reader = EntitlementService(app_pool), EntitlementReader(app_pool)
    batch = _batch_id(admin, tenant_a)
    prior = await _lut(svc, tenant_a, batch, instrument_number="LUT-CORRECT-ME")

    corrected = await svc.supersede(
        tenant_a.ctx,
        prior_id=prior,
        rec=InstrumentRecord(
            entity_id=tenant_a.entity_id, instrument_type="LUT",
            issuing_authority="GSTN", instrument_number="LUT-CORRECT-ME",
            valid_from=FY25, valid_to=FY25_END,
            batch_id=batch, bronze_ingest_id=uuid.uuid4(),
        ),
    )

    live = [
        i for i in await reader.find(
            tenant_a.ctx, entity_id=tenant_a.entity_id,
            instrument_type="LUT", as_of=IN_FY25,
        )
        if i.instrument_number == "LUT-CORRECT-ME"
    ]
    assert len(live) == 1, "exactly one version must be current"
    assert live[0].instrument_id == corrected

    # The prior version still exists, and names its successor's ancestry.
    rows = admin.execute(
        sql.SQL(
            "SELECT superseded_at IS NOT NULL FROM {}.entitlement_instrument"
            " WHERE instrument_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (prior,),
    ).fetchone()
    assert rows is not None and rows[0] is True, "prior version was destroyed"


async def test_two_current_versions_of_one_instrument_are_impossible(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The resubmission failure mode, refused by the database.

    Two ingests of the same LUT both landing as current would make the
    entitlement query return it twice and make "which one is true" unanswerable.
    """
    svc = EntitlementService(app_pool)
    batch = _batch_id(admin, tenant_a)
    await _lut(svc, tenant_a, batch, instrument_number="LUT-DUPLICATE")

    with pytest.raises(psycopg.errors.UniqueViolation):
        await _lut(svc, tenant_a, batch, instrument_number="LUT-DUPLICATE")


# =============================================================================
# The registry is load-bearing, and the boundary holds
# =============================================================================


async def test_only_archetype_3_document_types_can_be_instruments(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Storing a GSTR-1 here is a foreign key violation, not a review comment.

    The composite FK (instrument_type, 3) -> document_type(doc_type_code,
    archetype) is what makes the Document Type Registry enforce the archetype
    split rather than merely describe it.
    """
    svc = EntitlementService(app_pool)
    batch = _batch_id(admin, tenant_a)

    # POSITIVE CONTROL: GSTR_1 is a real registry row — just not archetype 3.
    got = admin.execute(
        "SELECT archetype FROM platform_ref.document_type WHERE doc_type_code = 'GSTR_1'"
    ).fetchone()
    assert got is not None and got[0] == 5

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await svc.record(
            tenant_a.ctx,
            InstrumentRecord(
                entity_id=tenant_a.entity_id, instrument_type="GSTR_1",
                issuing_authority="GSTN", instrument_number="NOT-AN-INSTRUMENT",
                valid_from=FY25, batch_id=batch, bronze_ingest_id=uuid.uuid4(),
            ),
        )


async def test_one_tenant_cannot_read_another_tenants_instruments(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Entitlements are tenant data. Knowing a competitor holds an EPCG licence
    is commercially material, so the same boundary applies as to invoices."""
    svc = EntitlementService(app_pool)
    await _lut(
        svc, tenant_b, _batch_id(admin, tenant_b), instrument_number="LUT-B-SECRET"
    )

    # POSITIVE CONTROL: it genuinely exists.
    control = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.entitlement_instrument"
            " WHERE instrument_number = 'LUT-B-SECRET'"
        ).format(sql.Identifier(tenant_b.ctx.silver_schema))
    ).fetchone()
    assert control is not None and control[0] == 1, "seed failed — gate is vacuous"

    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.v1_entitlement_instrument").format(
                    sql.Identifier(tenant_b.ctx.silver_schema)
                )
            )


async def test_recon_cannot_reach_the_instrument_base_table(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """The v1_ view is the entire read contract, for this table as for the rest."""
    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.entitlement_instrument").format(
                    sql.Identifier(tenant_a.ctx.silver_schema)
                )
            )
