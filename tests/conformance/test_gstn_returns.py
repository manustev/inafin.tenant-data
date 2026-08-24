"""B1.03 GSTR_2B — GSTN_JSON_PROMOTE, the first archetype-5 typed table.

D1/D3 (`HANDOFF-2026-08-19-categoryB.md`, decided 2026-08-19): typed columns
except named narrative fields; a new dispatch mechanism, bespoke Python per
return type. These gates exercise the real staged sample data
(`fixtures/bronze_source/vardhman/B1.03/`), not synthetic fixtures — the
same discipline `test_sales_register.py` established for the reference
pattern this migration follows.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import uuid

import pytest
from tests.conftest import SeededTenant

from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.core.tenant import Role
from src.dispatch.router import dispatch_load
from src.silver.gstn_returns.gstr2b import Gstr2bLoader, parse_gstr_2b

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "fixtures" / "bronze_source" / "vardhman" / "B1.03"
    / "GSTR2B_29AABCV1234K1Z9_112024.json"
)
GSTIN = "29AABCV1234K1Z9"
PERIOD_START = dt.date(2024, 11, 1)
PERIOD_END = dt.date(2024, 11, 30)


class TestParseGstr2b:
    def test_parses_the_real_sample_shape(self) -> None:
        parsed = parse_gstr_2b(FIXTURE.read_bytes(), expected_period=PERIOD_START)
        assert parsed.gstin == GSTIN
        assert parsed.return_period == "112024"
        assert parsed.generated_at is not None
        # Real specimen: 8 b2b suppliers x their invoices' items, 0 cdnr,
        # 0 impg, 1 isd credit — confirmed by direct inspection this session.
        sections = {ln.section for ln in parsed.lines}
        assert sections == {"B2B", "ISD"}
        assert any(ln.section == "ISD" and ln.taxable_value is None for ln in parsed.lines)
        assert parsed.itc_summary  # itcsumm block is present and non-empty

    def test_period_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValidationRejected, match="rtnprd"):
            parse_gstr_2b(FIXTURE.read_bytes(), expected_period=dt.date(2024, 12, 1))

    def test_nonempty_cdnr_is_rejected(self) -> None:
        """No real specimen in this workspace has a populated `cdnr` — this
        test constructs the one synthetically, not to trust its shape as a
        fixture, but to prove the parser refuses rather than silently
        mis-parsing it (see gstr2b.py's `_reject_unparsed_cdnr`)."""
        payload = json.loads(FIXTURE.read_bytes())
        payload["data"]["docdata"]["cdnr"] = [{"ctin": "somefield"}]
        with pytest.raises(ValidationRejected, match="cdnr"):
            parse_gstr_2b(json.dumps(payload).encode(), expected_period=PERIOD_START)

    def test_malformed_json_is_rejected(self) -> None:
        with pytest.raises(ValidationRejected):
            parse_gstr_2b(b"not json", expected_period=PERIOD_START)


@pytest.mark.asyncio
class TestGstr2bLoader:
    async def test_loads_and_is_idempotent_on_resubmission(
        self, app_pool: TenantScopedPool, tenant_a: SeededTenant,
    ) -> None:
        entity_id = uuid.uuid4()
        loader = Gstr2bLoader(app_pool)
        data = FIXTURE.read_bytes()

        first = await loader.load(
            tenant_a.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=uuid.uuid4(),
            data=data, period_start=PERIOD_START, period_end=PERIOD_END,
        )
        assert first.inserted is True
        assert first.batch_id is not None
        assert first.line_count > 0
        assert first.itc_summary_count > 0

        async with app_pool.transaction(tenant_a.ctx, Role.SUPPORT) as conn:
            header = await (
                await conn.execute(
                    f"SELECT id, superseded_at FROM {tenant_a.ctx.silver_schema}.gstr_2b"
                    " WHERE entity_id = %s",
                    (entity_id,),
                )
            ).fetchone()
            assert header is not None
            header_id, superseded_at = header
            assert superseded_at is None

            line_count = (
                await (
                    await conn.execute(
                        f"SELECT count(*) FROM {tenant_a.ctx.silver_schema}.gstr_2b_line"
                        " WHERE header_id = %s",
                        (header_id,),
                    )
                ).fetchone()
            )[0]
            assert line_count == first.line_count

        # Resubmitting byte-identical content must be a no-op: no new batch,
        # nothing to publish — TYPED-TABLES-PLAN.md 5's idempotency promise,
        # at whole-statement grain.
        second = await loader.load(
            tenant_a.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=uuid.uuid4(),
            data=data, period_start=PERIOD_START, period_end=PERIOD_END,
        )
        assert second.inserted is False
        assert second.batch_id is None

    async def test_a_changed_statement_supersedes_the_prior_one(
        self, app_pool: TenantScopedPool, tenant_a: SeededTenant,
    ) -> None:
        entity_id = uuid.uuid4()
        loader = Gstr2bLoader(app_pool)
        payload = json.loads(FIXTURE.read_bytes())

        first = await loader.load(
            tenant_a.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=uuid.uuid4(),
            data=json.dumps(payload).encode(),
            period_start=PERIOD_START, period_end=PERIOD_END,
        )
        assert first.inserted is True

        # A real change: drop the ISD line, mirroring a supplier amendment
        # regenerating the statement.
        payload["data"]["docdata"]["isd"] = []
        second = await loader.load(
            tenant_a.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=uuid.uuid4(),
            data=json.dumps(payload).encode(),
            period_start=PERIOD_START, period_end=PERIOD_END,
        )
        assert second.inserted is True
        assert second.batch_id != first.batch_id

        async with app_pool.transaction(tenant_a.ctx, Role.SUPPORT) as conn:
            rows = await (
                await conn.execute(
                    f"SELECT id, superseded_at FROM {tenant_a.ctx.silver_schema}.gstr_2b"
                    " WHERE entity_id = %s ORDER BY id",
                    (entity_id,),
                )
            ).fetchall()
            assert len(rows) == 2
            assert rows[0][1] is not None  # first row closed
            assert rows[1][1] is None      # second row live


@pytest.mark.asyncio
async def test_dispatch_load_routes_gstr_2b_through_gstn_json_promote(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
) -> None:
    """End to end through the same `dispatch_load` entry point
    `POST /artefacts/{id}/trigger` uses — proves the registry's
    `dispatch_mechanism = GSTN_JSON_PROMOTE` cell (shared migration 033)
    actually routes here, not just that the loader works when called
    directly."""
    entity_id = uuid.uuid4()
    outcome = await dispatch_load(
        tenant_a.ctx, app_pool,
        ingest_id=uuid.uuid4(), entity_id=entity_id, doc_type_code="GSTR_2B",
        data=FIXTURE.read_bytes(), content_format="json",
        period_start=PERIOD_START, period_end=PERIOD_END, gstin=GSTIN,
    )
    assert outcome.mechanism == "GSTN_JSON_PROMOTE"
    assert outcome.status == "ACCEPTED"
    assert outcome.batch_id is not None
