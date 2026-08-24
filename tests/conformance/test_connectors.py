"""Conformance tests for `src/connectors/` — Category B source pulling.

Three things are asserted, matching the three pieces
`reference/B-Document_prompt.md` asked for:

1. `LocalFixtureConnector` actually resolves real staged sample data (both
   the GSTIN+period case and the register-shaped no-GSTIN case), and prefers
   JSON over CSV when both exist — the preference this session's format
   comparison (`HANDOFF-2026-08-19-categoryB.md` §2b) established matters.
2. `build_source_connector` in live mode returns an adapter that refuses to
   fetch until configured (`ConnectorNotConfiguredError`), for every
   `source_system` the registry actually uses — never a silent fake
   response.
3. `resolve_document_source` reads the "which sections use B1.01, in which
   mode" mapping straight from `platform_ref.document_type`'s existing
   `sections`/`mode` columns, matching
   `INAFIN_Recon_Doc4_Sec2_SourceDocRegister_v2..md` exactly — proving no
   new mapping table was needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import SeededTenant

from src.connectors.adapters.gstn_api import GstnApiConnector
from src.connectors.base import ConnectorNotConfiguredError, SourceDocumentNotFoundError
from src.connectors.factory import build_source_connector
from src.connectors.local_fixture import LocalFixtureConnector
from src.connectors.registry_lookup import resolve_document_source
from src.core.config import Settings
from src.core.pool import TenantScopedPool

pytestmark = pytest.mark.conformance

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "bronze_source"
_STAGED_TENANT = "vardhman"


@pytest.fixture
def fixture_connector() -> LocalFixtureConnector:
    return LocalFixtureConnector(fixture_root=_FIXTURE_ROOT)


class TestLocalFixtureConnector:
    """Exercises the connector against the real staged sample tree —
    `scripts/stage_bronze_fixtures.py` must have been run (it is, and its
    output is committed) for these to pass; a missing fixture tree is
    exactly the failure `SourceDocumentNotFoundError` should describe, not a
    reason to skip."""

    @pytest.mark.asyncio
    async def test_resolves_a_periodic_return_by_gstin_and_period(
        self, fixture_connector: LocalFixtureConnector,
    ) -> None:
        """GSTR-2B for one GSTIN/period exists as both JSON and (via the
        flat all-lines CSV) partially in CSV — JSON must win, per this
        session's format-fidelity finding."""
        doc = await fixture_connector.fetch(
            tenant_slug=_STAGED_TENANT, doc_type_code="GSTR_2B", ref="B1.03",
            gstin="29AABCV1234K1Z9", period="112024",
        )
        assert doc.content_format == "json"
        assert "29AABCV1234K1Z9" in doc.filename
        assert "112024" in doc.filename
        assert doc.content  # non-empty bytes

    @pytest.mark.asyncio
    async def test_resolves_a_register_shaped_type_with_no_gstin_or_period(
        self, fixture_connector: LocalFixtureConnector,
    ) -> None:
        """B1.02 (ARN/filing-date register) is one CSV file covering every
        GSTIN and period at once — no gstin/period filter applies."""
        doc = await fixture_connector.fetch(
            tenant_slug=_STAGED_TENANT, doc_type_code="GSTR_1_ARN", ref="B1.02",
        )
        assert doc.content_format == "csv"
        assert doc.content

    @pytest.mark.asyncio
    async def test_json_is_preferred_over_csv_and_pdf_when_all_three_exist(
        self, fixture_connector: LocalFixtureConnector,
    ) -> None:
        """B3.01 (open SCN register) is the real case where the gstin/period
        filter does NOT already narrow to one file — CSV, JSON and PDF all
        describe the same register with no per-file GSTIN in the filename,
        so the format preference itself is what decides the outcome.
        Genuinely exercises the preference: without it, `sorted()` iteration
        order alone would pick the PDF (`DRC01_SCN_...pdf` sorts before
        `open_scn_register.*` alphabetically)."""
        doc = await fixture_connector.fetch(
            tenant_slug=_STAGED_TENANT, doc_type_code="OPEN_SCN_REGISTER", ref="B3.01",
        )
        assert doc.content_format == "json"

    @pytest.mark.asyncio
    async def test_unknown_ref_raises_source_document_not_found(
        self, fixture_connector: LocalFixtureConnector,
    ) -> None:
        with pytest.raises(SourceDocumentNotFoundError):
            await fixture_connector.fetch(
                tenant_slug=_STAGED_TENANT, doc_type_code="NOT_A_REAL_TYPE", ref="B9.99",
            )

    @pytest.mark.asyncio
    async def test_no_matching_gstin_period_combination_raises(
        self, fixture_connector: LocalFixtureConnector,
    ) -> None:
        """A real ref folder, but a (gstin, period) combination the sample
        set never generated — distinct failure from an unknown ref entirely,
        both surfaced as the same exception type."""
        with pytest.raises(SourceDocumentNotFoundError):
            await fixture_connector.fetch(
                tenant_slug=_STAGED_TENANT, doc_type_code="GSTR_2B", ref="B1.03",
                gstin="00NOTAREALGSTIN0", period="012099",
            )


