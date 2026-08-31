"""`record_and_dispatch_trigger` — the implementation shared by
`POST /artefacts/{id}/trigger` and `tenantctl trigger`, twelfth-session item
#2's manual local trigger.

`test_api_ingest.py` already exercises every one of these paths through the
HTTP route (which now calls this same function — see
`src/dispatch/trigger.py`'s module docstring for why it was extracted). These
tests exercise the function directly, the same relationship
`test_register_specs.py` has to `RegisterLoader`: the route's tests prove HTTP
status codes map correctly; these prove the underlying behaviour the CLI also
depends on, without going through FastAPI.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from tests.conftest import SeededTenant
from tests.handoff.conftest import PERIOD_END, PERIOD_START

from src.bronze.service import BronzeIngestionService
from src.core.pool import TenantScopedPool
from src.dispatch.router import MissingDispatchFieldError
from src.dispatch.trigger import record_and_dispatch_trigger
from src.silver.registers import spec_for

pytestmark = pytest.mark.conformance

PURCHASE_REGISTER_SPEC = spec_for("PURCHASE_REGISTER")
GSTIN = "27AAFCI9876P1ZQ"


async def test_a_well_formed_trigger_dispatches_and_accepts(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC, seed="A", overrides={"invoice_no": "PI-TRIGGER-OK"}
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="PURCHASE_REGISTER",
    )

    result = await record_and_dispatch_trigger(
        app_pool, tenant_a.ctx, bronze,
        ingest_id=receipt.ingest_id, doc_type_code="PURCHASE_REGISTER",
        period_start=PERIOD_START, period_end=PERIOD_END, gstin=GSTIN,
    )
    assert result.status == "ACCEPTED"
    assert result.mechanism == "REGISTER_LOADER"
    assert result.batch_id is not None
    assert result.doc_type_code == "PURCHASE_REGISTER"


async def test_an_unknown_ingest_id_raises_foreign_key_violation(
    app_pool: TenantScopedPool, bronze: BronzeIngestionService, tenant_a: SeededTenant,
) -> None:
    """The caller-mistake shape — see the module docstring's split. Each
    caller (the route, the CLI) maps this to its own error surface; this
    function itself does not translate it."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await record_and_dispatch_trigger(
            app_pool, tenant_a.ctx, bronze,
            ingest_id=uuid.uuid4(), doc_type_code="PURCHASE_REGISTER",
        )


async def test_a_type_missing_a_required_dispatch_field_raises(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """REGISTER_LOADER needs period_start/period_end/gstin — omitted here on
    purpose. This must RAISE, not fold into `TriggerOutcome.status`: it is a
    caller mistake (a well-formed request would have supplied them), not a
    legitimate outcome of dispatch actually running."""
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC, seed="A", overrides={"invoice_no": "PI-TRIGGER-MISSING"}
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="PURCHASE_REGISTER",
    )

    with pytest.raises(MissingDispatchFieldError):
        await record_and_dispatch_trigger(
            app_pool, tenant_a.ctx, bronze,
            ingest_id=receipt.ingest_id, doc_type_code="PURCHASE_REGISTER",
        )


async def test_a_type_with_no_dispatch_mechanism_is_unrouted_not_raised(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """GSTR_1 — Stream A, GSTN-API-polled, `dispatch_mechanism` empty. This is
    the OTHER shape from the module docstring's split: a legitimate outcome
    of a well-formed trigger, folded into `status`, never raised. The trigger
    itself is still durably recorded — `trigger_id` is real."""
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=b"whatever\n", filename="gstr1.csv",
        document_type="GSTR_1",
    )

    result = await record_and_dispatch_trigger(
        app_pool, tenant_a.ctx, bronze,
        ingest_id=receipt.ingest_id, doc_type_code="GSTR_1",
    )
    assert result.status == "UNROUTED"
    assert result.mechanism is None
    assert result.trigger_id > 0


async def test_a_content_validation_failure_quarantines_not_raises(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """A malformed register (missing required columns) is a legitimate
    QUARANTINED outcome, not an exception — same split as the UNROUTED case
    above, different mechanism."""
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=b"not,the,right,columns\n1,2,3,4\n",
        filename="bad.csv", document_type="PURCHASE_REGISTER",
    )

    result = await record_and_dispatch_trigger(
        app_pool, tenant_a.ctx, bronze,
        ingest_id=receipt.ingest_id, doc_type_code="PURCHASE_REGISTER",
        period_start=PERIOD_START, period_end=PERIOD_END, gstin=GSTIN,
    )
    assert result.status == "QUARANTINED"
    assert result.batch_id is None

