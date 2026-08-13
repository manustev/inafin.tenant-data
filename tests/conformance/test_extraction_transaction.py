"""Archetype 1 extraction — A4.08 GTA consignment note, A6.01 Bill of Entry,
against the REAL specimen PDFs. No new persistence: both go through
`SilverPromotionService.promote_transaction_documents` unchanged, so what's
under test here is only the PDF-to-CSV-row synthesis.

Corrective refactor (2026-08-11): `doc_type_code`/`label_spec` are no longer
`ClassVar`s on `GtaConsignmentNoteExtractor`/`BillOfEntryExtractor` — they
come from `platform_ref.document_type.extraction_spec` at registry-build
time, so every extractor here is pulled from the `extractor_registry` fixture
(`tests/conftest.py`) rather than constructed directly.
"""

from __future__ import annotations

import pathlib
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.extraction.base import DocumentExtractor
from src.extraction.labelvalue import Extracted, NoTextLayer, Partial
from src.extraction.reader import PypdfReader

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "reference" / "A1-A7Documents"


def _read(filename: str) -> bytes:
    return (SAMPLES / filename).read_bytes()


def test_gta_consignment_note_extracts_cleanly(
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    pdf_text = PypdfReader().extract(_read("A4.08_GTA_Invoice_Consignment_Note.pdf"))
    assert pdf_text.has_native_text
    extractor = extractor_registry["GTA_INVOICE_CONSIGNMENT_NOTE"]
    outcome = extractor.extract(pdf_text.pages)
    assert isinstance(outcome, Extracted)
    assert outcome.fields["doc_number"] == "BLS/24-25/008842"
    assert outcome.fields["counterparty_gstin"] == "27AAEFB3210G1Z4"


def test_bill_of_entry_extracts_a_named_partial(
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """The amounts sit in a colon-less table — see transaction_types.py's
    module docstring. This IS the honest tier-1 outcome, not a bug."""
    pdf_text = PypdfReader().extract(_read("A6.01_Bill_of_Entry.pdf"))
    assert pdf_text.has_native_text
    extractor = extractor_registry["BILL_OF_ENTRY"]
    outcome = extractor.extract(pdf_text.pages)
    assert isinstance(outcome, Partial)
    assert set(outcome.missing) == {"taxable_value", "igst_amount"}
    assert outcome.fields["doc_number"] == "6738214"
    assert outcome.fields["port_code"] == "INNSA1 (Nhava Sheva)"


async def test_gta_consignment_note_promotes_a_real_transaction_document(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """`test_isolation.py::test_support_is_read_only` hardcodes
    `transaction_document`'s row count from the session-scoped seed — this
    test's own write is cleaned up in `finally` so it does not leave that
    count wrong for a test running later in the same session."""
    extractor = extractor_registry["GTA_INVOICE_CONSIGNMENT_NOTE"]
    pdf_bytes = _read("A4.08_GTA_Invoice_Consignment_Note.pdf")
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    try:
        assert result.written and result.record_id is not None  # record_id is the batch_id

        row = admin.execute(
            sql.SQL(
                "SELECT doc_type, direction, doc_number, counterparty_gstin,"
                "       total_taxable_value, attributes"
                "  FROM {}.transaction_document WHERE batch_id = %s"
            ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
            (result.record_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "GTA_INVOICE_CONSIGNMENT_NOTE"
        assert row[1] == "INWARD"
        assert row[2] == "BLS/24-25/008842"
        assert row[3] == "27AAEFB3210G1Z4"
        assert row[5]["rcm_applicable"] == "YES"
    finally:
        admin.execute(
            sql.SQL("DELETE FROM {}.transaction_document WHERE batch_id = %s").format(
                sql.Identifier(tenant_a.ctx.silver_schema)
            ),
            (result.record_id,),
        )
        admin.execute(
            sql.SQL("DELETE FROM {}.ingest_batch WHERE batch_id = %s").format(
                sql.Identifier(tenant_a.ctx.silver_schema)
            ),
            (result.record_id,),
        )


async def test_bill_of_entry_partial_quarantines_instead_of_promoting(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry["BILL_OF_ENTRY"]
    pdf_bytes = _read("A6.01_Bill_of_Entry.pdf")
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Partial)

    ingest_id = uuid.uuid4()
    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=ingest_id, pdf_bytes=pdf_bytes,
    )
    assert not result.written

    row = admin.execute(
        sql.SQL("SELECT reason FROM {}.quarantined_artefact WHERE ingest_id = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (ingest_id,),
    ).fetchone()
    assert row is not None
    assert "taxable_value" in row[0] and "igst_amount" in row[0]


async def test_no_text_layer_quarantines_an_archetype_1_extraction(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry["GTA_INVOICE_CONSIGNMENT_NOTE"]
    ingest_id = uuid.uuid4()
    result = await extractor.to_silver(
        NoTextLayer(), tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=ingest_id, pdf_bytes=b"x",
    )
    assert not result.written and result.reason == "NoTextLayer"
