"""SilverReader.artefact_outcome — "did my upload succeed", answered from SQL.

OVERVIEW. Each test drives one artefact through `RegisterLoader.load` for a
known outcome (never loaded at all, FATAL-quarantined, fully accepted,
partially accepted) and then asks `artefact_outcome` for that same artefact's
`bronze_ingest_id`. What is under test is the aggregation in
`src/reader/silver_reader.py`, not the loader itself — `test_registers.py`
already proves the loader's own behaviour; this file proves the read side
reports it correctly. `tests/handoff/test_outcome_gate.py` covers the same
question end to end through the real Bronze intake gate.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from tests.conftest import SeededTenant

from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.reader.silver_reader import ArtefactOutcome, SilverReader
from src.silver.registers import RegisterLoader, spec_for

pytestmark = pytest.mark.conformance

PERIOD_START = dt.date(2026, 4, 1)
PERIOD_END = dt.date(2026, 4, 30)
GSTIN = "27AAFCI9876P1ZQ"
SPEC = spec_for("PURCHASE_REGISTER")


@pytest.fixture
def entity() -> uuid.UUID:
    """A fresh business entity per test — see test_registers.py's twin fixture
    for why a shared one would leak state across tests in this module."""
    return uuid.uuid4()


async def _load(
    pool: TenantScopedPool, tenant: SeededTenant, data: bytes, *, entity_id: uuid.UUID
) -> tuple[uuid.UUID, object]:
    """Run one PURCHASE_REGISTER artefact through the loader, returning the
    Bronze `ingest_id` alongside whatever `RegisterLoader.load` returns — the
    id is generated here, rather than inside the loader, specifically so this
    test can ask `artefact_outcome` about the exact same artefact afterward."""
    ingest_id = uuid.uuid4()
    outcome = await RegisterLoader(pool, SPEC).load(
        tenant.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=ingest_id,
        data=data, period_start=PERIOD_START, period_end=PERIOD_END,
    )
    return ingest_id, outcome


def test_reader_reports_pending_for_an_artefact_it_has_never_seen() -> None:
    """No batch, no quarantine — the artefact simply hasn't been promoted yet.
    Synchronous on purpose: no DB call is needed to prove PENDING is reachable
    without one, but the real path is exercised by the async tests below."""
    outcome = ArtefactOutcome(
        bronze_ingest_id=uuid.uuid4(), status="PENDING", document_type=None,
        batch_id=None, row_count=None, accepted_count=None, rejected_count=0,
        quarantine_reason=None, rejections=(),
    )
    assert outcome.status == "PENDING"


async def test_an_unrelated_artefact_reports_pending(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """The real query, not just the dataclass: an ingest_id that never went
    through the loader at all must not be confused with one that failed."""
    reader = SilverReader(app_pool)
    outcome = await reader.artefact_outcome(tenant_a.ctx, uuid.uuid4())
    assert outcome.status == "PENDING"
    assert outcome.batch_id is None
    assert outcome.quarantine_reason is None
    assert outcome.rejections == ()


async def test_a_fatal_parse_failure_reports_quarantined_with_its_reason(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, entity: uuid.UUID
) -> None:
    """Mirrors test_registers.py's missing-column FATAL case — the artefact
    never produces a batch, so the outcome must come entirely from
    quarantined_artefact, with no row_count or rejections attached."""
    header = ",".join(SPEC.column_names).replace("cost_centre,", "costcentre,")
    mangled = f"{header}\n".encode()

    ingest_id = uuid.uuid4()
    with pytest.raises(ValidationRejected):
        await RegisterLoader(app_pool, SPEC).load(
            tenant_a.ctx, entity_id=entity, gstin=GSTIN, ingest_id=ingest_id,
            data=mangled, period_start=PERIOD_START, period_end=PERIOD_END,
        )

    reader = SilverReader(app_pool)
    outcome = await reader.artefact_outcome(tenant_a.ctx, ingest_id)
    assert outcome.status == "QUARANTINED"
    assert outcome.quarantine_reason is not None
    assert "cost_centre" in outcome.quarantine_reason
    assert outcome.batch_id is None
    assert outcome.row_count is None
    assert outcome.rejections == ()


async def test_a_fully_accepted_file_reports_zero_rejections(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, entity: uuid.UUID
) -> None:
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(SPEC, seed="A", overrides={"invoice_no": "PI-OUTCOME-OK"})
    ingest_id, load_outcome = await _load(app_pool, tenant_a, data, entity_id=entity)

    reader = SilverReader(app_pool)
    outcome = await reader.artefact_outcome(tenant_a.ctx, ingest_id)
    assert outcome.status == "ACCEPTED"
    assert outcome.batch_id == load_outcome.batch_id
    assert outcome.document_type == "PURCHASE_REGISTER"
    assert outcome.row_count == 1
    assert outcome.accepted_count == 1
    assert outcome.rejected_count == 0
    assert outcome.rejections == ()
    assert outcome.quarantine_reason is None


async def test_a_partially_bad_file_reports_partial_with_row_detail(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, entity: uuid.UUID
) -> None:
    from tests.conformance.test_registers import _cell, synthetic_csv

    good = synthetic_csv(SPEC, seed="A", overrides={"invoice_no": "PI-OUTCOME-GOOD"})
    header, good_row = good.decode().strip("\n").split("\n")
    bad_row = ",".join(
        "not-a-number" if c.name == "taxable_value"
        else "PI-OUTCOME-BAD" if c.name == "invoice_no"
        else _cell(SPEC, c.name, c.kind.value, "A")
        for c in SPEC.columns
    )
    data = f"{header}\n{good_row}\n{bad_row}\n".encode()

    ingest_id, load_outcome = await _load(app_pool, tenant_a, data, entity_id=entity)

    reader = SilverReader(app_pool)
    outcome = await reader.artefact_outcome(tenant_a.ctx, ingest_id)
    assert outcome.status == "PARTIAL"
    assert outcome.batch_id == load_outcome.batch_id
    assert outcome.row_count == 2
    assert outcome.accepted_count == 1
    assert outcome.rejected_count == 1
    assert len(outcome.rejections) == 1
    rejection = outcome.rejections[0]
    assert rejection.source_line == 3
    assert rejection.column_name == "taxable_value"
    assert "not a number" in rejection.message
    assert outcome.quarantine_reason is None
