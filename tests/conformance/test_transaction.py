"""Archetype 1 — the transaction document.

The claim under test is ARCHITECTURE.md 6's: eleven document types share one
table pair, and adding the twelfth is a registry row rather than a sprint. A
suite that only exercised PURCHASE_REGISTER would pass just as happily if the
abstraction were fake, so the load-bearing tests here are the ones that run the
SAME code over a second and third document type with nothing type-specific in
between.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.core.errors import TenantBoundaryViolation, ValidationRejected
from src.core.pool import TenantScopedPool
from src.silver.contract import (
    ContractError,
    Counterparty,
    Direction,
    parse_contract,
)
from src.silver.promote import SilverPromotionService
from src.silver.validate import validate_transaction_csv

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
REGISTRY_CSV = ROOT / "registry" / "document_types.csv"

PERIOD_START = dt.date(2026, 4, 1)
PERIOD_END = dt.date(2026, 4, 30)


def _registry_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(REGISTRY_CSV.open()))


def _archetype_1_codes() -> list[str]:
    return [
        r["doc_type_code"]
        for r in _registry_rows()
        if r["archetype"] == "1" and r["stream"] != "CORPUS" and r["field_contract"]
    ]


def _contract_for(code: str):
    for r in _registry_rows():
        if r["doc_type_code"] == code and r["field_contract"]:
            return parse_contract(r["field_contract"])
    raise AssertionError(f"no contract for {code}")


# =============================================================================
# The grammar
# =============================================================================


def test_contract_parses_all_clauses() -> None:
    c = parse_contract(
        "direction=OUTWARD;counterparty=FOREIGN;"
        "doc=sb_number:text!,port:text;line=fob:money!"
    )
    assert c.direction is Direction.OUTWARD
    assert c.counterparty is Counterparty.FOREIGN
    assert c.required_doc_attributes == ("sb_number",)
    assert c.required_line_attributes == ("fob",)
    assert c.doc_attribute("port") is not None
    assert c.doc_attribute("port").required is False


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("counterparty=REQUIRED", "must declare direction"),
        ("direction=OUTWARD", "must declare counterparty"),
        ("directon=OUTWARD;counterparty=REQUIRED", "unknown clause"),
        ("direction=SIDEWAYS;counterparty=REQUIRED", "direction 'SIDEWAYS'"),
        ("direction=INWARD;counterparty=MAYBE", "counterparty 'MAYBE'"),
        ("direction=INWARD;counterparty=REQUIRED;doc=x:sha256", "unknown type"),
        ("direction=INWARD;counterparty=REQUIRED;doc=NoCaps:text", "must match"),
        ("direction=INWARD;counterparty=REQUIRED;doc=a:text,a:date", "declared twice"),
        ("direction=INWARD;counterparty=REQUIRED;doc=bare", "must be name:type"),
        ("direction=INWARD;direction=OUTWARD;counterparty=REQUIRED", "declared twice"),
    ],
)
def test_malformed_contract_is_rejected(raw: str, fragment: str) -> None:
    """Strict, not forgiving.

    A typo'd clause that parsed to "nothing declared" would apply the wrong
    counterparty rules to an entire document type, and every row it produced
    would look perfectly well formed.
    """
    with pytest.raises(ContractError, match=fragment):
        parse_contract(raw)


def test_every_archetype_1_type_has_a_parseable_contract() -> None:
    codes = _archetype_1_codes()
    assert len(codes) == 11, f"expected 11 archetype-1 types, found {len(codes)}"
    for code in codes:
        _contract_for(code)  # raises ContractError if malformed


def test_registry_csv_and_database_agree(admin: psycopg.Connection) -> None:
    """The CSV is the source of truth; the database must be its compiled form.

    Drift here means someone edited one and not the other, and the deployed
    behaviour would silently stop matching what the CA team reviewed.
    """
    rows = admin.execute(
        "SELECT doc_type_code, field_contract FROM platform_ref.document_type"
        " WHERE field_contract <> ''"
    ).fetchall()
    in_db = dict(rows)
    in_csv = {
        r["doc_type_code"]: r["field_contract"]
        for r in _registry_rows()
        if r["field_contract"]
    }
    assert in_db == in_csv


# =============================================================================
# Validation, against files the generator did not write
# =============================================================================


@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("sales_register_handwritten.csv", "SALES_REGISTER"),
        ("bill_of_entry_handwritten.csv", "BILL_OF_ENTRY"),
    ],
)
def test_handwritten_fixture_validates(fixture: str, code: str) -> None:
    """See tests/fixtures/README.md — these were NOT produced by the generator.

    The generator reads the same contract the validator enforces, so generated
    fixtures cannot detect a wrong contract. These can.
    """
    result = validate_transaction_csv(
        (FIXTURES / fixture).read_bytes(), _contract_for(code)
    )
    assert result.ok, f"{fixture} should validate: {result.rejections[:3]}"
    assert result.documents


def test_handwritten_sales_register_shape() -> None:
    """Asserted against the file's contents, not against the contract.

    A test that re-derived its expectations from the same contract the code
    reads would agree with any contract at all.
    """
    result = validate_transaction_csv(
        (FIXTURES / "sales_register_handwritten.csv").read_bytes(),
        _contract_for("SALES_REGISTER"),
    )
    by_number = {d.doc_number: d for d in result.documents}
    assert set(by_number) == {
        "SI/2026-27/00001", "SI/2026-27/00002",
        "SI/2026-27/00003", "SI/2026-27/00004",
    }

    # Two lines share one invoice number and collapse into one document.
    assert len(by_number["SI/2026-27/00001"].lines) == 2
    assert by_number["SI/2026-27/00001"].total_taxable_value == 452000
    assert by_number["SI/2026-27/00001"].total_tax_value == 81360

    # B2C: no GSTIN, and that is a FACT rather than a missing field.
    assert by_number["SI/2026-27/00003"].counterparty_gstin is None

    # Cess is part of the tax split. Before migration 007 there was no column
    # for it and this 3170.00 would have vanished from the total.
    assert by_number["SI/2026-27/00004"].total_tax_value == 1680 + 3170

    # Contract attributes land in `attributes`, not in columns.
    assert by_number["SI/2026-27/00002"].attributes["place_of_supply"] == "29"


def test_handwritten_bill_of_entry_has_no_gstin() -> None:
    result = validate_transaction_csv(
        (FIXTURES / "bill_of_entry_handwritten.csv").read_bytes(),
        _contract_for("BILL_OF_ENTRY"),
    )
    assert result.ok
    for doc in result.documents:
        assert doc.counterparty_gstin is None
        assert doc.counterparty_name
        assert doc.attributes["port_code"] in {"INNSA1", "INMAA4"}


@pytest.mark.parametrize(
    ("fixture", "code", "fragment"),
    [
        (
            "purchase_register_no_gstin_rejected.csv",
            "PURCHASE_REGISTER",
            "counterparty_gstin is mandatory",
        ),
        (
            "bill_of_entry_gstin_supplied_rejected.csv",
            "BILL_OF_ENTRY",
            "overseas counterparty",
        ),
        (
            "sales_register_both_tax_heads_rejected.csv",
            "SALES_REGISTER",
            "cannot be intra-state and inter-state",
        ),
        (
            "bill_of_entry_missing_port_code_rejected.csv",
            "BILL_OF_ENTRY",
            "missing mandatory columns: port_code",
        ),
    ],
)
def test_negative_fixture_is_rejected_for_the_right_reason(
    fixture: str, code: str, fragment: str
) -> None:
    """Not merely that it failed — WHY it failed.

    A validator that rejected everything would pass a suite that only asserted
    `not result.ok`.
    """
    result = validate_transaction_csv(
        (FIXTURES / fixture).read_bytes(), _contract_for(code)
    )
    assert not result.ok, f"{fixture} should have been rejected"
    assert any(fragment in r for r in result.rejections), result.rejections


def test_counterparty_required_and_foreign_are_opposite_rules() -> None:
    """The same file, two contracts, two verdicts — and neither is 'accepted'.

    This is what makes counterparty a per-type contract rather than a global
    rule: demanding a GSTIN would reject every import, and never demanding one
    would let a purchase register lose its supplier.
    """
    with_gstin = (FIXTURES / "bill_of_entry_gstin_supplied_rejected.csv").read_bytes()
    assert not validate_transaction_csv(with_gstin, _contract_for("BILL_OF_ENTRY")).ok

    without_gstin = (FIXTURES / "purchase_register_no_gstin_rejected.csv").read_bytes()
    assert not validate_transaction_csv(
        without_gstin, _contract_for("PURCHASE_REGISTER")
    ).ok


# =============================================================================
# The generator, over every type
# =============================================================================


def _generate(code: str, *, docs: int = 3, lines: int = 2) -> bytes:
    out = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "gen_mock_erp.py"),
         code, "--docs", str(docs), "--lines", str(lines)],
        capture_output=True, check=True, cwd=ROOT,
    )
    return out.stdout


@pytest.mark.parametrize("code", _archetype_1_codes())
def test_generated_export_validates_for_every_type(code: str) -> None:
    """Every archetype-1 type, one code path, no per-type branch anywhere.

    Weak on its own — see the hand-written fixtures for why. Its value is
    coverage: a contract that cannot produce a valid file at all is caught here
    for all eleven types, including the eight nobody has hand-written yet.
    """
    result = validate_transaction_csv(_generate(code), _contract_for(code))
    assert result.ok, f"{code}: {result.rejections[:3]}"
    assert len(result.documents) == 3
    assert result.row_count == 6


def test_generator_is_deterministic() -> None:
    assert _generate("SALES_REGISTER") == _generate("SALES_REGISTER")


def test_generated_optional_counterparty_exercises_both_branches() -> None:
    """A fixture that always supplied a GSTIN would never reach the B2C path."""
    result = validate_transaction_csv(
        _generate("SALES_REGISTER", docs=8), _contract_for("SALES_REGISTER")
    )
    gstins = [d.counterparty_gstin for d in result.documents]
    assert any(g is None for g in gstins), "no B2C document generated"
    assert any(g is not None for g in gstins), "no B2B document generated"


# =============================================================================
# Promotion — the archetype claim, end to end
# =============================================================================


async def _promote(
    pool: TenantScopedPool,
    tenant: SeededTenant,
    code: str,
    data: bytes,
    *,
    entity_id: uuid.UUID | None = None,
):
    """Promote under a FRESH entity by default.

    The natural key is (entity_id, doc_type, counterparty, doc_number), and the
    generator is deterministic — so reusing the tenant's seeded entity would
    make the second test in this module collide with the first. Reusing one
    entity across unrelated tests would also mean tests passing or failing on
    execution order, which is the thing tests/handoff already had to work around.
    """
    return await SilverPromotionService(pool).promote_transaction_documents(
        tenant.ctx,
        document_type=code,
        ingest_id=uuid.uuid4(),
        entity_id=entity_id or uuid.uuid4(),
        data=data,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )


@pytest.mark.parametrize(
    "code", ["SALES_REGISTER", "BILL_OF_ENTRY", "ICEGATE_SHIPPING_BILL"]
)
async def test_a_new_document_type_needs_no_new_code(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, admin: psycopg.Connection,
    code: str,
) -> None:
    """THE archetype test.

    Three document types the promotion path has never been specialised for —
    one outward domestic, one inward import, one outward export — promoted by
    the same method with nothing but a registry code differing. If this ever
    needs a branch, ARCHITECTURE.md 6 says stop and fix the archetype rather
    than absorb the cost a hundred more times.
    """
    manifest = await _promote(app_pool, tenant_a, code, _generate(code))
    assert manifest.document_type == code

    row = admin.execute(
        sql.SQL(
            "SELECT count(*), count(DISTINCT direction)"
            "  FROM {}.transaction_document WHERE batch_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (manifest.batch_id,),
    ).fetchone()
    assert row == (3, 1)

    lines = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.transaction_line l"
            "  JOIN {}.transaction_document d ON d.doc_id = l.doc_id"
            " WHERE d.batch_id = %s"
        ).format(
            sql.Identifier(tenant_a.ctx.silver_schema),
            sql.Identifier(tenant_a.ctx.silver_schema),
        ),
        (manifest.batch_id,),
    ).fetchone()
    assert lines[0] == manifest.row_count == 6


async def test_every_archetype1_promote_type_promotes_end_to_end(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, admin: psycopg.Connection,
) -> None:
    """Every in-scope ARCHETYPE1_PROMOTE type, not a 3-type sample.

    `test_a_new_document_type_needs_no_new_code` above proves the archetype
    claim with three representative types; this proves the FULL in-scope set
    writes to Silver, which is a narrower, stronger claim that
    `test_generated_export_validates_for_every_type` cannot make — that one
    stops at `validate_transaction_csv`, never reaching the database. The gap
    between the two is exactly where the ERP upload E2E suite (2026-09-01)
    found `document_schema.py` publishing a schema a conforming upload could
    not actually satisfy (shared migration 046): validation against the
    CONTRACT passed, but nobody had proven a promotion against the DATABASE
    for anything but three hand-picked types. Read from the registry, not
    hardcoded, so a twelfth ARCHETYPE1_PROMOTE type is covered automatically.
    """
    codes = [
        str(r[0]) for r in admin.execute(
            "SELECT doc_type_code FROM platform_ref.document_type"
            " WHERE dispatch_mechanism = 'ARCHETYPE1_PROMOTE' AND in_scope"
            " ORDER BY doc_type_code"
        ).fetchall()
    ]
    assert codes, "no in-scope ARCHETYPE1_PROMOTE type found — test is vacuous"

    for code in codes:
        manifest = await _promote(app_pool, tenant_a, code, _generate(code))
        assert manifest.document_type == code
        assert manifest.row_count == 6, f"{code}: expected 6 lines, got {manifest.row_count}"


async def test_direction_and_stream_come_from_the_registry(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, admin: psycopg.Connection
) -> None:
    """Neither is passed in by the caller, so neither can be passed in wrong."""
    manifest = await _promote(
        app_pool, tenant_a, "BILL_OF_ENTRY", _generate("BILL_OF_ENTRY")
    )
    # Stream B: the client hands us their own copy of the Bill of Entry.
    assert manifest.source_stream == "B"

    directions = admin.execute(
        sql.SQL(
            "SELECT DISTINCT direction FROM {}.transaction_document WHERE batch_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (manifest.batch_id,),
    ).fetchall()
    assert directions == [("INWARD",)]

    outward = await _promote(
        app_pool, tenant_a, "SALES_REGISTER", _generate("SALES_REGISTER")
    )
    directions = admin.execute(
        sql.SQL(
            "SELECT DISTINCT direction FROM {}.transaction_document WHERE batch_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (outward.batch_id,),
    ).fetchall()
    assert directions == [("OUTWARD",)]


async def test_resubmitting_the_same_document_supersedes_not_conflicts(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, admin: psycopg.Connection
) -> None:
    """The `GTA_INVOICE_CONSIGNMENT_NOTE` finding (2026-09-01), reproduced at
    the archetype-1 promotion layer rather than through the PDF extractor —
    the bug lives in `promote_transaction_documents`, which BOTH the CSV
    (ARCHETYPE1_PROMOTE) and PDF (PDF_EXTRACTION's `TransactionDocumentExtractor`
    subclasses) paths call, so it is provable here without a real PDF.

    `promote_transaction_documents` always did a blind INSERT — no lookup
    against `transaction_document_natural_key_uq` at all — so re-promoting a
    document with the SAME (entity, type, counterparty, doc_number) had to
    violate that index. Never observed via the CSV path because nothing in
    this repo's fixtures re-uploads the same invoice number twice; only
    surfaced once shared migration 043 made the PDF path reachable and a real
    PDF specimen got triggered twice.

    Same entity BOTH times, and the SAME generated bytes — `_generate` is
    deterministic (`test_generator_is_deterministic`), so the second call
    produces the identical `doc_number` on purpose, which is the whole point.
    """
    entity_id = uuid.uuid4()
    data = _generate("BILL_OF_ENTRY", docs=1, lines=1)

    first = await _promote(app_pool, tenant_a, "BILL_OF_ENTRY", data, entity_id=entity_id)
    second = await _promote(app_pool, tenant_a, "BILL_OF_ENTRY", data, entity_id=entity_id)
    assert second.batch_id != first.batch_id, (
        "a re-promotion is its own batch — reusing the first batch_id would "
        "make the two readings indistinguishable in the manifest"
    )

    rows = admin.execute(
        sql.SQL(
            "SELECT doc_id, supersedes_doc_id, superseded_at IS NULL"
            "  FROM {}.transaction_document WHERE entity_id = %s"
            " ORDER BY recorded_at"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (entity_id,),
    ).fetchall()
    assert len(rows) == 2, "each promotion must append a row, not overwrite one"
    assert [r[2] for r in rows] == [False, True], "exactly one row is current"
    assert rows[0][1] is None, "the first reading supersedes nothing"
    assert rows[1][1] == rows[0][0]


async def test_contract_attributes_land_in_jsonb(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, admin: psycopg.Connection
) -> None:
    """A registry-declared attribute must be queryable without a schema change."""
    manifest = await _promote(
        app_pool, tenant_a, "BILL_OF_ENTRY", _generate("BILL_OF_ENTRY")
    )
    rows = admin.execute(
        sql.SQL(
            "SELECT attributes ->> 'be_number', attributes ->> 'port_code'"
            "  FROM {}.transaction_document WHERE batch_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (manifest.batch_id,),
    ).fetchall()
    assert len(rows) == 3
    for be_number, port_code in rows:
        assert be_number and port_code


async def test_wrong_archetype_is_refused(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """LUT is archetype 3. It has a home, and it is not this table."""
    with pytest.raises(ValidationRejected, match="archetype 3, not 1"):
        await _promote(app_pool, tenant_a, "LUT", b"doc_number\nX\n")


async def test_unknown_document_type_is_refused(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    with pytest.raises(ValidationRejected, match="not an in-scope document type"):
        await _promote(app_pool, tenant_a, "NOT_A_REAL_TYPE", b"doc_number\nX\n")


async def test_retired_row_type_is_refused(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """PURCHASE_INVOICE is a row type, retired by shared migration 008.

    It still exists in universal_master — Bronze is insert-only and historical
    artefact_ledger rows reference it — so the guard cannot be a foreign key.
    This is that guard.
    """
    with pytest.raises(ValidationRejected, match="not an in-scope document type"):
        await _promote(app_pool, tenant_a, "PURCHASE_INVOICE", b"doc_number\nX\n")


async def test_purchase_register_no_longer_promotes_to_transaction_document(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """TYPED-TABLES-PLAN.md 10 step 4 (tenant 011, this module's docstring).

    PURCHASE_REGISTER has a typed table (purchase_register, tenant 010) and a
    loader (src/silver/registers) now, so this path must refuse it rather than
    write a second, orphaned copy into transaction_document. Unlike
    test_wrong_archetype_is_refused, the registry still says archetype 1 for
    this type — document_type_scope_ck (shared 003) requires that whenever
    in_scope — so the guard is the explicit
    `promote.py._RETIRED_ARCHETYPE_1_TYPES` set, not the archetype check. This
    is the test that would fail if someone "cleaned up" that set away.
    """
    with pytest.raises(ValidationRejected, match="no longer promotes to transaction_document"):
        await _promote(app_pool, tenant_a, "PURCHASE_REGISTER", _generate("PURCHASE_REGISTER"))


# =============================================================================
# The v1_ contract survived the generalisation
# =============================================================================


async def test_v1_purchase_invoice_still_answers_the_v2_contract(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, admin: psycopg.Connection
) -> None:
    """Tenant migration 011 moved the base table out from under this view a
    second time — off transaction_document and onto purchase_register.

    inafinplatform/v2 reads v1_purchase_invoice and must not be able to tell.
    The column list is asserted explicitly because CREATE OR REPLACE VIEW would
    have refused a changed one — this is the check that it was not dropped and
    recreated with a different shape instead.
    """
    columns = admin.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = 'v1_purchase_invoice'"
        " ORDER BY ordinal_position",
        (tenant_a.ctx.silver_schema,),
    ).fetchall()
    assert [c[0] for c in columns] == [
        "invoice_id", "batch_id", "entity_id", "invoice_number", "supplier_gstin",
        "invoice_date", "total_taxable_value", "total_tax_value", "total_value",
        "payment_due_date", "currency", "valid_from", "valid_to", "recorded_at",
        "superseded_at", "bronze_ingest_id",
    ]


async def test_v1_purchase_invoice_line_has_no_successor(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, admin: psycopg.Connection
) -> None:
    """Dropped, not redefined — see tenant migration 011's header for why.

    purchase_register is invoice-level in the client's own reference schema
    (reference/inafin_a1_schema.sql, A1.03): no line_number, quantity,
    unit_price or description ever existed to redefine this view over. A
    reappearance here — even an empty one — would silently promise v2 line
    detail that does not exist.
    """
    exists = admin.execute(
        "SELECT count(*) FROM information_schema.views"
        " WHERE table_schema = %s AND table_name = 'v1_purchase_invoice_line'",
        (tenant_a.ctx.silver_schema,),
    ).fetchone()
    assert exists is not None and exists[0] == 0


async def test_purchase_register_doc_type_code_is_pinned(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, admin: psycopg.Connection
) -> None:
    """The old view filtered `WHERE doc_type <> 'PURCHASE_REGISTER'`; that
    predicate has no equivalent now because purchase_register is a dedicated
    table, not a shared one. This is the check that the guarantee the old
    WHERE clause gave is still real: the CHECK constraint (tenant 010), not an
    application-level filter, is what stops a mis-typed row appearing in
    v1_purchase_invoice.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            sql.SQL(
                """
                INSERT INTO {}.purchase_register (
                    entity_id, gstin, tax_period, doc_type_code, batch_id,
                    bronze_ingest_id, row_hash, invoice_no, invoice_date,
                    taxable_value, gl_code, cost_centre, valid_from
                ) VALUES (
                    %s, '27AAAPA1234A1Z5', DATE '2026-04-01', 'SALES_REGISTER',
                    %s, %s, 'wrong-type-probe', 'INV-WRONG-TYPE', DATE '2026-04-15',
                    1000.00, 'GL-X', 'CC-X', DATE '2026-04-15'
                )
                """
            ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
            (tenant_a.entity_id, tenant_a.batch_id, tenant_a.ingest_id),
        )


# =============================================================================
# Isolation, on the new tables
# =============================================================================


async def test_recon_cannot_read_transaction_base_tables(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> None:
    """New tables inherit the matrix — recon reaches v1_ views and nothing else.

    Grants are discovered by app.apply_tenant_grants at migrate time, so this is
    really asserting that a table added in migration 007 did not quietly arrive
    without the boundary being re-asserted over it.
    """
    from src.core.tenant import Role

    for table in ("transaction_document", "transaction_line"):
        # TenantBoundaryViolation, not a bare permission error: the pool
        # deliberately re-raises so an alert rule can tell "stopped at the
        # boundary" apart from "the query found nothing".
        with pytest.raises(TenantBoundaryViolation):
            async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
                await conn.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier(tenant_a.ctx.silver_schema),
                        sql.Identifier(table),
                    )
                )


