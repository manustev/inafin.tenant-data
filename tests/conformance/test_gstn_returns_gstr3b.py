"""B1.05 GSTR_3B — second archetype-5 typed table, same gates
`test_gstn_returns.py` established for GSTR_2B, against the real staged
sample data.
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
from src.silver.gstn_returns.gstr3b import Gstr3bLoader, parse_gstr_3b

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "fixtures" / "bronze_source" / "vardhman" / "B1.05"
    / "GSTR3B_29AABCV1234K1Z9_112024.json"
)
GSTIN = "29AABCV1234K1Z9"
PERIOD_START = dt.date(2024, 11, 1)
PERIOD_END = dt.date(2024, 11, 30)


class TestParseGstr3b:
    def test_parses_the_real_sample_shape(self) -> None:
        parsed = parse_gstr_3b(FIXTURE.read_bytes(), expected_period=PERIOD_START)
        assert parsed.gstin == GSTIN
        assert parsed.return_period == "112024"
        assert parsed.arn == "AA2912248074924B"
        assert parsed.filing_date == dt.date(2024, 12, 20)
        assert parsed.filing_status == "Filed"
        assert parsed.osup_det[0] > 0  # taxable value
        # 5 itc_avl + 2 itc_rev + 2 itc_inelg = 9 rows, confirmed against the
        # real payload's own fixed ty vocabulary.
        assert len(parsed.itc_detail) == 9
        assert {d.box for d in parsed.itc_detail} == {"AVAILED", "REVERSED", "INELIGIBLE"}
        assert len(parsed.inward_supply) == 2
        assert {s.supply_type for s in parsed.inward_supply} == {"GST", "NONGST"}

    def test_period_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValidationRejected, match="ret_period"):
            parse_gstr_3b(FIXTURE.read_bytes(), expected_period=dt.date(2024, 12, 1))

    def test_nonempty_inter_sup_is_rejected(self) -> None:
        """No real specimen has a populated inter_sup list — constructed
        synthetically to prove the parser refuses rather than guessing (see
        gstr3b.py's `_reject_unparsed_inter_sup`)."""
        payload = json.loads(FIXTURE.read_bytes())
        payload["inter_sup"]["unreg_details"] = [{"pos": "27"}]
        with pytest.raises(ValidationRejected, match="inter_sup"):
            parse_gstr_3b(json.dumps(payload).encode(), expected_period=PERIOD_START)

    def test_unrecognised_itc_type_is_rejected(self) -> None:
        """A `ty` outside the confirmed vocabulary must not be silently
        stored under an unverified value."""
        payload = json.loads(FIXTURE.read_bytes())
        payload["itc_elg"]["itc_avl"].append({"ty": "SOMETHING_NEW", "iamt": 1})
        with pytest.raises(ValidationRejected, match="unrecognised ty"):
            parse_gstr_3b(json.dumps(payload).encode(), expected_period=PERIOD_START)

    def test_unrecognised_inward_supply_type_is_rejected(self) -> None:
        """Table 5's `ty` has its own closed vocabulary (GST/NONGST), and its
        own gate — the ITC gate above does not cover this list. The DB's FK to
        `Gstr3b_Inward_Supply_Type` would refuse the write anyway; rejecting at
        parse time is what makes the failure legible instead of a constraint
        violation three layers down."""
        payload = json.loads(FIXTURE.read_bytes())
        payload["inward_sup"]["isup_details"].append(
            {"ty": "SOMETHING_NEW", "inter": 1, "intra": 2}
        )
        with pytest.raises(ValidationRejected, match="unrecognised ty"):
            parse_gstr_3b(json.dumps(payload).encode(), expected_period=PERIOD_START)

    def test_malformed_json_is_rejected(self) -> None:
        with pytest.raises(ValidationRejected):
            parse_gstr_3b(b"not json", expected_period=PERIOD_START)


@pytest.mark.asyncio
class TestGstr3bLoader:
    async def test_loads_and_is_idempotent_on_resubmission(
        self, app_pool: TenantScopedPool, tenant_a: SeededTenant,
    ) -> None:
        entity_id = uuid.uuid4()
        loader = Gstr3bLoader(app_pool)
        data = FIXTURE.read_bytes()

        first = await loader.load(
            tenant_a.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=uuid.uuid4(),
            data=data, period_start=PERIOD_START, period_end=PERIOD_END,
        )
        assert first.inserted is True
        assert first.batch_id is not None
        assert first.itc_detail_count == 9
        assert first.inward_supply_count == 2

        async with app_pool.transaction(tenant_a.ctx, Role.SUPPORT) as conn:
            header = await (
                await conn.execute(
                    f"SELECT id, superseded_at, arn FROM {tenant_a.ctx.silver_schema}.gstr_3b"
                    " WHERE entity_id = %s",
                    (entity_id,),
                )
            ).fetchone()
            assert header is not None
            header_id, superseded_at, arn = header
            assert superseded_at is None
            assert arn == "AA2912248074924B"

            detail_count = (
                await (
                    await conn.execute(
                        f"SELECT count(*) FROM {tenant_a.ctx.silver_schema}.gstr_3b_itc_detail"
                        " WHERE header_id = %s",
                        (header_id,),
                    )
                ).fetchone()
            )[0]
            assert detail_count == 9

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
        loader = Gstr3bLoader(app_pool)
        payload = json.loads(FIXTURE.read_bytes())

        first = await loader.load(
            tenant_a.ctx, entity_id=entity_id, gstin=GSTIN, ingest_id=uuid.uuid4(),
            data=json.dumps(payload).encode(),
            period_start=PERIOD_START, period_end=PERIOD_END,
        )
        assert first.inserted is True

        # A real change: interest amount corrected.
        payload["intr_ltfee"]["intr_details"]["iamt"] = 999.0
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
                    f"SELECT id, superseded_at FROM {tenant_a.ctx.silver_schema}.gstr_3b"
                    " WHERE entity_id = %s ORDER BY id",
                    (entity_id,),
                )
            ).fetchall()
            assert len(rows) == 2
            assert rows[0][1] is not None
            assert rows[1][1] is None


@pytest.mark.asyncio
async def test_dispatch_load_routes_gstr_3b_through_gstn_json_promote(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
) -> None:
    entity_id = uuid.uuid4()
    outcome = await dispatch_load(
        tenant_a.ctx, app_pool,
        ingest_id=uuid.uuid4(), entity_id=entity_id, doc_type_code="GSTR_3B",
        data=FIXTURE.read_bytes(), content_format="json",
        period_start=PERIOD_START, period_end=PERIOD_END, gstin=GSTIN,
    )
    assert outcome.mechanism == "GSTN_JSON_PROMOTE"
    assert outcome.status == "ACCEPTED"
    assert outcome.batch_id is not None
