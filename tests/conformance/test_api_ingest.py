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

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
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


def test_a_quarantined_sales_register_transitions_status_out_of_pending(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """The `inafin_test_01` finding (2026-09-01): a synchronously
    QUARANTINED trigger left `GET /status` reporting PENDING forever, with no
    `quarantine_reason` — a client polling for a terminal state timed out.

    Root cause: `SalesRegisterLoader.load` raised `ValidationRejected` on a
    fatal parse failure with no durable record written anywhere.
    `record_and_dispatch_trigger`'s `TriggerOutcome.status = "QUARANTINED"`
    was always correct — that response body was never wrong — but
    `SilverReader.artefact_outcome` (`GET /status`) looks up
    `v1_quarantined_artefact` first and found nothing, then
    `v1_ingest_batch` (also nothing, since no batch is ever created on this
    path) and fell back to PENDING. `RegisterLoader.load` (the flat
    REGISTER_LOADER mechanism) has carried the fix — a `_quarantine()` write
    before the raise — since it was written; `sales_register.py`, the older
    hand-written loader, never had it.

    Asserted end to end through HTTP, both calls, because the bug was
    specifically the DISAGREEMENT between what trigger reported and what
    status reported — a unit test on either loader in isolation would not
    catch that the two responses used to contradict each other.
    """
    entity_id = uuid.uuid4()
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "SALES_REGISTER"},
        files={"file": ("sr.csv", b"not,the,right,columns\n1,2,3\n", "text/csv")},
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
    assert trigger.json()["status"] == "QUARANTINED"

    status = api_client.get(f"/artefacts/{ingest_id}/status", headers=AUTH)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "QUARANTINED"
    assert body["quarantine_reason"] is not None and "missing required" in body["quarantine_reason"]


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


def test_trigger_is_422_not_500_for_bytes_that_are_not_a_pdf(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """Defense in depth for the ERP upload E2E finding (2026-09-01): even
    with the catalogue corrected (migration `043`), a `PDF_EXTRACTION` type
    can still receive bytes that are not a valid PDF at all — a genuinely
    corrupt upload, or a client that has not yet re-downloaded the corrected
    schema. Before `PypdfReader.extract` learned to catch
    `pypdf.errors.PyPdfError`, this reached the caller as an unhandled
    `pypdf.errors.PdfStreamError` — an opaque 500 — exactly the shape the E2E
    suite's `BILL_OF_ENTRY` reproduction hit.
    """
    entity_id = uuid.uuid4()
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "LUT"},
        files={"file": ("lut.pdf", b"not a pdf at all", "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    ingest_id = upload.json()["ingest_id"]

    trigger = api_client.post(
        f"/artefacts/{ingest_id}/trigger", headers=AUTH, json={"doc_type_code": "LUT"}
    )
    assert trigger.status_code == 422, trigger.text
    assert "could not read PDF" in trigger.json()["detail"]


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


def test_upload_via_the_portal_route_is_provenanced_portal(
    api_client: TestClient, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """`received_from` distinguishes how an artefact arrived. Every other
    `BronzeIngestionService.receive` caller defaults to `"upload"`; this HTTP
    route is the one going through the portal, and now says so — worth
    getting right before a second caller (a source connector pulling from
    GSTN/ICEGATE, `src/connectors/`) starts writing through the same service
    with its own value and this column becomes load-bearing."""
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "BILL_OF_ENTRY"},
        files={"file": ("export.csv", b"doc_number\nPROV-1\n", "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    ingest_id = upload.json()["ingest_id"]

    row = admin.execute(
        sql.SQL("SELECT received_from FROM {}.artefact_ledger WHERE ingest_id = %s").format(
            sql.Identifier(tenant_a.ctx.bronze_schema)
        ),
        (ingest_id,),
    ).fetchone()
    assert row is not None and row[0] == "PORTAL"


def test_upload_with_an_unrecognised_document_type_is_422(
    api_client: TestClient, tenant_a: SeededTenant,
) -> None:
    """The registry gate (`BronzeIngestionService._check_document_type_in_scope`)
    reached through the real route, not just the service directly — the file
    must never reach the object store or the ledger for a type the registry
    does not recognise as in-scope."""
    resp = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={
            "entity_id": str(tenant_a.entity_id),
            "document_type": "THIS_TYPE_DOES_NOT_EXIST",
        },
        files={"file": ("export.csv", b"doc_number\nX\n", "text/csv")},
    )
    assert resp.status_code == 422, resp.text
    assert "not a recognised document type" in resp.json()["detail"]


def test_upload_with_no_document_type_is_422(
    api_client: TestClient, tenant_a: SeededTenant,
) -> None:
    """No default remains — a caller must declare what they are sending.
    FastAPI's own missing-required-field 422, not this codebase's."""
    resp = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id)},
        files={"file": ("export.csv", b"doc_number\nX\n", "text/csv")},
    )
    assert resp.status_code == 422, resp.text


