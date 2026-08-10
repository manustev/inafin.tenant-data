"""Auth boundary — src/api/auth.py's StaticTokenAuth.

The tenant slug must come ONLY from the resolved token, never from anything
the caller supplies directly (ARCHITECTURE.md 5.6). These gates prove the
placeholder adapter at least holds that line, even though it is not a real
security boundary (no signature, no expiry — see the module docstring).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conformance.conftest import TOKEN_ACME, TOKEN_GLOBEX
from tests.conftest import SeededTenant

pytestmark = pytest.mark.conformance


def test_missing_token_is_401(api_client: TestClient) -> None:
    resp = api_client.get("/artefacts/00000000-0000-0000-0000-000000000000/status")
    assert resp.status_code == 401


def test_malformed_authorization_header_is_401(api_client: TestClient) -> None:
    resp = api_client.get(
        "/artefacts/00000000-0000-0000-0000-000000000000/status",
        headers={"Authorization": "Basic not-a-bearer-token"},
    )
    assert resp.status_code == 401


def test_unknown_token_is_401(api_client: TestClient) -> None:
    resp = api_client.get(
        "/artefacts/00000000-0000-0000-0000-000000000000/status",
        headers={"Authorization": "Bearer this-token-was-never-issued"},
    )
    assert resp.status_code == 401


def test_tenant_a_token_cannot_read_tenant_b_artefact(
    api_client: TestClient, tenant_b: SeededTenant
) -> None:
    """`tenant_b.ingest_id` is ACCEPTED in tenant B's own schema (seeded by
    `tests/conftest.py`). Asked about with tenant A's token, the same UUID
    must resolve to nothing in tenant A's schema — PENDING, not tenant B's
    real ACCEPTED status. `SilverReader.artefact_outcome` is already scoped
    by `Role.RECON`'s GRANT; this is the isolation boundary observed from the
    API layer's own auth resolution, not a new one.
    """
    resp = api_client.get(
        f"/artefacts/{tenant_b.ingest_id}/status",
        headers={"Authorization": f"Bearer {TOKEN_ACME}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING"

    as_globex = api_client.get(
        f"/artefacts/{tenant_b.ingest_id}/status",
        headers={"Authorization": f"Bearer {TOKEN_GLOBEX}"},
    )
    assert as_globex.json()["status"] == "ACCEPTED"
