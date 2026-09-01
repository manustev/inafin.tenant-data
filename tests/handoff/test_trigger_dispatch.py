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

import pathlib
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant
from tests.handoff.conftest import PERIOD_END, PERIOD_START

from src.bronze.service import BronzeIngestionService
from src.core.errors import SilverConstraintViolation, UnknownArtefact
from src.core.pool import TenantScopedPool
from src.dispatch.router import MissingDispatchFieldError
from src.dispatch.trigger import record_and_dispatch_trigger
from src.extraction.reader import PypdfReader
from src.provisioning.objectstore import ObjectStorePort
from src.silver.registers import spec_for

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
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


async def test_an_unknown_ingest_id_raises_unknown_artefact(
    app_pool: TenantScopedPool, bronze: BronzeIngestionService, tenant_a: SeededTenant,
) -> None:
    """The caller-mistake shape — see the module docstring's split. Each
    caller (the route, the CLI) maps this to its own error surface; this
    function itself does not choose a status code.

    `UnknownArtefact` rather than the raw `psycopg.errors.ForeignKeyViolation`
    this used to raise: a FK violation from the DISPATCH below means something
    entirely different (a Silver row referencing an unknown batch, doc type or
    master value) and must not reach a caller as "no such artefact". Only this
    module knows which statement raised, so only this module can tell them
    apart.
    """
    with pytest.raises(UnknownArtefact):
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



async def test_a_domain_check_violation_raises_invalid_not_a_bare_psycopg_error(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """The gap the ERP upload E2E suite found (2026-09-01), reproduced.

    `supplier_gstin` publishes in the schema catalogue as plain `text`, so a
    client can generate a CSV that is valid against the schema it downloaded
    and still be refused: the column is `platform_ref.gstin`, a domain whose
    CHECK enforces the embedded PAN. Nothing before the INSERT catches this —
    the register loader validates shape, not the domain — so Postgres is the
    first thing to say no, and before this it reached the caller as an opaque
    HTTP 500.

    This asserts the SHAPE of that refusal, not the schema gap itself: the
    published contract is a separate fix. What must hold here is that the
    error arrives typed, with the constraint that fired, and classified
    INVALID (a value the client can correct) rather than CONFLICT.
    """
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC,
        seed="A",
        overrides={
            "supplier_gstin": "NOTAGSTIN12345",
            "invoice_no": f"PI-BADGSTIN-{uuid.uuid4().hex[:8]}",
        },
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="bad_gstin.csv",
        document_type="PURCHASE_REGISTER",
    )

    with pytest.raises(SilverConstraintViolation) as caught:
        await record_and_dispatch_trigger(
            app_pool, tenant_a.ctx, bronze,
            ingest_id=receipt.ingest_id, doc_type_code="PURCHASE_REGISTER",
            period_start=PERIOD_START, period_end=PERIOD_END, gstin=GSTIN,
        )

    assert caught.value.kind == "INVALID"
    # Postgres names the domain's constraint but populates neither column nor
    # table for a domain check — the honest Nones `SilverConstraintViolation`
    # documents, not a gap in the mapping.
    assert caught.value.constraint == "gstin_check"