def test_batch_upload_all_files_succeed(
    api_client: TestClient, tenant_a: SeededTenant,
) -> None:
    resp = api_client.post(
        "/artefacts/batch",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "BILL_OF_ENTRY"},
        files=[
            ("files", ("a.csv", b"doc_number\nBATCH-A\n", "text/csv")),
            ("files", ("b.csv", b"doc_number\nBATCH-B\n", "text/csv")),
            ("files", ("c.csv", b"doc_number\nBATCH-C\n", "text/csv")),
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted_count"] == 3
    assert body["rejected_count"] == 0
    assert len(body["items"]) == 3
    assert all(item["ok"] for item in body["items"])
    assert len({item["ingest_id"] for item in body["items"]}) == 3, (
        "each file must get its own ingest_id"
    )


def test_batch_upload_one_bad_file_does_not_fail_the_others(
    api_client: TestClient, tenant_a: SeededTenant,
) -> None:
    """A wrong extension refuses only its own file — the two good ones next
    to it must still succeed. Proves the batch is file-independent, not a
    single all-or-nothing unit."""
    resp = api_client.post(
        "/artefacts/batch",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "BILL_OF_ENTRY"},
        files=[
            ("files", ("good1.csv", b"doc_number\nBATCH-GOOD-1\n", "text/csv")),
            ("files", ("bad.exe", b"whatever", "application/octet-stream")),
            ("files", ("good2.csv", b"doc_number\nBATCH-GOOD-2\n", "text/csv")),
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted_count"] == 2
    assert body["rejected_count"] == 1

    by_name = {item["filename"]: item for item in body["items"]}
    assert by_name["good1.csv"]["ok"] is True
    assert by_name["good2.csv"]["ok"] is True
    assert by_name["bad.exe"]["ok"] is False
    assert "extension" in by_name["bad.exe"]["error"]


def test_batch_upload_with_no_files_is_422(
    api_client: TestClient, tenant_a: SeededTenant,
) -> None:
    resp = api_client.post(
        "/artefacts/batch",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "BILL_OF_ENTRY"},
        files=[],
    )
    assert resp.status_code == 422, resp.text


def test_batch_upload_is_provenanced_portal_per_file(
    api_client: TestClient, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    resp = api_client.post(
        "/artefacts/batch",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "BILL_OF_ENTRY"},
        files=[("files", ("a.csv", b"doc_number\nBATCH-PROV\n", "text/csv"))],
    )
    ingest_id = resp.json()["items"][0]["ingest_id"]
    row = admin.execute(
        sql.SQL("SELECT received_from FROM {}.artefact_ledger WHERE ingest_id = %s").format(
            sql.Identifier(tenant_a.ctx.bronze_schema)
        ),
        (ingest_id,),
    ).fetchone()
    assert row is not None and row[0] == "PORTAL"


def test_trigger_is_422_with_constraint_context_when_the_database_refuses_a_value(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """The status code half of the ERP upload E2E finding (2026-09-01).

    `supplier_gstin` is published as plain `text` in the schema catalogue but
    is `platform_ref.gstin` in the table, so a CSV valid against the
    downloaded schema can still be refused by the domain's CHECK. Before
    this, the route caught only ForeignKeyViolation and ValueError and every
    one of these escaped as an opaque 500 — a client had no way to tell a bad
    value from a broken server.

    The body carries the constraint name because that is the only thing that
    makes the 422 actionable: "gstin_check" tells a client which of the
    fourteen columns to look at, and the bare message does not.
    """
    entity_id = uuid.uuid4()
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "PURCHASE_REGISTER"},
        files={
            "file": (
                "purchase_register.csv",
                b"supplier_gstin,invoice_no,invoice_date,hsn_sac,taxable_value,"
                b"gst_rate,cgst,sgst,igst,cess,gl_code,cost_centre,"
                b"itc_eligibility,rcm_flag\n"
                b"NOTAGSTIN12345,PI-ROUTE-1,2026-04-15,9983,100.00,18.00,"
                b"9.00,9.00,0.00,0.00,GL-1,CC-1,ELIGIBLE,false\n",
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
            "doc_type_code": "PURCHASE_REGISTER",
            "period_start": PERIOD_START, "period_end": PERIOD_END, "gstin": GSTIN,
        },
    )
    assert trigger.status_code == 422, trigger.text
    detail = trigger.json()["detail"]
    assert detail["kind"] == "INVALID"
    assert detail["constraint"] == "gstin_check"


def test_resubmitting_a_document_supersedes_and_returns_202(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """Through HTTP: a re-triggered artefact is a correction, not an error.

    This test asserted a 409 when it was written, because the extraction path
    inserted blind and the second trigger could only fail. Superseding
    (`src/silver/supersede.py`) is the actual fix for the E2E finding — the
    409 mapping below is what remains for the case the index still catches.
    """
    entity_id = uuid.uuid4()
    data = (
        (SAMPLES / "A4.01_Cost_Sharing_Agreement.pdf").read_bytes()
        + b"\n%% run " + uuid.uuid4().hex.encode() + b"\n"
    )
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "COST_SHARING_AGREEMENT"},
        files={"file": ("csa.pdf", data, "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["deduplicated"] is False
    ingest_id = upload.json()["ingest_id"]

    body = {"doc_type_code": "COST_SHARING_AGREEMENT"}
    first = api_client.post(f"/artefacts/{ingest_id}/trigger", headers=AUTH, json=body)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "ACCEPTED"

    second = api_client.post(f"/artefacts/{ingest_id}/trigger", headers=AUTH, json=body)
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "ACCEPTED"
    assert second.json()["batch_id"] != first.json()["batch_id"], (
        "a re-extraction is its own batch — reusing the first batch_id would "
        "make the two readings indistinguishable in the manifest"
    )


def test_resubmitting_an_archetype1_pdf_supersedes_and_returns_202(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """`GTA_INVOICE_CONSIGNMENT_NOTE`, the finding as reported (2026-09-01):
    re-triggering its reference PDF a second time returned `409` on
    `transaction_document_natural_key_uq`.

    Different root cause from the test above it despite the identical shape
    — that one is a PDF archetype's own service
    (`src/silver/entitlement.py` and its four siblings); this is
    `src/silver/promote.py::promote_transaction_documents`, shared by
    ARCHETYPE1_PROMOTE (CSV) and PDF_EXTRACTION's archetype-1 extractors
    alike, and it never had ANY natural-key lookup — not "content-keyed
    instead of natural-keyed" the way the other archetypes started out, just
    absent. Tenant migration 030 gave `transaction_document` the
    `supersedes_doc_id` pointer the other five tables already had; this test
    is the HTTP-level proof the fix works for a real PDF specimen, not just
    the archetype-level proof in `test_transaction.py`.
    """
    entity_id = uuid.uuid4()
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(entity_id), "document_type": "GTA_INVOICE_CONSIGNMENT_NOTE"},
        files={
            "file": (
                "gta.pdf",
                (SAMPLES / "A4.08_GTA_Invoice_Consignment_Note.pdf").read_bytes(),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    ingest_id = upload.json()["ingest_id"]

    body = {"doc_type_code": "GTA_INVOICE_CONSIGNMENT_NOTE"}
    first = api_client.post(f"/artefacts/{ingest_id}/trigger", headers=AUTH, json=body)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "ACCEPTED"
    assert first.json()["mechanism"] == "PDF_EXTRACTION"

    second = api_client.post(f"/artefacts/{ingest_id}/trigger", headers=AUTH, json=body)
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "ACCEPTED"
    assert second.json()["batch_id"] != first.json()["batch_id"]


def test_a_conflict_is_409_with_constraint_context(
    api_client: TestClient, tenant_a: SeededTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """409, not 422, and not the opaque 500 this used to be.

    Supersede removed the routine cause of a unique violation but not the
    possibility (two concurrent dispatches can still race past
    `close_current`'s `FOR UPDATE`). A client's correct response to a
    CONFLICT differs from INVALID — do not resend these bytes, versus fix the
    value and resend — so the two must not collapse into one status. The
    violation is faked because the race is not deterministically provokable;
    the status mapping is what is under test.
    """
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "LUT"},
        files={"file": ("lut.pdf", (SAMPLES / "A5.01_LUT_Letter_of_Undertaking.pdf").read_bytes(),
                        "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    ingest_id = upload.json()["ingest_id"]

    async def _raise_unique(*args: object, **kwargs: object) -> object:
        raise psycopg.errors.UniqueViolation(
            'duplicate key value violates unique constraint '
            '"entitlement_instrument_current_uq"'
        )

    monkeypatch.setattr("src.dispatch.trigger.dispatch_load", _raise_unique)

    resp = api_client.post(
        f"/artefacts/{ingest_id}/trigger", headers=AUTH, json={"doc_type_code": "LUT"}
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["kind"] == "CONFLICT"
    # `constraint` is None here and that is correct, not a gap: an exception
    # constructed in Python carries no server `Diagnostic`, so there is
    # nothing to lift. That the diagnostic fields ARE carried through when
    # Postgres actually raises is gated by
    # `test_trigger_is_422_with_constraint_context_when_the_database_refuses_a_value`,
    # which provokes a real domain-check violation.
    assert detail["constraint"] is None
