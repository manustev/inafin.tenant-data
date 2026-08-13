"""Archetype 2 extraction — PAYROLL_TDS_REGISTER, FIRC_BRC_REGISTER,
FORM_15CA_15CB, against the REAL specimen PDFs. No new persistence: all three
go through `RegisterLoader.load(..., content_format="csv")` unchanged, so
what's under test is the PDF-to-CSV-rows synthesis and that the resulting
rows actually satisfy the new `RegisterSpec`s (tenant migration 020).
"""

from __future__ import annotations

import pathlib
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.core.pool import TenantScopedPool
from src.extraction.labelvalue import Extracted, NoTextLayer
from src.extraction.reader import PypdfReader
from src.extraction.register_types import (
    FircBrcRegisterExtractor,
    Form15ca15cbExtractor,
    PayrollTdsRegisterExtractor,
)

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "reference" / "A1-A7Documents"


def _read(filename: str) -> bytes:
    return (SAMPLES / filename).read_bytes()


def test_payroll_tds_register_extracts_all_five_rows() -> None:
    pdf_text = PypdfReader().extract(_read("A3.05_Payroll_TDS_Register.pdf"))
    assert pdf_text.has_native_text
    extractor = object.__new__(PayrollTdsRegisterExtractor)
    outcome = extractor.extract(pdf_text.pages)
    assert isinstance(outcome, Extracted)
    rows = outcome.fields["rows"]
    assert len(rows) == 5
    assert {r["ref_no"] for r in rows} == {
        "EMP0231", "EMP0045", "CONS0012", "CONS0019", "CONS0027",
    }
    assert {r["classification"] for r in rows} == {"Employee", "Contractor", "Professional"}


def test_firc_brc_register_extracts_all_four_rows_incl_pending() -> None:
    pdf_text = PypdfReader().extract(_read("A5.14_FIRC_BRC_Register.pdf"))
    extractor = object.__new__(FircBrcRegisterExtractor)
    outcome = extractor.extract(pdf_text.pages)
    assert isinstance(outcome, Extracted)
    rows = outcome.fields["rows"]
    assert len(rows) == 4
    pending = next(r for r in rows if r["export_invoice_no"] == "INV/EXP/24-25/0301")
    assert pending["firc_no"] == ""
    assert "Pending" in pending["realisation_status"]


def test_form_15ca_15cb_extracts_its_one_row() -> None:
    pdf_text = PypdfReader().extract(_read("A7.04_Form_15CA_15CB.pdf"))
    extractor = object.__new__(Form15ca15cbExtractor)
    outcome = extractor.extract(pdf_text.pages)
    assert isinstance(outcome, Extracted)
    rows = outcome.fields["rows"]
    assert len(rows) == 1
    assert rows[0]["form_15ca_ack_no"] == "156789234561234"
    assert rows[0]["remittance_amount_inr"] == "1992000"


async def test_payroll_tds_register_loads_into_the_real_table(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    extractor = PayrollTdsRegisterExtractor(app_pool)
    pdf_bytes = _read("A3.05_Payroll_TDS_Register.pdf")
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written and result.reason == "inserted=5 unchanged=0 superseded=0 rejected=0"

    count = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.payroll_tds_register"
                 " WHERE batch_id = %s").format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchone()
    assert count is not None and count[0] == 5


async def test_firc_brc_register_loads_into_the_real_table(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    extractor = FircBrcRegisterExtractor(app_pool)
    pdf_bytes = _read("A5.14_FIRC_BRC_Register.pdf")
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written

    row = admin.execute(
        sql.SQL("SELECT firc_no FROM {}.firc_brc_register"
                 " WHERE batch_id = %s AND export_invoice_no = 'INV/EXP/24-25/0301'").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (result.record_id,),
    ).fetchone()
    assert row is not None and row[0] is None


async def test_form_15ca_15cb_loads_into_the_real_table(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    extractor = Form15ca15cbExtractor(app_pool)
    pdf_bytes = _read("A7.04_Form_15CA_15CB.pdf")
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written

    row = admin.execute(
        sql.SQL("SELECT remittance_amount_inr FROM {}.form_15ca_15cb"
                 " WHERE batch_id = %s").format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchone()
    assert row is not None and row[0] == 1992000


async def test_no_text_layer_quarantines_a_register_extraction(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    extractor = PayrollTdsRegisterExtractor(app_pool)
    ingest_id = uuid.uuid4()
    result = await extractor.to_silver(
        NoTextLayer(), tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=ingest_id, pdf_bytes=b"x",
    )
    assert not result.written and result.reason == "NoTextLayer"