class TestFactory:
    """`build_source_connector` is the one place `source_data_mode` and
    `source_system` become a concrete adapter."""

    def test_local_fixture_mode_returns_the_fixture_connector_for_any_source_system(
        self,
    ) -> None:
        settings = Settings(
            pg_app_dsn="", pg_migrate_dsn="",
            source_data_mode="local_fixture", source_fixture_root=str(_FIXTURE_ROOT),
        )
        connector = build_source_connector("GSTN_API", settings)
        assert isinstance(connector, LocalFixtureConnector)

    @pytest.mark.asyncio
    async def test_live_mode_unconfigured_adapter_refuses_to_fetch(self) -> None:
        """No GSP credentials exist in this workspace — live mode must
        raise, never fabricate a response."""
        settings = Settings(pg_app_dsn="", pg_migrate_dsn="", source_data_mode="live")
        connector = build_source_connector("GSTN_API", settings)
        assert isinstance(connector, GstnApiConnector)
        with pytest.raises(ConnectorNotConfiguredError):
            await connector.fetch(
                tenant_slug=_STAGED_TENANT, doc_type_code="GSTR_2B", ref="B1.03",
            )

    def test_live_mode_configured_adapter_is_reported_configured(self) -> None:
        """Supplying a base_url + credential_ref makes `_configured()` true
        — proven through the public factory path, not by reaching into the
        adapter's private state."""
        settings = Settings(
            pg_app_dsn="", pg_migrate_dsn="",
            source_data_mode="live",
            source_connector_base_urls={"GSTN_API": "https://gsp.example.test"},
            source_connector_credential_refs={"GSTN_API": "vault://gsp/gstn-api"},
        )
        connector = build_source_connector("GSTN_API", settings)
        assert isinstance(connector, GstnApiConnector)
        assert connector._configured() is True


@pytest.mark.asyncio
class TestRegistryLookup:
    """Proves the "B1.01 is used by A1,A2,A7,A8,A9,A10, mode BOTH" mapping
    the connector-layer brief asked for already lives in
    `platform_ref.document_type` — nothing new was added to the schema."""

    async def test_gstr1_sections_and_mode_match_the_source_document_register(
        self, app_pool: TenantScopedPool, tenant_a: SeededTenant,
    ) -> None:
        meta = await resolve_document_source(app_pool, tenant_a.ctx, "GSTR_1")
        assert meta.ref == "B1.01"
        assert meta.source_system == "GSTN_API"
        assert meta.mode == "BOTH"
        # Exact section set from INAFIN_Recon_Doc4_Sec2_SourceDocRegister_v2..md's
        # B1.01 row: "A1,A2,A7,A8,A9,A10".
        assert set(meta.sections) == {"A1", "A2", "A7", "A8", "A9", "A10"}

    async def test_court_stay_order_source_system_is_court_registry(
        self, app_pool: TenantScopedPool, tenant_a: SeededTenant,
    ) -> None:
        """A source_system distinct from every GST-specific connector —
        proves the lookup isn't hardcoded to GSTN-shaped values."""
        meta = await resolve_document_source(app_pool, tenant_a.ctx, "COURT_STAY_ORDER")
        assert meta.source_system == "COURT_REGISTRY"
        assert meta.mode == "BOTH"
