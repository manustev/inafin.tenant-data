"""Archetype 3 extraction — the 24 entitlement-instrument types, against the
REAL specimen PDFs in `reference/A1-A7Documents/`.

These are the fixtures, not a stand-in for them — `streamed-marinating-gray
.md`'s verification section is explicit: run every extractor against the real
PDF bytes and assert `Extracted` or a NAMED `Partial` with specific missing
fields, never an unhandled exception. `test_every_sampled_type_extracts_a_
named_outcome` is the load-bearing test; the rest exercise the write path
(quarantine on `Partial`/`NoTextLayer`, a real `entitlement_instrument` row on
`Extracted`) for a representative few rather than all 24, since
`EntitlementService.record` itself is already proven by
`test_entitlement.py` — what is new here is the PDF-to-`InstrumentRecord`
step, not the persistence.

Corrective refactor (2026-08-11): extractors are no longer imported as
hand-written classes (`LutExtractor`, `ENTITLEMENT_EXTRACTORS`, ...) — every
test below pulls its extractor out of the `extractor_registry` fixture
(`tests/conftest.py`), which builds the exact `platform_ref.document_type ->
extraction_spec` runtime path (`src/extraction/registry.py`) production code
uses. This exercises the actual data path, not a bypassed shortcut.
"""

from __future__ import annotations

import io
import pathlib
import uuid

import psycopg
import pypdf
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.extraction.base import DocumentExtractor, EntitlementExtractor
from src.extraction.labelvalue import Extracted, NoTextLayer, Partial, parse_label_value
from src.extraction.reader import PypdfReader

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "reference" / "A1-A7Documents"

#: doc_type_code -> specimen filename. One PDF per archetype-3 type in this
#: batch (registry/document_types.csv's `archetype` column is authoritative
#: for which 24 that is).
SAMPLE_FILE: dict[str, str] = {
    "ADVANCE_PRICING_AGREEMENT": "A4.07_Advance_Pricing_Agreement_Extract.pdf",
    "LUT": "A5.01_LUT_Letter_of_Undertaking.pdf",
    "IEC_CERTIFICATE": "A5.02_IEC_Certificate.pdf",
    "SEZ_LETTER_OF_APPROVAL": "A5.03_SEZ_Letter_of_Approval.pdf",
    "SEZ_LETTER_OF_PERMISSION": "A5.04_SEZ_Letter_of_Permission_ITES.pdf",
    "SEZ_OPERATIONS_COMMENCEMENT_CERT": "A5.05_SEZ_Operations_Commencement_Certificate.pdf",
    "SEZ_NOTIFICATION": "A5.06_SEZ_Notification.pdf",
    "SEZ_ANNUAL_PERFORMANCE_REPORT": "A5.07_SEZ_Annual_Performance_Report.pdf",
    "EOU_LETTER_OF_PERMISSION": "A5.08_EOU_Letter_of_Permission.pdf",
    "STPI_REGISTRATION": "A5.09_STPI_Registration_Letter.pdf",
    "EOU_ANNUAL_COMPLIANCE_REPORT": "A5.10_EOU_Annual_Compliance_Report.pdf",
    "EPCG_LICENCE": "A5.11_EPCG_Licence.pdf",
    "ADVANCE_AUTHORISATION": "A5.12_Advance_Authorisation.pdf",
    "EODC": "A5.13_Export_Obligation_Discharge_Certificate.pdf",
    "SOFTEX_FORM_APPROVAL": "A5.15_SOFTEX_Form_Approval.pdf",
    "CUSTOMS_BONDED_WAREHOUSE_LICENCE": "A5.16_Customs_Bonded_Warehouse_Licence.pdf",
    "CUSTOMS_CLASSIFICATION_ORDER": "A6.02_Customs_Tariff_Classification_Order.pdf",
    "CUSTOMS_DUTY_EXEMPTION_CERTIFICATE": "A6.03_Customs_Duty_Exemption_Certificate.pdf",
    "B17_BOND": "A6.04_B17_Bond.pdf",
    "BLUT": "A6.05_BLUT.pdf",
    "AEO_CERTIFICATE": "A6.06_AEO_Certificate.pdf",
    "RBI_DELAYED_REALISATION_APPROVAL": "A7.01_RBI_Approval_Delayed_Export_Realisation.pdf",
    "ODI_APPROVAL": "A7.03_ODI_Approval.pdf",
    "SCOMET_LICENCE": "A7.05_SCOMET_Licence.pdf",
}

#: Two specimens have no clean label:value date at all (a periodic table, a
#: prose validity clause) — see registry/document_types.csv's comment on
#: those rows and src/extraction/entitlement_types.py's module docstring for
#: why this is an accepted tier-1 limitation, not a bug. Every other type
#: must extract as `Extracted`.
EXPECTED_PARTIAL_MISSING: dict[str, tuple[str, ...]] = {
    "SEZ_ANNUAL_PERFORMANCE_REPORT": ("valid_from",),
    "CUSTOMS_DUTY_EXEMPTION_CERTIFICATE": ("valid_from",),
}


