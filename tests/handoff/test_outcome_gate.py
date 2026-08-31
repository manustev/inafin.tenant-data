"""artefact_outcome, driven through the real Bronze intake gate.

OVERVIEW. tests/conformance/test_outcome.py proves SilverReader.artefact_outcome
aggregates ingest_batch / rejected_row / quarantined_artefact correctly, using
a loader-generated ingest_id. This file proves the same answer holds for an
ingest_id that actually came from `BronzeIngestionService.receive` — the id a
real upload API would hand back to a customer — end to end through both
failure paths this repo has: the archetype-1 file-level quarantine
(BILL_OF_ENTRY, via SilverPromotionService) and the register loader's row-level
rejection (PURCHASE_REGISTER, via RegisterLoader). Together these are the
answer to the question that started this work: "did my upload succeed, and if
not, why."
"""

from __future__ import annotations

import uuid

import pytest
from tests.conftest import SeededTenant
from tests.handoff.conftest import PERIOD_END, PERIOD_START

from src.bronze.service import BronzeIngestionService
from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.reader.silver_reader import SilverReader
from src.silver.promote import SilverPromotionService
from src.silver.registers import RegisterLoader, spec_for

pytestmark = pytest.mark.handoff

PURCHASE_REGISTER_SPEC = spec_for("PURCHASE_REGISTER")
GSTIN = "27AAFCI9876P1ZQ"


async def test_a_never_uploaded_ingest_id_is_pending(
    reader: SilverReader, tenant_a: SeededTenant
) -> None:
    outcome = await reader.artefact_outcome(tenant_a.ctx, uuid.uuid4())
    assert outcome.status == "PENDING"


async def test_a_quarantined_upload_is_visible_through_its_own_ingest_id(
    bronze: BronzeIngestionService,
    promoter: SilverPromotionService,
    reader: SilverReader,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """The archetype-1 path: a structurally invalid file never becomes a
    batch, and the caller learns why from the same ingest_id Bronze handed
    back on upload — no separate error channel is needed."""
    bad = (
        b"doc_number,counterparty_gstin,counterparty_name,doc_date,be_number,"
        b"be_date,port_code,line_number,hsn_sac,quantity,unit_price,"
        b"taxable_value,gst_rate\n"
        b"BE-OUTCOME-BAD,27AAAPA1234A1Z5,Some Overseas Ltd,2026-04-15,"
        b"BE-OUTCOME-BAD,2026-04-15,INNSA1,1,84713010,10,1000,10000,18\n"
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=bad, filename="bad.csv",
        document_type="BILL_OF_ENTRY",
    )
    with pytest.raises(ValidationRejected):
        await promoter.promote_transaction_documents(
            tenant_a.ctx, document_type="BILL_OF_ENTRY", ingest_id=receipt.ingest_id,
            entity_id=entity_id, data=bad, period_start=PERIOD_START, period_end=PERIOD_END,
        )

    outcome = await reader.artefact_outcome(tenant_a.ctx, receipt.ingest_id)
    assert outcome.status == "QUARANTINED"
    assert outcome.quarantine_reason is not None
    assert "overseas counterparty" in outcome.quarantine_reason
    assert outcome.batch_id is None


async def test_a_fully_accepted_upload_is_visible_through_its_own_ingest_id(
    bronze: BronzeIngestionService,
    reader: SilverReader,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """The register-loader path, all rows good: ACCEPTED, no rejections."""
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC, seed="A", overrides={"invoice_no": "PI-GATE-OK"}
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="good.csv",
        document_type="PURCHASE_REGISTER",
    )
    load_outcome = await RegisterLoader(app_pool, PURCHASE_REGISTER_SPEC).load(
        tenant_a.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=receipt.ingest_id,
        data=data, period_start=PERIOD_START, period_end=PERIOD_END,
    )

    outcome = await reader.artefact_outcome(tenant_a.ctx, receipt.ingest_id)
    assert outcome.status == "ACCEPTED"
    assert outcome.batch_id == load_outcome.batch_id
    assert outcome.rejected_count == 0
    assert outcome.accepted_count == outcome.row_count


async def test_a_partially_accepted_upload_is_visible_through_its_own_ingest_id(
    bronze: BronzeIngestionService,
    reader: SilverReader,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """The register-loader path, one bad row among good ones: PARTIAL, with
    the rejection detail attached — the exact "successful but with an issue"
    report row-level rejection exists to make possible."""
    from tests.conformance.test_registers import _cell, synthetic_csv

    good = synthetic_csv(
        PURCHASE_REGISTER_SPEC, seed="A", overrides={"invoice_no": "PI-GATE-GOOD"}
    )
    header, good_row = good.decode().strip("\n").split("\n")
    bad_row = ",".join(
        "not-a-number" if c.name == "taxable_value"
        else "PI-GATE-BAD" if c.name == "invoice_no"
        else _cell(PURCHASE_REGISTER_SPEC, c.name, c.kind.value, "A")
        for c in PURCHASE_REGISTER_SPEC.columns
    )
    data = f"{header}\n{good_row}\n{bad_row}\n".encode()

    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="partial.csv",
        document_type="PURCHASE_REGISTER",
    )
    await RegisterLoader(app_pool, PURCHASE_REGISTER_SPEC).load(
        tenant_a.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=receipt.ingest_id,
        data=data, period_start=PERIOD_START, period_end=PERIOD_END,
    )

    outcome = await reader.artefact_outcome(tenant_a.ctx, receipt.ingest_id)
    assert outcome.status == "PARTIAL"
    assert outcome.accepted_count == 1
    assert outcome.rejected_count == 1
    assert outcome.rejections[0].column_name == "taxable_value"
