"""Archetype 6/7/4/8 extraction — proceeding_event, entity_master_record,
financial_statement_extract, narrative_contract — against the REAL specimen
PDFs, same discipline as `test_extraction_entitlement.py`.

Also the DDL-gate + business-rule tests for the four new tables (mirroring
`tests/conformance/test_entitlement.py`'s shape — hand-written, no
`information_schema` walk).

Corrective refactor (2026-08-11): extractors are pulled from the
`extractor_registry` fixture (`tests/conftest.py`), which builds the
production runtime path — `platform_ref.document_type.extraction_spec` ->
`src/extraction/registry.py`'s `build_extractor_registry` — instead of
importing hand-written classes directly.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.core.errors import TenantBoundaryViolation
from src.core.pool import TenantScopedPool
from src.core.tenant import Role
from src.extraction.base import (
    DocumentExtractor,
    EntityMasterExtractor,
    FinancialStatementExtractor,
    NarrativeContractExtractor,
    ProceedingEventExtractor,
)
from src.extraction.labelvalue import Extracted, NoTextLayer, Partial
from src.extraction.reader import PypdfReader
from src.extraction.tablevalue import parse_table_rows
from src.silver.entity_master import EntityMasterRecord, EntityMasterService
from src.silver.financial_statement import FinancialStatementRecord, FinancialStatementService
from src.silver.narrative_contract import NarrativeContractRecord, NarrativeContractService
from src.silver.proceeding_event import ProceedingEventRecord, ProceedingEventService

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "reference" / "A1-A7Documents"

SAMPLE_FILE: dict[str, str] = {
    "RBI_COMPOUNDING_ORDER": "A7.02_RBI_Compounding_Order.pdf",
    "ITC_04_ACKNOWLEDGEMENT": "A5.17_ITC-04_Acknowledgements.pdf",
    "CERTIFICATE_OF_INCORPORATION": "A2.01_Certificate_of_Incorporation.pdf",
    "SHAREHOLDING_PATTERN": "A2.03_Shareholding_Pattern.pdf",
    "RELATED_PARTY_REGISTER": "A2.04_Related_Party_Register.pdf",
    "GSTIN_REGISTER": "A2.07_GSTIN_Register.pdf",
    "DIRECTOR_LIST_WITH_DIN": "A3.01_List_of_Directors_with_DIN.pdf",
    "FORM_DIR_12": "A3.02_Form_DIR-12.pdf",
    "KMP_LIST": "A3.06_KMP_List.pdf",
    "RELATED_PARTY_DISCLOSURE_IND_AS_24": "A2.05_IndAS24_Related_Party_Disclosure.pdf",
    "MEMORANDUM_ARTICLES_OF_ASSOCIATION": "A2.02_Memorandum_Articles_of_Association_Extract.pdf",
    "JOINT_VENTURE_AGREEMENT": "A2.06_Joint_Venture_Agreement.pdf",
    "BOARD_RESOLUTION_DIRECTOR_APPOINTMENT": "A3.03_Board_Resolution_WTD_Appointment.pdf",
    "DIRECTOR_SERVICE_AGREEMENT": "A3.04_Director_Service_Agreement.pdf",
    "COST_SHARING_AGREEMENT": "A4.01_Cost_Sharing_Agreement.pdf",
    "FOREIGN_CUSTOMER_SERVICE_CONTRACT": "A4.02_Service_Contract_Foreign_Customer.pdf",
    "OVERSEAS_VENDOR_CONTRACT": "A4.03_Overseas_Vendor_Contract_SaaS.pdf",
    "JOBWORK_AGREEMENT": "A4.04_Job_Work_Agreement.pdf",
    "LONG_DURATION_SERVICE_CONTRACT": "A4.05_Long_Duration_Service_Contract.pdf",
    "TRANSFER_PRICING_DOCUMENTATION": "A4.06_Transfer_Pricing_Documentation.pdf",
    "SECURITY_AGENCY_CONTRACT": "A4.09_Security_Agency_Contract.pdf",
}

#: doc_type_code -> named Partial(missing=...), for every specimen that
#: legitimately does not extract cleanly (see registry/document_types.csv's
#: comments and the *_types.py module docstrings for why each one is what it
#: is).
EXPECTED_PARTIAL_MISSING: dict[str, tuple[str, ...]] = {
    "ITC_04_ACKNOWLEDGEMENT": ("reference_number", "event_date"),
    "KMP_LIST": ("as_of_date",),
}


def _read(filename: str) -> bytes:
    return (SAMPLES / filename).read_bytes()


@pytest.mark.parametrize("doc_type_code", sorted(SAMPLE_FILE))
def test_every_sampled_archetype_6_7_4_8_type_extracts_a_named_outcome(
    doc_type_code: str, extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry[doc_type_code]
    reader = PypdfReader()
    pdf_text = reader.extract(_read(SAMPLE_FILE[doc_type_code]))
    assert pdf_text.has_native_text

    outcome = extractor.extract(pdf_text.pages)

    if doc_type_code in EXPECTED_PARTIAL_MISSING:
        assert isinstance(outcome, Partial), f"{doc_type_code}: expected Partial: {outcome!r}"
        assert outcome.missing == EXPECTED_PARTIAL_MISSING[doc_type_code]
        assert outcome.failed == ()
    else:
        assert isinstance(outcome, Extracted), f"{doc_type_code}: expected Extracted: {outcome!r}"


def test_every_sample_type_resolves_to_its_expected_archetype_base(
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """The archetype dispatch (`src/extraction/registry.py`'s
    `_ARCHETYPE_BASE_CLASS`) is fixed code — this pins that
    `RBI_COMPOUNDING_ORDER`/`ITC_04_ACKNOWLEDGEMENT` land on
    `ProceedingEventExtractor`, the seven entity-master types on
    `EntityMasterExtractor`, the one financial-statement type on
    `FinancialStatementExtractor`, and the eleven narrative-contract types on
    `NarrativeContractExtractor`."""
    expected: dict[str, type[DocumentExtractor]] = {
        "RBI_COMPOUNDING_ORDER": ProceedingEventExtractor,
        "ITC_04_ACKNOWLEDGEMENT": ProceedingEventExtractor,
        "CERTIFICATE_OF_INCORPORATION": EntityMasterExtractor,
        "SHAREHOLDING_PATTERN": EntityMasterExtractor,
        "RELATED_PARTY_REGISTER": EntityMasterExtractor,
        "GSTIN_REGISTER": EntityMasterExtractor,
        "DIRECTOR_LIST_WITH_DIN": EntityMasterExtractor,
        "FORM_DIR_12": EntityMasterExtractor,
        "KMP_LIST": EntityMasterExtractor,
        "RELATED_PARTY_DISCLOSURE_IND_AS_24": FinancialStatementExtractor,
    }
    for code, base in expected.items():
        assert isinstance(extractor_registry[code], base), code
    narrative_codes = {
        "MEMORANDUM_ARTICLES_OF_ASSOCIATION", "JOINT_VENTURE_AGREEMENT",
        "BOARD_RESOLUTION_DIRECTOR_APPOINTMENT", "DIRECTOR_SERVICE_AGREEMENT",
        "COST_SHARING_AGREEMENT", "FOREIGN_CUSTOMER_SERVICE_CONTRACT",
        "OVERSEAS_VENDOR_CONTRACT", "JOBWORK_AGREEMENT",
        "LONG_DURATION_SERVICE_CONTRACT", "TRANSFER_PRICING_DOCUMENTATION",
        "SECURITY_AGENCY_CONTRACT",
    }
    for code in narrative_codes:
        assert isinstance(extractor_registry[code], NarrativeContractExtractor), code
        assert extractor_registry[code].label_spec == {}


# =============================================================================
# The write path
# =============================================================================


async def test_extracted_rbi_compounding_order_writes_a_proceeding_event(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry["RBI_COMPOUNDING_ORDER"]
    pdf_bytes = _read(SAMPLE_FILE["RBI_COMPOUNDING_ORDER"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written and result.record_id is not None

    row = admin.execute(
        sql.SQL(
            "SELECT event_type, reference_number, amount"
            "  FROM {}.proceeding_event WHERE event_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "RBI_COMPOUNDING_ORDER"
    assert row[1] == "CA/FED/MUM/2022/00274"


async def test_extracted_certificate_of_incorporation_writes_an_entity_master_record(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry["CERTIFICATE_OF_INCORPORATION"]
    pdf_bytes = _read(SAMPLE_FILE["CERTIFICATE_OF_INCORPORATION"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written

    row = admin.execute(
        sql.SQL("SELECT master_type, reference_number FROM {}.entity_master_record"
                 " WHERE record_id = %s").format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "CERTIFICATE_OF_INCORPORATION"
    assert row[1] == "U29100MH2015PTC271034"


async def test_shareholding_pattern_extracts_and_writes_an_entity_master_record(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """`as_of_date` was mislabeled — "As On Date", a label that does not
    appear anywhere in the specimen — not a genuine tier-1 gap. Shared
    migration 048 re-points it at "Financial Year", the same line
    `reference_number` already reads; `_parse_date`'s `.search()` finds the
    date inside that line's trailing "(as on 31 March 2025)"."""
    extractor = extractor_registry["SHAREHOLDING_PATTERN"]
    pdf_bytes = _read(SAMPLE_FILE["SHAREHOLDING_PATTERN"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)
    assert outcome.fields["as_of_date"] == dt.date(2025, 3, 31)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written

    row = admin.execute(
        sql.SQL("SELECT master_type, reference_number, as_of_date"
                 "  FROM {}.entity_master_record WHERE record_id = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (result.record_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "SHAREHOLDING_PATTERN"
    assert row[2] == dt.date(2025, 3, 31)

    # The table content — 6 holders x 2 financial-year validity windows,
    # never captured before this session (`shareholding_pattern_holder`,
    # tenant migration 032). 12, not 6: the FY24-25/FY23-24 %-holding
    # columns are two DIFFERENT facts with two different validity windows,
    # not two columns of one fact.
    facts = admin.execute(
        sql.SQL(
            "SELECT holder_name, pct_holding, valid_from, valid_to"
            "  FROM {}.shareholding_pattern_holder"
            " WHERE record_id = %s ORDER BY holder_name, valid_from"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchall()
    assert len(facts) == 12
    by_key = {(r[0], r[2]): r for r in facts}
    assert by_key[("Arvind Rao Deshmukh (Promoter / MD)", dt.date(2024, 4, 1))] == (
        "Arvind Rao Deshmukh (Promoter / MD)", 37, dt.date(2024, 4, 1), dt.date(2025, 3, 31),
    )
    assert by_key[("Arvind Rao Deshmukh (Promoter / MD)", dt.date(2023, 4, 1))] == (
        "Arvind Rao Deshmukh (Promoter / MD)", 37, dt.date(2023, 4, 1), dt.date(2024, 3, 31),
    )
    assert by_key[("Rajeev Malhotra (Independent Director)", dt.date(2024, 4, 1))][1] == 0.5


async def test_shareholding_pattern_resubmission_supersedes_the_fact_rows(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """Re-ingesting the same PDF closes the old 12 rows and inserts a fresh
    12 under the new `record_id` — full-snapshot replace, matching
    `entity_master_record` itself (tenant migration 032's header)."""
    extractor = extractor_registry["SHAREHOLDING_PATTERN"]
    pdf_bytes = _read(SAMPLE_FILE["SHAREHOLDING_PATTERN"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    entity_id = uuid.uuid4()
    first = await extractor.to_silver(
        outcome, tenant_a.ctx, entity_id=entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    second = await extractor.to_silver(
        outcome, tenant_a.ctx, entity_id=entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert first.written and second.written
    assert first.record_id != second.record_id

    schema = sql.Identifier(tenant_a.ctx.silver_schema)
    old_open = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.shareholding_pattern_holder"
            " WHERE record_id = %s AND superseded_at IS NULL"
        ).format(schema),
        (first.record_id,),
    ).fetchone()
    assert old_open == (0,)

    new_open = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.shareholding_pattern_holder"
            " WHERE record_id = %s AND superseded_at IS NULL"
        ).format(schema),
        (second.record_id,),
    ).fetchone()
    assert new_open == (12,)


async def test_director_list_extracts_and_writes_an_entity_master_record(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """Same class of mislabeling as SHAREHOLDING_PATTERN. Shared migration 050
    re-points `as_of_date` at "Source" — the specimen's real provenance line
    ("Source: ... cross-verified as at 31 March 2025.") — not "List Date",
    which appears nowhere in the document."""
    extractor = extractor_registry["DIRECTOR_LIST_WITH_DIN"]
    pdf_bytes = _read(SAMPLE_FILE["DIRECTOR_LIST_WITH_DIN"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)
    assert outcome.fields["as_of_date"] == dt.date(2025, 3, 31)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written

    row = admin.execute(
        sql.SQL("SELECT master_type, reference_number, as_of_date"
                 "  FROM {}.entity_master_record WHERE record_id = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (result.record_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "DIRECTOR_LIST_WITH_DIN"
    assert row[2] == dt.date(2025, 3, 31)

    # The table content — 6 directors, replicating the SHAREHOLDING_PATTERN
    # table-fact pattern (`director_list_din`, tenant migration 034).
    facts = admin.execute(
        sql.SQL(
            "SELECT name, din, designation, currently_active"
            "  FROM {}.director_list_din"
            " WHERE record_id = %s ORDER BY name"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchall()
    assert len(facts) == 6
    by_din = {r[1]: r for r in facts}
    assert by_din["01234567"] == (
        "Arvind Rao Deshmukh", "01234567", "Managing Director", "Yes",
    )
    assert by_din["05678901"] == (
        "Gerald Fernandes", "05678901", "Non-Executive Director", "No (resigned)",
    )


async def test_director_list_resubmission_supersedes_the_fact_rows(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """The same full-snapshot-replace supersede as SHAREHOLDING_PATTERN, now
    exercised through `TableFactExtractor`'s GENERIC (not FY-expanding)
    `_extra_silver_write` — the code path every table-shaped type besides
    `SHAREHOLDING_PATTERN` shares."""
    extractor = extractor_registry["DIRECTOR_LIST_WITH_DIN"]
    pdf_bytes = _read(SAMPLE_FILE["DIRECTOR_LIST_WITH_DIN"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    entity_id = uuid.uuid4()
    first = await extractor.to_silver(
        outcome, tenant_a.ctx, entity_id=entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    second = await extractor.to_silver(
        outcome, tenant_a.ctx, entity_id=entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert first.written and second.written
    assert first.record_id != second.record_id

    schema = sql.Identifier(tenant_a.ctx.silver_schema)
    old_open = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.director_list_din"
            " WHERE record_id = %s AND superseded_at IS NULL"
        ).format(schema),
        (first.record_id,),
    ).fetchone()
    assert old_open == (0,)

    new_open = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.director_list_din"
            " WHERE record_id = %s AND superseded_at IS NULL"
        ).format(schema),
        (second.record_id,),
    ).fetchone()
    assert new_open == (6,)


async def test_gstin_register_extracts_and_writes_an_entity_master_record(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """`as_of_date` was mislabeled, not a genuine tier-1 gap — the specimen
    states no "Register Date" line, but DOES carry a real as-of date on its
    table header, "Status (as on 31-Mar-2025)". Shared migration 058
    re-points the label there; `_find_value`'s new colon-less date fallback
    (`src/extraction/labelvalue.py`, mirroring the existing `money` one)
    reconstructs it across the line wrap `pypdf` introduces mid-token."""
    extractor = extractor_registry["GSTIN_REGISTER"]
    pdf_bytes = _read(SAMPLE_FILE["GSTIN_REGISTER"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)
    assert outcome.fields["as_of_date"] == dt.date(2025, 3, 31)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written

    row = admin.execute(
        sql.SQL("SELECT master_type, reference_number, as_of_date"
                 "  FROM {}.entity_master_record WHERE record_id = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (result.record_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "GSTIN_REGISTER"
    assert row[2] == dt.date(2025, 3, 31)

    # The table content — 5 GSTINs, replicating the SHAREHOLDING_PATTERN
    # table-fact pattern (`gstin_register_entry`, tenant migration 033).
    facts = admin.execute(
        sql.SQL(
            "SELECT gstin, state, status FROM {}.gstin_register_entry"
            " WHERE record_id = %s ORDER BY gstin"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchall()
    assert len(facts) == 5
    by_gstin = {r[0]: r for r in facts}
    assert by_gstin["27AABCM4521F1Z5"] == ("27AABCM4521F1Z5", "Maharashtra", "Active")
    assert by_gstin["06AABCM4521F1ZM"][2].startswith("Suspended")


async def test_gstin_register_resubmission_supersedes_the_fact_rows(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """Same full-snapshot-replace supersede as `test_director_list_
    resubmission_supersedes_the_fact_rows` — a second real exercise of
    `TableFactExtractor`'s generic `_extra_silver_write`."""
    extractor = extractor_registry["GSTIN_REGISTER"]
    pdf_bytes = _read(SAMPLE_FILE["GSTIN_REGISTER"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    entity_id = uuid.uuid4()
    first = await extractor.to_silver(
        outcome, tenant_a.ctx, entity_id=entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    second = await extractor.to_silver(
        outcome, tenant_a.ctx, entity_id=entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert first.written and second.written
    assert first.record_id != second.record_id

    schema = sql.Identifier(tenant_a.ctx.silver_schema)
    old_open = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.gstin_register_entry"
            " WHERE record_id = %s AND superseded_at IS NULL"
        ).format(schema),
        (first.record_id,),
    ).fetchone()
    assert old_open == (0,)

    new_open = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.gstin_register_entry"
            " WHERE record_id = %s AND superseded_at IS NULL"
        ).format(schema),
        (second.record_id,),
    ).fetchone()
    assert new_open == (5,)


def test_kmp_list_table_grammar_is_ready_despite_the_header_gap(
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """Same reasoning as `test_gstin_register_table_grammar_is_ready_
    despite_the_header_gap` — `KMP_LIST` stays `Partial`, shared migration
    057 reverted 055's attempt to drop `as_of_date` to optional, and the
    table grammar is proven ready independent of that gap."""
    extractor = extractor_registry["KMP_LIST"]
    assert isinstance(extractor, EntityMasterExtractor)
    pdf_bytes = _read(SAMPLE_FILE["KMP_LIST"])
    pages = PypdfReader().extract(pdf_bytes).pages

    outcome = extractor.extract(pages)
    assert isinstance(outcome, Partial)
    assert outcome.missing == ("as_of_date",)

    table_spec = extractor._table_spec  # type: ignore[attr-defined]
    rows = parse_table_rows(pages, table_spec)
    assert len(rows) == 4
    by_name = {r["name"]: r for r in rows}
    assert by_name["Arvind Rao Deshmukh"] == {
        "name": "Arvind Rao Deshmukh", "designation": "Managing Director & CEO",
        "din": "01234567", "membership": "—",
    }
    assert by_name["Prakash Menon"] == {
        "name": "Prakash Menon", "designation": "Chief Financial Officer",
        "din": "N/A (not a director)", "membership": "PAN AFXPM7821L",
    }


async def test_related_party_disclosure_writes_a_sparse_financial_statement_extract(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    extractor = extractor_registry["RELATED_PARTY_DISCLOSURE_IND_AS_24"]
    pdf_bytes = _read(SAMPLE_FILE["RELATED_PARTY_DISCLOSURE_IND_AS_24"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written

    row = admin.execute(
        sql.SQL(
            "SELECT auditor, financial_year, headline_figures"
            "  FROM {}.financial_statement_extract WHERE extract_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchone()
    assert row is not None
    assert "Deshpande & Vora" in row[0]
    assert row[1] is None  # not bound from this specimen — accepted limitation
    assert row[2] == {}


async def test_prose_contract_writes_a_row_with_empty_key_terms(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    """The archetype-8 design point under test: a document with zero bound
    facts is a SUCCESSFUL write, not a quarantine — the MinIO Silver copy is
    the primary record for this archetype."""
    extractor = extractor_registry["COST_SHARING_AGREEMENT"]
    pdf_bytes = _read(SAMPLE_FILE["COST_SHARING_AGREEMENT"])
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)
    assert outcome.fields == {}

    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=uuid.uuid4(), pdf_bytes=pdf_bytes,
    )
    assert result.written

    row = admin.execute(
        sql.SQL("SELECT contract_type, key_terms FROM {}.narrative_contract"
                 " WHERE contract_id = %s").format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (result.record_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "COST_SHARING_AGREEMENT"
    assert row[1] == {}


async def test_no_text_layer_quarantines_across_all_four_archetypes(
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    extractor_registry: dict[str, DocumentExtractor],
) -> None:
    for doc_type_code in (
        "RBI_COMPOUNDING_ORDER", "CERTIFICATE_OF_INCORPORATION",
        "RELATED_PARTY_DISCLOSURE_IND_AS_24", "COST_SHARING_AGREEMENT",
    ):
        extractor = extractor_registry[doc_type_code]
        ingest_id = uuid.uuid4()
        result = await extractor.to_silver(
            NoTextLayer(), tenant_a.ctx,
            entity_id=tenant_a.entity_id, ingest_id=ingest_id, pdf_bytes=b"x",
        )
        assert not result.written and result.reason == "NoTextLayer"


# =============================================================================
# DDL-gate + business-rule tests for the four new tables
# =============================================================================


def _batch_id(admin: psycopg.Connection[tuple[object, ...]], tenant: SeededTenant) -> uuid.UUID:
    row = admin.execute(
        sql.SQL("SELECT batch_id FROM {}.ingest_batch LIMIT 1").format(
            sql.Identifier(tenant.ctx.silver_schema)
        )
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def test_proceeding_event_rejects_a_non_archetype_6_type(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The composite FK (event_type, 6) -> document_type(doc_type_code,
    archetype) is what makes the registry enforce archetype membership rather
    than merely describe it — same gate shape as test_entitlement.py's."""
    svc = ProceedingEventService(app_pool)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await svc.record(
            tenant_a.ctx,
            ProceedingEventRecord(
                entity_id=tenant_a.entity_id, event_type="LUT",  # archetype 3, not 6
                authority="RBI", reference_number="NOT-A-PROCEEDING",
                event_date=dt.date(2024, 1, 1),
                batch_id=_batch_id(admin, tenant_a), bronze_ingest_id=uuid.uuid4(),
            ),
        )


async def test_proceeding_event_supersession_keeps_the_prior_row(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    svc = ProceedingEventService(app_pool)
    batch = _batch_id(admin, tenant_a)
    rec = ProceedingEventRecord(
        entity_id=tenant_a.entity_id, event_type="RBI_COMPOUNDING_ORDER",
        authority="RBI", reference_number=f"CA-{uuid.uuid4().hex[:8]}",
        event_date=dt.date(2024, 1, 1), batch_id=batch, bronze_ingest_id=uuid.uuid4(),
    )
    prior = await svc.record(tenant_a.ctx, rec)
    corrected = await svc.supersede(tenant_a.ctx, prior_id=prior, rec=rec)

    prior_row = admin.execute(
        sql.SQL("SELECT superseded_at IS NOT NULL FROM {}.proceeding_event"
                 " WHERE event_id = %s").format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (prior,),
    ).fetchone()
    assert prior_row is not None and prior_row[0] is True
    current_row = admin.execute(
        sql.SQL("SELECT superseded_at IS NULL FROM {}.proceeding_event"
                 " WHERE event_id = %s").format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (corrected,),
    ).fetchone()
    assert current_row is not None and current_row[0] is True


async def test_one_tenant_cannot_read_another_tenants_proceeding_events(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, tenant_b: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    svc = ProceedingEventService(app_pool)
    await svc.record(
        tenant_b.ctx,
        ProceedingEventRecord(
            entity_id=tenant_b.entity_id, event_type="RBI_COMPOUNDING_ORDER",
            authority="RBI", reference_number="CA-B-SECRET",
            event_date=dt.date(2024, 1, 1),
            batch_id=_batch_id(admin, tenant_b), bronze_ingest_id=uuid.uuid4(),
        ),
    )
    control = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.proceeding_event WHERE reference_number = 'CA-B-SECRET'")
        .format(sql.Identifier(tenant_b.ctx.silver_schema))
    ).fetchone()
    assert control is not None and control[0] == 1, "seed failed — gate is vacuous"

    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.v1_proceeding_event").format(
                    sql.Identifier(tenant_b.ctx.silver_schema)
                )
            )


async def test_entity_master_record_rejects_a_non_archetype_7_type(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The composite FK (master_type, 7) -> document_type(doc_type_code,
    archetype) — same gate shape as proceeding_event's."""
    svc = EntityMasterService(app_pool)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await svc.record(
            tenant_a.ctx,
            EntityMasterRecord(
                entity_id=tenant_a.entity_id, master_type="LUT",  # archetype 3, not 7
                reference_number="NOT-A-MASTER-RECORD", as_of_date=dt.date(2024, 1, 1),
                batch_id=_batch_id(admin, tenant_a), bronze_ingest_id=uuid.uuid4(),
            ),
        )


async def test_entity_master_record_current_uq_refuses_a_duplicate(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    svc = EntityMasterService(app_pool)
    batch = _batch_id(admin, tenant_a)
    rec = EntityMasterRecord(
        entity_id=tenant_a.entity_id, master_type="CERTIFICATE_OF_INCORPORATION",
        reference_number="CIN-DUPLICATE", as_of_date=dt.date(2015, 3, 14),
        batch_id=batch, bronze_ingest_id=uuid.uuid4(),
    )
    await svc.record(tenant_a.ctx, rec)
    with pytest.raises(psycopg.errors.UniqueViolation):
        await svc.record(tenant_a.ctx, rec)


async def test_financial_statement_extract_rejects_a_non_archetype_4_type(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    svc = FinancialStatementService(app_pool)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await svc.record(
            tenant_a.ctx,
            FinancialStatementRecord(
                entity_id=tenant_a.entity_id, statement_type="LUT",  # archetype 3, not 4
                auditor="Nobody", batch_id=_batch_id(admin, tenant_a),
                bronze_ingest_id=uuid.uuid4(),
            ),
        )


async def test_narrative_contract_current_uq_refuses_the_same_artefact_twice(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The other half of the CONTENT-key story: the SAME bronze_ingest_id for
    the same (entity, type) must be refused — otherwise the resubmission
    failure mode entitlement_instrument_current_uq guards against would be
    open here too."""
    svc = NarrativeContractService(app_pool)
    batch = _batch_id(admin, tenant_a)
    rec = NarrativeContractRecord(
        entity_id=tenant_a.entity_id, contract_type="COST_SHARING_AGREEMENT",
        batch_id=batch, bronze_ingest_id=uuid.uuid4(),
    )
    await svc.record(tenant_a.ctx, rec)
    with pytest.raises(psycopg.errors.UniqueViolation):
        await svc.record(tenant_a.ctx, rec)


async def test_narrative_contract_current_uq_is_keyed_on_the_artefact(
    app_pool: TenantScopedPool, tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """No document-carried reference number exists for prose contracts — the
    natural key is the artefact itself (CONTENT strategy, same idea
    src/silver/registers/spec.py's KeyKind.CONTENT documents), so two
    DIFFERENT bronze_ingest_ids for the same (entity, type) must both be
    allowed to coexist as current."""
    svc = NarrativeContractService(app_pool)
    batch = _batch_id(admin, tenant_a)
    first = await svc.record(
        tenant_a.ctx,
        NarrativeContractRecord(
            entity_id=tenant_a.entity_id, contract_type="COST_SHARING_AGREEMENT",
            batch_id=batch, bronze_ingest_id=uuid.uuid4(),
        ),
    )
    second = await svc.record(
        tenant_a.ctx,
        NarrativeContractRecord(
            entity_id=tenant_a.entity_id, contract_type="COST_SHARING_AGREEMENT",
            batch_id=batch, bronze_ingest_id=uuid.uuid4(),
        ),
    )
    assert first != second
    rows = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.narrative_contract"
                 " WHERE contract_id IN (%s, %s) AND superseded_at IS NULL").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (first, second),
    ).fetchone()
    assert rows is not None and rows[0] == 2
