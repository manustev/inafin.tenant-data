"""REST upload / trigger / status — src/api/routes_ingest.py.

Upload and status wrap real library calls (`BronzeIngestionService.receive`,
`SilverReader.artefact_outcome`) unchanged. Trigger is no longer a stub —
`test_trigger_dispatches_*` below exercise all four `src/dispatch/router.py`
mechanisms through the real HTTP surface, one per mechanism, the same way
`test_a_new_document_type_needs_no_new_code` proves the archetype claim for
CSV promotion: nothing about the route or the dispatcher names a specific
document type.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from tests.conformance.conftest import TOKEN_ACME, RecordingPublisher
from tests.conftest import SeededTenant

pytestmark = pytest.mark.conformance

AUTH = {"Authorization": f"Bearer {TOKEN_ACME}"}

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLES = ROOT / "reference" / "A1-A7Documents"

PERIOD_START = "2026-04-01"
PERIOD_END = "2026-04-30"
GSTIN = "27AAFCI9876P1ZQ"


def _generate_transaction_csv(code: str) -> bytes:
    """Same generator `test_transaction.py`'s `_generate` uses — a
    contract-conformant archetype-1 export for any registry code, so this
    file does not need its own hand-written ARCHETYPE1_PROMOTE fixture."""
    out = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "gen_mock_erp.py"),
         code, "--docs", "2", "--lines", "1"],
        capture_output=True, check=True, cwd=ROOT,
    )
    return out.stdout


def test_upload_then_status_is_pending_before_any_load(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """A fresh upload has nothing promoting it yet — PENDING, before any
    trigger runs."""
    resp = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "BILL_OF_ENTRY"},
        files={"file": ("export.csv", b"doc_number\nX-1\n", "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    ingest_id = body["ingest_id"]
    assert body["deduplicated"] is False

    status = api_client.get(f"/artefacts/{ingest_id}/status", headers=AUTH)
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "PENDING"


def test_upload_twice_deduplicates(api_client: TestClient, tenant_a: SeededTenant) -> None:
    data = {"entity_id": str(tenant_a.entity_id), "document_type": "BILL_OF_ENTRY"}
    files = {"file": ("export.csv", b"doc_number\nDEDUP-1\n", "text/csv")}
    first = api_client.post("/artefacts", headers=AUTH, data=data, files=files)
    second = api_client.post("/artefacts", headers=AUTH, data=data, files=files)

    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert first.json()["ingest_id"] == second.json()["ingest_id"]


def test_trigger_for_an_unknown_artefact_is_404(api_client: TestClient) -> None:
    resp = api_client.post(
        f"/artefacts/{uuid.uuid4()}/trigger",
        headers=AUTH,
        json={"doc_type_code": "BILL_OF_ENTRY"},
    )
    assert resp.status_code == 404


def test_status_of_an_already_accepted_seed_artefact(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """`tests/conftest.py` seeds a batch whose `bronze_manifest_ref` is
    `tenant_a.ingest_id` — reached without going through this file's own
    trigger calls."""
    status = api_client.get(f"/artefacts/{tenant_a.ingest_id}/status", headers=AUTH)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "ACCEPTED"
    assert body["rejected_count"] == 0


def test_trigger_dispatches_a_flat_register(
    api_client: TestClient, tenant_a: SeededTenant, batch_publisher: RecordingPublisher,
) -> None:
    """REGISTER_LOADER mechanism, end to end through HTTP: upload, trigger
    with the period/gstin a flat register needs, and see a real batch land —
    and see Pipeline 2's doorbell actually ring for it
    (`src/dispatch/router.py::_publish_if_ready`), the manifest-publish gap
    closed this session.

    A FRESH entity, not `tenant_a.entity_id` — that fixture is shared,
    session-scoped, and reused by unrelated tests; writing a real
    TRIAL_BALANCE batch under it would make this test's success depend on
    execution order, the exact bug `test_transaction.py`'s `_promote`
    docstring already explains.
    """
    entity_id = uuid.uuid4()
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "TRIAL_BALANCE"},
        files={
            "file": (
                "trial_balance.csv",
                (FIXTURES / "trial_balance_handwritten.csv").read_bytes(),
                "text/csv",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    ingest_id = upload.json()["ingest_id"]

    trigger = api_client.post(
        f"/artefacts/{ingest_id}/trigger",
        headers=AUTH,
        json={
            "doc_type_code": "TRIAL_BALANCE",
            "period_start": PERIOD_START, "period_end": PERIOD_END, "gstin": GSTIN,
        },
    )
    assert trigger.status_code == 202, trigger.text
    body = trigger.json()
    assert body["mechanism"] == "REGISTER_LOADER"
    assert body["status"] == "ACCEPTED"
    assert body["batch_id"] is not None

    status = api_client.get(f"/artefacts/{ingest_id}/status", headers=AUTH)
    assert status.json()["status"] == "ACCEPTED"

    assert len(batch_publisher.published) == 1
    manifest = batch_publisher.published[0]
    assert str(manifest.batch_id) == body["batch_id"]
    assert manifest.document_type == "TRIAL_BALANCE"
    assert manifest.entity_id == entity_id


def test_trigger_dispatches_sales_register(
    api_client: TestClient, tenant_a: SeededTenant, batch_publisher: RecordingPublisher,
) -> None:
    """SALES_REGISTER mechanism — the one hand-written loader, not a
    RegisterSpec entry. A fresh entity, same reason as the register test above."""
    entity_id = uuid.uuid4()
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "SALES_REGISTER"},
        files={
            "file": (
                "sales_register.csv",
                (FIXTURES / "sales_register_typed_handwritten.csv").read_bytes(),
                "text/csv",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    ingest_id = upload.json()["ingest_id"]

    trigger = api_client.post(
        f"/artefacts/{ingest_id}/trigger",
        headers=AUTH,
        json={
            "doc_type_code": "SALES_REGISTER",
            "period_start": PERIOD_START, "period_end": PERIOD_END, "gstin": GSTIN,
        },
    )
    assert trigger.status_code == 202, trigger.text
    body = trigger.json()
    assert body["mechanism"] == "SALES_REGISTER"
    assert body["status"] == "ACCEPTED"
    assert body["batch_id"] is not None

    assert len(batch_publisher.published) == 1
    assert str(batch_publisher.published[0].batch_id) == body["batch_id"]


def test_trigger_dispatches_archetype1_promote(
    api_client: TestClient, tenant_a: SeededTenant, batch_publisher: RecordingPublisher,
) -> None:
    """ARCHETYPE1_PROMOTE mechanism — an archetype-1 type with no
    RegisterSpec entry, so it must fall through to
    SilverPromotionService.promote_transaction_documents. A fresh entity:
    `test_isolation.py::test_support_is_read_only` counts
    `tenant_a`'s `transaction_document` rows exactly, so writing another one
    under that shared entity would make that unrelated test's assertion
    depend on this file having already run."""
    entity_id = uuid.uuid4()
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "ICEGATE_SHIPPING_BILL"},
        files={
            "file": (
                "shipping_bill.csv",
                _generate_transaction_csv("ICEGATE_SHIPPING_BILL"),
                "text/csv",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    ingest_id = upload.json()["ingest_id"]

    trigger = api_client.post(
        f"/artefacts/{ingest_id}/trigger",
        headers=AUTH,
        json={
            "doc_type_code": "ICEGATE_SHIPPING_BILL",
            "period_start": PERIOD_START, "period_end": PERIOD_END,
        },
    )
    assert trigger.status_code == 202, trigger.text
    body = trigger.json()
    assert body["mechanism"] == "ARCHETYPE1_PROMOTE"
    assert body["status"] == "ACCEPTED"
    assert body["batch_id"] is not None

    assert len(batch_publisher.published) == 1
    assert str(batch_publisher.published[0].batch_id) == body["batch_id"]


def test_trigger_dispatches_pdf_extraction(
    api_client: TestClient, tenant_a: SeededTenant, batch_publisher: RecordingPublisher,
) -> None:
    """PDF_EXTRACTION mechanism — a real specimen PDF, the same one
    `test_extraction_entitlement.py` extracts directly. No period/gstin
    needed: a PDF is a single dated instrument, not a period export. A fresh
    entity: `entitlement_instrument`'s natural key is (entity_id,
    instrument_type, instrument_number), and `test_extraction_entitlement
    .py` writes this exact LUT specimen under `tenant_a.entity_id` too."""
    entity_id = uuid.uuid4()
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "LUT"},
        files={
            "file": (
                "lut.pdf",
                (SAMPLES / "A5.01_LUT_Letter_of_Undertaking.pdf").read_bytes(),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    ingest_id = upload.json()["ingest_id"]

    trigger = api_client.post(
        f"/artefacts/{ingest_id}/trigger", headers=AUTH, json={"doc_type_code": "LUT"},
    )
    assert trigger.status_code == 202, trigger.text
    body = trigger.json()
    assert body["mechanism"] == "PDF_EXTRACTION"
    assert body["status"] == "ACCEPTED"
    # PDF_EXTRACTION's own ingest_batch row (create_single_document_batch)
    # is now surfaced through DispatchOutcome.batch_id — previously always
    # None, which is why this mechanism's manifest never used to publish.
    assert body["batch_id"] is not None

    assert len(batch_publisher.published) == 1
    assert str(batch_publisher.published[0].batch_id) == body["batch_id"]


def test_trigger_reports_unrouted_for_a_type_with_no_dispatch_mechanism(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """GSTR_1 is Stream A, GSTN-API-polled — never upload-triggered, so
    dispatch_mechanism is empty. The trigger is still durably recorded
    (load_trigger), it just does not resolve to a loader."""
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "GSTR_1"},
        files={"file": ("gstr1.csv", b"whatever\n", "text/csv")},
    )
    ingest_id = upload.json()["ingest_id"]

    trigger = api_client.post(
        f"/artefacts/{ingest_id}/trigger", headers=AUTH, json={"doc_type_code": "GSTR_1"},
    )
    assert trigger.status_code == 202, trigger.text
    assert trigger.json()["status"] == "UNROUTED"
    assert trigger.json()["mechanism"] is None


def test_trigger_is_422_when_a_required_field_is_missing(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """REGISTER_LOADER needs period_start/period_end/gstin — omitting them
    is a 422, not a silent no-op or a 500."""
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "TRIAL_BALANCE"},
        files={
            "file": (
                "trial_balance.csv",
                (FIXTURES / "trial_balance_handwritten.csv").read_bytes(),
                "text/csv",
            )
        },
    )
    ingest_id = upload.json()["ingest_id"]

    trigger = api_client.post(
        f"/artefacts/{ingest_id}/trigger",
        headers=AUTH,
        json={"doc_type_code": "TRIAL_BALANCE"},
    )
    assert trigger.status_code == 422, trigger.text