async def test_resubmitting_a_document_supersedes_the_prior_reading(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    object_store: ObjectStorePort,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Re-dispatching an artefact CORRECTS its row rather than failing.

    Until 2026-09-01 the extraction path only ever called `record()`, a blind
    INSERT, so the second dispatch of any document had to violate
    `narrative_contract_current_uq` — the ERP upload E2E finding. The index
    was never the problem and is unchanged; what was missing was the LOOKUP
    that finds the row to close (`src/silver/supersede.py`).

    Asserted as a CHAIN, not just "the second call succeeded": exactly one
    row current, every earlier one closed, and each new row pointing back at
    the one it replaced. A supersede that closed the prior row without
    linking to it would satisfy the index and still lose the audit trail
    that makes Silver bitemporal.

    A fresh entity keeps this off the shared fixture's rows; the per-run
    marker in the bytes keeps Bronze's content dedup from handing back a
    prior run's `ingest_id` and making the first dispatch the superseding one.
    """
    entity_id = uuid.uuid4()
    data = (
        (ROOT / "reference" / "A1-A7Documents" / "A4.01_Cost_Sharing_Agreement.pdf")
        .read_bytes()
        + b"\n%% run " + uuid.uuid4().hex.encode() + b"\n"
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data,
        filename="A4.01_Cost_Sharing_Agreement.pdf",
        document_type="COST_SHARING_AGREEMENT",
    )
    assert not receipt.deduplicated

    for _ in range(3):
        outcome = await record_and_dispatch_trigger(
            app_pool, tenant_a.ctx, bronze, ingest_id=receipt.ingest_id,
            doc_type_code="COST_SHARING_AGREEMENT",
            store=object_store, reader=PypdfReader(),
        )
        assert outcome.status == "ACCEPTED"

    rows = admin.execute(
        sql.SQL(
            "SELECT contract_id, supersedes_contract_id, superseded_at IS NULL"
            "  FROM {}.narrative_contract WHERE entity_id = %s"
            " ORDER BY recorded_at"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (entity_id,),
    ).fetchall()

    assert len(rows) == 3, "each dispatch must append a row, not overwrite one"
    assert [r[2] for r in rows] == [False, False, True], "exactly one row is current"
    assert rows[0][1] is None, "the first reading supersedes nothing"
    assert rows[1][1] == rows[0][0]
    assert rows[2][1] == rows[1][0]


async def test_a_unique_violation_still_maps_to_conflict(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Supersede removed the ROUTINE cause of a unique violation, not the
    possibility of one.

    Two concurrent dispatches of the same document can both find no current
    row and both insert; `close_current` takes a `FOR UPDATE` to narrow that
    window, but the index is still the last word and a caller must get a
    usable answer when it speaks. That path is genuinely hard to provoke
    deterministically, so the violation is faked here — what is under test is
    the CLASSIFICATION (CONFLICT, not INVALID), which is the part that
    decides whether a client sees 409 or 422.
    """
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC, seed="A",
        overrides={"invoice_no": f"PI-UQ-{uuid.uuid4().hex[:8]}"},
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="PURCHASE_REGISTER",
    )

    async def _raise_unique(*args: object, **kwargs: object) -> object:
        raise psycopg.errors.UniqueViolation(
            'duplicate key value violates unique constraint '
            '"narrative_contract_current_uq"'
        )

    monkeypatch.setattr("src.dispatch.trigger.dispatch_load", _raise_unique)

    with pytest.raises(SilverConstraintViolation) as caught:
        await record_and_dispatch_trigger(
            app_pool, tenant_a.ctx, bronze,
            ingest_id=receipt.ingest_id, doc_type_code="PURCHASE_REGISTER",
            period_start=PERIOD_START, period_end=PERIOD_END, gstin=GSTIN,
        )
    assert caught.value.kind == "CONFLICT"


async def test_a_foreign_key_violation_from_silver_is_not_reported_as_a_missing_artefact(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason `UnknownArtefact` is scoped to one statement.

    `load_trigger`'s only foreign key is to `artefact_ledger`, so a violation
    THERE means the artefact does not exist. Silver's typed tables carry
    several of their own — `batch_id` to `ingest_batch`, the composite
    (type, archetype) to `platform_ref.document_type`, `universal_master`
    vocabulary keys — and a violation from one of THOSE means a row
    referenced something unknown, which is a 422 about the file, not a 404
    about the artefact. Catching the psycopg type around the whole function,
    as this route did before 2026-09-01, could not tell the two apart and
    reported the second as the first.

    `dispatch_load` is faked rather than provoked with a real file: engineering
    a document whose extraction yields an unrecognised `universal_master`
    value would test the extractor's vocabulary handling, not this module's
    error routing, and the routing is what a mutation check showed nothing
    covered. The fake raises the exact exception type the real path would.
    """
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC,
        seed="A",
        overrides={"invoice_no": f"PI-SILVER-FK-{uuid.uuid4().hex[:8]}"},
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="PURCHASE_REGISTER",
    )

    async def _raise_silver_fk(*args: object, **kwargs: object) -> object:
        raise psycopg.errors.ForeignKeyViolation(
            'insert or update on table "purchase_register" violates foreign key'
            ' constraint "purchase_register_batch_id_fkey"'
        )

    monkeypatch.setattr("src.dispatch.trigger.dispatch_load", _raise_silver_fk)

    with pytest.raises(SilverConstraintViolation) as caught:
        await record_and_dispatch_trigger(
            app_pool, tenant_a.ctx, bronze,
            ingest_id=receipt.ingest_id, doc_type_code="PURCHASE_REGISTER",
            period_start=PERIOD_START, period_end=PERIOD_END, gstin=GSTIN,
        )
    assert caught.value.kind == "INVALID"
