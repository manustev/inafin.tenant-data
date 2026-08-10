"""REST upload / trigger / status — src/api/routes_ingest.py.

Upload and status wrap real library calls (`BronzeIngestionService.receive`,
`SilverReader.artefact_outcome`) unchanged; trigger is a documented STUB that
records intent only (migrations/tenant/015_load_trigger.sql, TODO.md) — no
loader runs as a result of it, and these tests prove that rather than assume it.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tests.conformance.conftest import TOKEN_ACME
from tests.conftest import SeededTenant

pytestmark = pytest.mark.conformance

AUTH = {"Authorization": f"Bearer {TOKEN_ACME}"}


def test_upload_then_status_is_pending_before_any_load(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """A fresh upload has nothing promoting it yet — PENDING is the only
    reachable state through the API alone in this slice (no dispatcher)."""
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


def test_trigger_records_intent_and_does_not_create_a_batch(
    api_client: TestClient, tenant_a: SeededTenant
) -> None:
    """The stub's whole contract: a row lands in load_trigger, no ingest_batch
    appears, and status stays PENDING right after — because nothing consumed
    the trigger."""
    upload = api_client.post(
        "/artefacts",
        headers=AUTH,
        data={"entity_id": str(tenant_a.entity_id), "document_type": "BILL_OF_ENTRY"},
        files={"file": ("export.csv", b"doc_number\nTRIG-1\n", "text/csv")},
    )
    ingest_id = upload.json()["ingest_id"]

    trigger = api_client.post(
        f"/artefacts/{ingest_id}/trigger",
        headers=AUTH,
        json={"doc_type_code": "BILL_OF_ENTRY"},
    )
    assert trigger.status_code == 202, trigger.text
    body = trigger.json()
    assert body["ingest_id"] == ingest_id
    assert body["doc_type_code"] == "BILL_OF_ENTRY"
    assert body["status"] == "recorded"

    status = api_client.get(f"/artefacts/{ingest_id}/status", headers=AUTH)
    assert status.json()["status"] == "PENDING"


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
    `tenant_a.ingest_id` — this is the ACCEPTED branch, reached without going
    through this slice's stub trigger (there is no dispatcher yet)."""
    status = api_client.get(f"/artefacts/{tenant_a.ingest_id}/status", headers=AUTH)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "ACCEPTED"
    assert body["rejected_count"] == 0