def _read(filename: str) -> bytes:
    return (SAMPLES / filename).read_bytes()


def test_all_24_archetype_3_sample_types_are_registered(
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """The registry's own archetype column, not the plan's prose estimate, is
    authoritative — every SAMPLE_FILE key must resolve to a real
    `EntitlementExtractor` instance, and every archetype-3 registry entry this
    batch built an extraction_spec for must be exactly this set."""
    archetype_3 = {
        code for code, ext in extractor_registry.items() if isinstance(ext, EntitlementExtractor)
    }
    assert archetype_3 == set(SAMPLE_FILE)


@pytest.mark.parametrize("doc_type_code", sorted(SAMPLE_FILE))
def test_every_sampled_type_extracts_a_named_outcome(
    doc_type_code: str, extractor_registry: dict[str, DocumentExtractor]
) -> None:
    """The load-bearing assertion: real bytes in, a NAMED outcome out, never
    an unhandled exception."""
    extractor = extractor_registry[doc_type_code]
    reader = PypdfReader()
    pdf_text = reader.extract(_read(SAMPLE_FILE[doc_type_code]))
    assert pdf_text.has_native_text, f"{doc_type_code}'s specimen unexpectedly has no text layer"

    outcome = extractor.extract(pdf_text.pages)

    if doc_type_code in EXPECTED_PARTIAL_MISSING:
        assert isinstance(outcome, Partial), f"{doc_type_code}: expected Partial, got {outcome!r}"
        assert outcome.missing == EXPECTED_PARTIAL_MISSING[doc_type_code]
        assert outcome.failed == ()
    else:
        assert isinstance(outcome, Extracted), f"{doc_type_code}: expected Extracted: {outcome!r}"
        assert "instrument_number" in outcome.fields
        assert "valid_from" in outcome.fields or "validity" in outcome.fields


def test_no_text_layer_routes_to_the_named_outcome(
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """No real scanned sample exists (`HANDOFF-2026-08-11.md`), so this one
    case is a constructed fixture — the outcome enum is what's under test,
    not a layout, which is the legitimate exception to "never mock a
    government PDF's layout" (CLAUDE.md)."""
    reader = PypdfReader()
    # A syntactically valid, single blank-page PDF, built with pypdf's own
    # writer rather than hand-rolled — pypdf extracts "" from it, which is
    # well under _NATIVE_TEXT_MIN_CHARS.
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    pdf_text = reader.extract(buf.getvalue())
    assert not pdf_text.has_native_text

    extractor = extractor_registry["LUT"]

    outcome = parse_label_value(pdf_text.pages, extractor.label_spec)
    assert isinstance(outcome, NoTextLayer)


# =============================================================================
# The write path — Extracted writes a real row; Partial/NoTextLayer quarantine
# =============================================================================


async def test_extracted_lut_writes_a_real_entitlement_instrument(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry["LUT"]
    pdf_bytes = _read(SAMPLE_FILE["LUT"])
    pdf_text = PypdfReader().extract(pdf_bytes)
    outcome = extractor.extract(pdf_text.pages)
    assert isinstance(outcome, Extracted)

    ingest_id = uuid.uuid4()
    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=ingest_id, pdf_bytes=pdf_bytes,
    )
    assert result.written and result.record_id is not None

    row = admin.execute(
        sql.SQL(
            "SELECT instrument_type, instrument_number, valid_from, valid_to"
            "  FROM {}.entitlement_instrument WHERE instrument_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "LUT"
    assert row[1] == "AD2704240123456"


async def test_partial_extraction_quarantines_instead_of_writing(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry["CUSTOMS_DUTY_EXEMPTION_CERTIFICATE"]
    pdf_bytes = _read(SAMPLE_FILE["CUSTOMS_DUTY_EXEMPTION_CERTIFICATE"])
    pdf_text = PypdfReader().extract(pdf_bytes)
    outcome = extractor.extract(pdf_text.pages)
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
    assert row is not None and "missing required field(s): valid_from" in row[0]


async def test_no_text_layer_quarantines(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry["LUT"]
    ingest_id = uuid.uuid4()
    result = await extractor.to_silver(
        NoTextLayer(), tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=ingest_id, pdf_bytes=b"irrelevant",
    )
    assert not result.written and result.reason == "NoTextLayer"

    row = admin.execute(
        sql.SQL("SELECT reason FROM {}.quarantined_artefact WHERE ingest_id = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (ingest_id,),
    ).fetchone()
    assert row is not None and "NoTextLayer" in row[0]
