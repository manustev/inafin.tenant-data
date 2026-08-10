"""GraphQL reads — src/api/graphql/schema.py.

Resolvers wrap `SilverReader`/`EntitlementReader` unchanged; these gates prove
the wiring (context_getter -> Role.RECON reads -> response shape), not new
read logic.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conformance.conftest import TOKEN_ACME
from tests.conftest import SeededTenant

pytestmark = pytest.mark.conformance

AUTH = {"Authorization": f"Bearer {TOKEN_ACME}"}

ARTEFACT_OUTCOME_QUERY = """
query($id: UUID!) {
  artefactOutcome(bronzeIngestId: $id) {
    status
    documentType
    rejectedCount
  }
}
"""


def test_artefact_outcome_round_trips(api_client: TestClient, tenant_a: SeededTenant) -> None:
    resp = api_client.post(
        "/graphql",
        headers=AUTH,
        json={
            "query": ARTEFACT_OUTCOME_QUERY,
            "variables": {"id": str(tenant_a.ingest_id)},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "errors" not in body, body
    assert body["data"]["artefactOutcome"]["status"] == "ACCEPTED"
    assert body["data"]["artefactOutcome"]["rejectedCount"] == 0


def test_graphql_without_a_token_is_401(api_client: TestClient) -> None:
    resp = api_client.post(
        "/graphql",
        json={
            "query": ARTEFACT_OUTCOME_QUERY,
            "variables": {"id": "00000000-0000-0000-0000-000000000000"},
        },
    )
    assert resp.status_code == 401