async def test_transaction_documents_do_not_cross_tenants(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    admin: psycopg.Connection,
) -> None:
    """Colliding document numbers in both tenants; neither may see the other's."""
    data = _generate("SALES_REGISTER")
    a = await _promote(app_pool, tenant_a, "SALES_REGISTER", data)
    b = await _promote(app_pool, tenant_b, "SALES_REGISTER", data)

    # Positive control: the rows genuinely exist in both, with the same numbers.
    numbers = {}
    for tenant, batch in ((tenant_a, a), (tenant_b, b)):
        rows = admin.execute(
            sql.SQL(
                "SELECT doc_number FROM {}.transaction_document"
                " WHERE batch_id = %s ORDER BY doc_number"
            ).format(sql.Identifier(tenant.ctx.silver_schema)),
            (batch.batch_id,),
        ).fetchall()
        numbers[tenant.slug] = [r[0] for r in rows]
    assert numbers[tenant_a.slug] == numbers[tenant_b.slug]
    assert numbers[tenant_a.slug]

    # acme's role, pointed at globex's schema.
    from src.core.tenant import Role

    with pytest.raises(TenantBoundaryViolation):
        async with app_pool.transaction(tenant_a.ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.transaction_document").format(
                    sql.Identifier(tenant_b.ctx.silver_schema)
                )
            )


# =============================================================================
# Cleansing
# =============================================================================


def test_gstin_is_cleansed_before_validation() -> None:
    """Lower case and embedded spaces are formatting, not a validation failure.

    ERP exports carry both routinely. Rejecting them would quarantine files that
    are substantively fine, and a quarantine nobody trusts gets bypassed.
    """
    header = (
        "doc_number,counterparty_gstin,doc_date,payment_due_date,line_number,"
        "hsn_sac,quantity,unit_price,taxable_value,gst_rate,igst_amount\n"
    )
    row = (
        "PI/1,27 aaacr5055k1z5,2026-04-03,2026-07-02,1,"
        "84713010,10,100.0000,1000.00,18.00,180.00\n"
    )
    result = validate_transaction_csv(
        (header + row).encode(), _contract_for("PURCHASE_REGISTER")
    )
    assert result.ok, result.rejections
    assert result.documents[0].counterparty_gstin == "27AAACR5055K1Z5"


def test_unparseable_attribute_is_an_error_not_a_silent_drop() -> None:
    """A vanished be_date would leave a Bill of Entry that reconciles to nothing."""
    text = (FIXTURES / "bill_of_entry_handwritten.csv").read_text()
    broken = text.replace("2026-04-08,INNSA1", "not-a-date,INNSA1")
    assert broken != text

    result = validate_transaction_csv(broken.encode(), _contract_for("BILL_OF_ENTRY"))
    assert not result.ok
    assert any("be_date" in r for r in result.rejections), result.rejections


def test_empty_file_is_rejected() -> None:
    reader = io.StringIO()
    writer = csv.writer(reader)
    writer.writerow(["doc_number", "doc_date", "line_number", "hsn_sac",
                     "quantity", "unit_price", "taxable_value", "gst_rate",
                     "place_of_supply"])
    result = validate_transaction_csv(
        reader.getvalue().encode(), _contract_for("SALES_REGISTER")
    )
    assert not result.ok
    assert any("no data rows" in r for r in result.rejections)
