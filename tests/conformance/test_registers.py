"""The 23 flat A1 register loaders, end to end.

`test_register_specs.py` proves each spec describes its table. This file proves
the loader built on those specs does the right thing with a file: parses it,
rejects what it should, and upserts idempotently under both key strategies.

The synthetic-CSV tests below are generated FROM the spec, which is exactly the
circularity `tests/fixtures/README.md` warns about — a wrong spec would produce a
file that loads perfectly. What breaks the circle is that the row still has to
survive Postgres: the domains, the CHECK vocabularies and the NOT NULLs are the
independent judge, and none of them were written by this test. That is why the
smoke test asserts a row LANDED rather than merely that no exception was raised.

The hand-written fixtures carry what generation cannot: an unregistered supplier
with no GSTIN, two genuinely identical bank rows, a trial balance with real GL
codes. Do not regenerate one to make a test pass.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import pathlib
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.silver.registers import (
    SPECS,
    KeyKind,
    RegisterLoader,
    RegisterSpec,
    financial_year,
    parse_register_csv,
    parse_register_ndjson,
    spec_for,
)

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"

PURCHASE_HANDWRITTEN = FIXTURES / "purchase_register_typed_handwritten.csv"
PURCHASE_BAD_TYPES = FIXTURES / "purchase_register_bad_types_rejected.csv"
BANK_HANDWRITTEN = FIXTURES / "bank_statement_outward_handwritten.csv"
TRIAL_BALANCE_HANDWRITTEN = FIXTURES / "trial_balance_handwritten.csv"

PERIOD_START = dt.date(2026, 4, 1)
PERIOD_END = dt.date(2026, 4, 30)
GSTIN = "27AAFCI9876P1ZQ"
SPEC_IDS = [s.table for s in SPECS]

# Values for columns a CHECK constrains to a vocabulary. Postgres, not this
# dict, is what enforces them — these exist so the synthetic row is ACCEPTED and
# the smoke test therefore exercises the insert rather than the rejection path.
VOCABULARY: dict[tuple[str, str], str] = {
    ("credit_debit_note_register", "note_type"): "CN",
    ("purchase_register", "itc_eligibility"): "ELIGIBLE",
    ("advance_receipt_register", "supply_type"): "GOODS",
    ("audited_pl_account", "account_type"): "REVENUE",
    ("audited_pl_account", "dr_cr"): "CR",
    ("trial_balance", "dr_cr"): "DR",
    ("balance_sheet", "category"): "OTHER",
    ("fixed_asset_register", "disposal_type"): "SALE",
    ("inter_gstin_transaction_register", "txn_type"): "STOCK_TRANSFER",
    ("platform_settlement_report", "report_type"): "MTR",
    ("itc_reversal_register", "reversal_type"): "RULE_42",
    ("common_input_service_invoice", "service_category"): "RENT",
}


def _cell(spec: RegisterSpec, column_name: str, kind: str, seed: str) -> str:
    if (fixed := VOCABULARY.get((spec.table, column_name))) is not None:
        return fixed
    if column_name == "currency":
        return "USD"
    if column_name.endswith("gstin"):
        return GSTIN
    match kind:
        case "date":
            return "2026-04-15"
        case "decimal":
            return "18.00"
        case "integer":
            return "36"
        case "boolean":
            return "false"
        case _:
            return f"{column_name}-{seed}"


def synthetic_csv(
    spec: RegisterSpec, *, seed: str = "A", overrides: dict[str, str] | None = None
) -> bytes:
    """One valid row for `spec`, in spec column order.

    Built column by column rather than by patching bytes: several columns share
    a value ('18.00' is every decimal), so a byte replacement meant to change one
    field would silently change four.
    """
    overrides = overrides or {}
    header = ",".join(spec.column_names)
    row = ",".join(
        overrides.get(c.name, _cell(spec, c.name, c.kind.value, seed)) for c in spec.columns
    )
    return f"{header}\n{row}\n".encode()


def a_correction_to(spec: RegisterSpec) -> tuple[str, str] | None:
    """A (column, new value) that changes the row's CONTENT but not its IDENTITY.

    Must avoid the key columns — changing one of those makes a different row, not
    a corrected one — and the valid_from column, which would move the validity
    interval as well. Text is preferred because the assertion reads better;
    decimal is the fallback for `common_input_service_invoice`, whose every
    non-key column is a number.
    """
    def usable(name: str) -> bool:
        return (
            name not in spec.key_columns
            and name != spec.valid_from_column
            and (spec.table, name) not in VOCABULARY
            and name != "currency"
            and not name.endswith("gstin")
        )

    for column in spec.columns:
        if column.kind.value == "text" and usable(column.name):
            return column.name, f"{column.name}-CORRECTED"
    for column in spec.columns:
        if column.kind.value == "decimal" and usable(column.name):
            return column.name, "19.00"
    return None


async def _load(
    pool: TenantScopedPool,
    tenant: SeededTenant,
    spec: RegisterSpec,
    data: bytes,
    *,
    entity_id: uuid.UUID,
    period_start: dt.date = PERIOD_START,
    period_end: dt.date = PERIOD_END,
    doc_type_code: str | None = None,
):
    return await RegisterLoader(pool, spec).load(
        tenant.ctx,
        entity_id=entity_id,
        gstin=GSTIN,
        ingest_id=uuid.uuid4(),
        data=data,
        period_start=period_start,
        period_end=period_end,
        doc_type_code=doc_type_code,
    )


def _live(
    admin: psycopg.Connection[tuple[object, ...]],
    tenant: SeededTenant,
    spec: RegisterSpec,
    entity_id: uuid.UUID,
    *columns: str,
) -> list[tuple[object, ...]]:
    selected = ("id", "row_hash", *columns)
    return admin.execute(
        sql.SQL("SELECT {} FROM {}.{} WHERE entity_id = %s AND superseded_at IS NULL").format(
            sql.SQL(", ").join(sql.Identifier(c) for c in selected),
            sql.Identifier(tenant.ctx.silver_schema),
            sql.Identifier(spec.table),
        ),
        (entity_id,),
    ).fetchall()


@pytest.fixture
def entity() -> uuid.UUID:
    """A fresh business entity per test.

    Every key starts with entity_id, so a fresh entity gives each test its own
    key space without deleting rows between tests — which would also hide a
    supersession bug.
    """
    return uuid.uuid4()


# =============================================================================
# Parsing — no database
# =============================================================================


def test_the_handwritten_purchase_register_parses() -> None:
    """Five rows, and the fourth has no supplier GSTIN.

    An unregistered supplier is legitimate — it is why the row carries
    rcm_flag=true — and its GSTIN's ABSENCE IS A FACT. A parser that required one
    would drop every RCM purchase, which are precisely the rows a Rule 42 review
    is about.
    """
    result = parse_register_csv(spec_for("PURCHASE_REGISTER"), PURCHASE_HANDWRITTEN.read_bytes())
    assert result.ok, result.row_rejections
    assert result.row_count == 5

    unregistered = result.rows[3]
    assert unregistered.values["supplier_gstin"] is None
    assert unregistered.values["rcm_flag"] is True
    assert unregistered.values["taxable_value"] == decimal.Decimal("18500.00")

    # A TDS deductor's GSTIN carries 'D' at position 14, not 'Z'. It parses here
    # and the platform_ref.gstin domain accepts it; a hand-rolled regex in this
    # layer is what would drop it.
    assert result.rows[2].values["supplier_gstin"] == "27AAACR5055K1D5"


def test_every_malformed_row_is_reported_once(
    ) -> None:
    """Four bad rows, four distinct reasons, all in one pass — and the file
    still parses (tenant migration 012: row-level, not file-level rejection).

    Rejections accumulate rather than raising on the first: a file with fourteen
    bad rows should be reported once, not fourteen times over fourteen
    re-ingests. The reasons are asserted, not just the count — a validator that
    rejects everything for the wrong reason passes a count assertion.
    """
    result = parse_register_csv(spec_for("PURCHASE_REGISTER"), PURCHASE_BAD_TYPES.read_bytes())
    assert result.ok, result.fatal  # the FILE parses; all four ROWS are bad
    assert result.rows == []
    assert len(result.row_rejections) == 4

    reasons = " | ".join(
        f"row {r.source_line}: {r.column}: {r.message}" for r in result.row_rejections
    )
    assert "row 2: invoice_date" in reasons and "not an ISO date" in reasons
    assert "row 3: taxable_value" in reasons and "not a number" in reasons
    assert "row 4: invoice_no: empty required field(s): invoice_no" in reasons
    assert "row 5: rcm_flag" in reasons and "not a boolean" in reasons

    # The column is structured, not just embedded in the message string — a
    # customer report groups by this without re-parsing anything.
    columns = {r.source_line: r.column for r in result.row_rejections}
    assert columns == {
        2: "invoice_date", 3: "taxable_value", 4: "invoice_no", 5: "rcm_flag",
    }


def test_a_missing_column_is_rejected_before_any_row_is_read() -> None:
    """A shifted export is a file-level (FATAL) fault, not a per-row one.

    Reporting it per row would bury the one actionable fact — a column is gone —
    under N copies of its consequence. Unlike a bad cell, there is no batch to
    build from a file with no cost_centre column at all: every row would be
    missing the same required field, so ParseResult.fatal short-circuits before
    any row is read.
    """
    spec = spec_for("PURCHASE_REGISTER")
    mangled = synthetic_csv(spec).replace(b"cost_centre,", b"costcentre,")
    result = parse_register_csv(spec, mangled)
    assert not result.ok
    assert result.fatal == "missing required column(s): cost_centre"
    assert result.rows == []
    assert result.row_rejections == []


# =============================================================================
# NDJSON — the same per-row rule, a different framing
# =============================================================================


def _synthetic_record(spec: RegisterSpec, *, seed: str = "A", **overrides: object) -> dict:
    record = {c.name: _cell(spec, c.name, c.kind.value, seed) for c in spec.columns}
    record.update(overrides)
    return record


def test_ndjson_and_csv_agree_on_an_identical_row() -> None:
    """Two framings, one rule. `_parse_row` is not reimplemented per format.

    Same cell text in both — this is not about JSON's native types (that's
    the next test) — so the row_hash and coerced values must be identical.
    """
    spec = spec_for("PURCHASE_REGISTER")
    csv_result = parse_register_csv(spec, synthetic_csv(spec))

    record = _synthetic_record(spec)
    ndjson_result = parse_register_ndjson(
        spec, (json.dumps(record) + "\n").encode()
    )

    assert csv_result.ok and ndjson_result.ok
    assert csv_result.rows[0].row_hash == ndjson_result.rows[0].row_hash
    assert csv_result.rows[0].values == ndjson_result.rows[0].values


def test_ndjson_accepts_native_json_types() -> None:
    """A number in the file is a JSON number, not a quoted string — must coerce.

    This is the reason `_parse_row` never sees a JSON value directly: every
    cell is stringified first, so `18.00` (JSON string) and `18.0` (JSON
    number) both reach the same Decimal-parsing rule CSV cells go through.
    """
    spec = spec_for("PURCHASE_REGISTER")
    record = _synthetic_record(
        spec, taxable_value=240000.5, gst_rate=18, rcm_flag=False,
    )
    result = parse_register_ndjson(spec, (json.dumps(record) + "\n").encode())
    assert result.ok, result.row_rejections
    assert result.rows[0].values["taxable_value"] == decimal.Decimal("240000.5")
    assert result.rows[0].values["rcm_flag"] is False


def test_ndjson_malformed_line_is_a_row_rejection_not_a_file_failure() -> None:
    """One bad line must not sink the file — same guarantee as a bad CSV row."""
    spec = spec_for("PURCHASE_REGISTER")
    good = json.dumps(_synthetic_record(spec, invoice_no="PI-GOOD-1"))
    lines = f"{good}\nnot json at all\n[1, 2, 3]\n".encode()

    result = parse_register_ndjson(spec, lines)
    assert result.ok, result.fatal
    assert len(result.rows) == 1
    assert len(result.row_rejections) == 2

    by_line = {r.source_line: r for r in result.row_rejections}
    assert "not valid JSON" in by_line[2].message
    assert by_line[2].column is None
    assert by_line[3].message == "each line must be a JSON object"
    assert by_line[3].column is None


def test_ndjson_missing_column_is_fatal_on_the_first_record() -> None:
    spec = spec_for("PURCHASE_REGISTER")
    record = _synthetic_record(spec)
    del record["cost_centre"]
    result = parse_register_ndjson(spec, (json.dumps(record) + "\n").encode())
    assert not result.ok
    assert result.fatal == "missing required column(s): cost_centre"


@pytest.mark.asyncio
async def test_ndjson_loads_through_the_loader_same_as_csv(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    entity: uuid.UUID,
) -> None:
    """content_format="ndjson" is a real, wired path through RegisterLoader.load,
    not just a standalone parser nobody calls."""
    spec = spec_for("PURCHASE_REGISTER")
    record = _synthetic_record(spec, invoice_no="PI-NDJSON-1")
    data = (json.dumps(record) + "\n").encode()

    outcome = await RegisterLoader(app_pool, spec).load(
        tenant_a.ctx, entity_id=entity, gstin=GSTIN, ingest_id=uuid.uuid4(),
        data=data, period_start=PERIOD_START, period_end=PERIOD_END,
        content_format="ndjson",
    )
    assert outcome.inserted == 1
    assert outcome.rejected == 0
    assert [row[2] for row in _live(admin, tenant_a, spec, entity, "invoice_no")] == [
        "PI-NDJSON-1"
    ]


def test_identical_rows_in_one_file_are_collapsed_and_counted() -> None:
    """The bank statement has two identical ECS debits on 2026-04-06.

    A content-keyed table cannot tell two real payments of the same amount on the
    same day from an export bug — the unique index rejects the second either way.
    So one is kept and the drop is COUNTED, rather than failing the file or
    losing the row in silence. That count is the only signal anyone downstream
    will ever get, which is why it is on the outcome and not just in a log line.
    """
    result = parse_register_csv(
        spec_for("BANK_STATEMENT_OUTWARD"), BANK_HANDWRITTEN.read_bytes()
    )
    assert result.ok, result.row_rejections
    assert result.row_count == 4
    assert result.collapsed_duplicates == 1


def test_whitespace_does_not_change_a_row_hash() -> None:
    """Normalise once, hash the normalised strings.

    An ERP that pads a column on Tuesday would otherwise re-insert its entire
    register, and every row would supersede an identical predecessor.
    """
    spec = spec_for("TRIAL_BALANCE")
    clean = TRIAL_BALANCE_HANDWRITTEN.read_bytes()
    padded = clean.replace(b"1100,Cash", b"  1100  ,  Cash")
    assert (
        parse_register_csv(spec, clean).rows[0].row_hash
        == parse_register_csv(spec, padded).rows[0].row_hash
    )


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (dt.date(2026, 4, 1), "2026-27"),
        (dt.date(2027, 3, 31), "2026-27"),
        (dt.date(2026, 3, 31), "2025-26"),
        (dt.date(2099, 4, 1), "2099-00"),
    ],
)
def test_financial_year_runs_april_to_march(day: dt.date, expected: str) -> None:
    """Three A1 registers are keyed on it, and it is not the calendar year."""
    assert financial_year(day) == expected


# =============================================================================
# Every register accepts a row
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
async def test_a_row_lands_in_every_register(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    spec: RegisterSpec,
    entity: uuid.UUID,
) -> None:
    """23 tables that had no way to put a row in them now have one.

    The assertion is that the row is READABLE afterwards, not that `load`
    returned. A synthetic row still has to pass the gstin domain, the tax_rate
    bounds, every CHECK vocabulary and every NOT NULL — none of which this test
    wrote — so landing is real evidence and a clean return would not be.
    """
    code = spec.doc_type_codes[0]
    outcome = await _load(
        app_pool, tenant_a, spec, synthetic_csv(spec), entity_id=entity, doc_type_code=code
    )
    assert outcome.inserted == 1
    assert outcome.row_count == 1

    rows = _live(admin, tenant_a, spec, entity, "batch_id", "doc_type_code", "bronze_ingest_id")
    assert len(rows) == 1
    _id, _hash, batch_id, doc_type_code, bronze_ingest_id = rows[0]
    assert doc_type_code == code
    # Lineage. Every Silver row names the batch that produced it and the Bronze
    # artefact it derives from; proving that chain resolves is a Phase 1 exit
    # criterion, so it is asserted per type rather than once.
    assert batch_id == outcome.batch_id
    assert bronze_ingest_id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
async def test_reloading_an_unchanged_file_is_a_no_op(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    spec: RegisterSpec,
    entity: uuid.UUID,
) -> None:
    """TYPED-TABLES-PLAN.md 5, for all 23 at once.

    A weekly full-file re-export must no-op on every row it has already seen.
    `inserted=0, superseded=0, unchanged=1` is the observable proof, and one live
    row afterwards is the proof that the no-op was real rather than a second row
    that happens not to be counted.
    """
    data = synthetic_csv(spec)
    code = spec.doc_type_codes[0]
    await _load(app_pool, tenant_a, spec, data, entity_id=entity, doc_type_code=code)
    again = await _load(app_pool, tenant_a, spec, data, entity_id=entity, doc_type_code=code)

    assert (again.inserted, again.superseded, again.unchanged) == (0, 0, 1)
    assert len(_live(admin, tenant_a, spec, entity)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
async def test_a_changed_row_supersedes_under_a_natural_key_and_appends_under_a_content_key(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    spec: RegisterSpec,
    entity: uuid.UUID,
) -> None:
    """The difference between the two strategies, made observable per type.

    NATURAL — the document identifies itself, so the corrected row REPLACES its
    predecessor and one live row remains.

    CONTENT — it does not, so the correction lands BESIDE the original and two
    live rows remain. That is a real limitation of these registers (tenant
    migration 010's header says so), and this test is where it is visible rather
    than merely documented. Anyone who "fixes" it by inventing a natural key for
    a bank statement will fail here, which is the point.
    """
    correction = a_correction_to(spec)
    assert correction is not None, f"{spec.table} has no correctable non-key column"
    column, new_value = correction

    code = spec.doc_type_codes[0]
    await _load(
        app_pool, tenant_a, spec, synthetic_csv(spec),
        entity_id=entity, doc_type_code=code,
    )
    outcome = await _load(
        app_pool, tenant_a, spec, synthetic_csv(spec, overrides={column: new_value}),
        entity_id=entity, doc_type_code=code,
    )

    original_value = _cell(spec, column, spec.column(column).kind.value, "A")  # type: ignore[union-attr]
    live = [str(r[2]) for r in _live(admin, tenant_a, spec, entity, column)]
    if spec.key_kind is KeyKind.NATURAL:
        assert (outcome.superseded, outcome.inserted) == (1, 0)
        assert live == [new_value]
    else:
        assert (outcome.superseded, outcome.inserted) == (0, 1)
        assert sorted(live) == sorted([original_value, new_value])


# =============================================================================
# The cases the parametrised sweep cannot reach
# =============================================================================


@pytest.mark.asyncio
async def test_a_supplier_with_no_gstin_supersedes_instead_of_duplicating(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    entity: uuid.UUID,
) -> None:
    """The NULL in a natural key, which `=` would never match.

    `purchase_register`'s key includes `supplier_gstin`, and an unregistered
    supplier legitimately has none. `supplier_gstin = NULL` matches nothing, so a
    lookup written with `=` would insert a duplicate on every resubmission — and
    the partial unique index would not stop it either, because it treats two
    NULLs as distinct. The loader uses IS NOT DISTINCT FROM; this is the row that
    proves it.

    The INDEX gap is still open (TODO.md): two concurrent loads can still both
    insert. The fix is NULLS NOT DISTINCT on the three affected indexes, which is
    a migration.
    """
    spec = spec_for("PURCHASE_REGISTER")
    original = PURCHASE_HANDWRITTEN.read_bytes()
    await _load(app_pool, tenant_a, spec, original, entity_id=entity)

    corrected = original.replace(b"6820,CC-LOG", b"6820,CC-FREIGHT")
    outcome = await _load(app_pool, tenant_a, spec, corrected, entity_id=entity)

    assert (outcome.inserted, outcome.superseded, outcome.unchanged) == (0, 1, 4)
    live = _live(admin, tenant_a, spec, entity, "invoice_no", "cost_centre")
    assert len(live) == 5
    assert [r[3] for r in live if r[2] == "CASH/BILL/0442"] == ["CC-FREIGHT"]


@pytest.mark.asyncio
async def test_an_annual_register_lands_under_its_financial_year(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    entity: uuid.UUID,
) -> None:
    """`fy` is derived from the batch period, never taken from the file.

    Same reason `gstin` is a parameter: a mislabelled export must not be able to
    choose which year it lands in.
    """
    spec = spec_for("TRIAL_BALANCE")
    outcome = await _load(
        app_pool, tenant_a, spec, TRIAL_BALANCE_HANDWRITTEN.read_bytes(),
        entity_id=entity, period_start=dt.date(2026, 4, 1), period_end=dt.date(2027, 3, 31),
    )
    assert outcome.inserted == 8

    rows = _live(admin, tenant_a, spec, entity, "fy", "gl_code", "closing_balance")
    assert {str(r[2]) for r in rows} == {"2026-27"}
    assert sorted(str(r[3]) for r in rows)[0] == "1100"


@pytest.mark.asyncio
async def test_a_batch_that_straddles_two_financial_years_is_refused(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, entity: uuid.UUID
) -> None:
    """Refused, not filed under the year the first day falls in.

    An annual register covering April 2026 to March 2028 is not a fact about
    either year. Assigning it to one silently would make a reconciliation wrong
    in a way no later query could detect — the row would look perfectly ordinary.
    """
    with pytest.raises(ValueError, match="spans 2026-27 and 2027-28"):
        await _load(
            app_pool, tenant_a, spec_for("TRIAL_BALANCE"),
            TRIAL_BALANCE_HANDWRITTEN.read_bytes(), entity_id=entity,
            period_start=dt.date(2026, 4, 1), period_end=dt.date(2028, 3, 31),
        )


@pytest.mark.asyncio
async def test_a_shared_table_demands_the_registry_code(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    entity: uuid.UUID,
) -> None:
    """Seven marketplace codes share platform_settlement_report.

    Shared migration 013 relaxed the unique index on `table_name` to allow it and
    recorded why nothing else qualifies. The consequence for the loader is that
    `doc_type_code` has no default here — defaulting to one of the seven would
    file every Flipkart report as an Amazon one, and the row would look valid.
    """
    spec = spec_for("FLIPKART_GTR")
    data = synthetic_csv(spec)

    with pytest.raises(ValueError, match="doc_type_code is required"):
        await _load(app_pool, tenant_a, spec, data, entity_id=entity)

    with pytest.raises(ValueError, match="does not load into platform_settlement_report"):
        await _load(
            app_pool, tenant_a, spec, data, entity_id=entity, doc_type_code="TRIAL_BALANCE"
        )

    await _load(app_pool, tenant_a, spec, data, entity_id=entity, doc_type_code="FLIPKART_GTR")
    rows = _live(admin, tenant_a, spec, entity, "doc_type_code", "report_type")
    assert [(str(r[2]), str(r[3])) for r in rows] == [("FLIPKART_GTR", "MTR")]


@pytest.mark.asyncio
async def test_a_rejected_file_writes_nothing_at_all(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    entity: uuid.UUID,
) -> None:
    """Not even the batch row — for a FATAL parse failure only.

    A missing required column (tenant migration 012 keeps this FATAL — see
    test_a_missing_column_is_rejected_before_any_row_is_read) is the case under
    test: nothing can be identified as a row at all, so validation happens
    before the transaction opens, deliberately. A manifest row for a batch whose
    rows were never written is worse than no row: Pipeline 2 would claim it,
    find nothing, and record a clean execution over an empty period.

    PURCHASE_BAD_TYPES (malformed cells, not a missing column) no longer
    belongs to this test: that file promotes its good rows now — see
    test_a_partially_bad_file_promotes_the_good_rows_and_records_the_rest below.
    """
    spec = spec_for("PURCHASE_REGISTER")
    mangled = synthetic_csv(spec).replace(b"cost_centre,", b"costcentre,")
    with pytest.raises(ValidationRejected, match="rejected as PURCHASE_REGISTER"):
        await _load(app_pool, tenant_a, spec, mangled, entity_id=entity)

    assert _live(admin, tenant_a, spec, entity) == []
    assert admin.execute(
        sql.SQL("SELECT count(*) FROM {}.ingest_batch WHERE entity_id = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (entity,),
    ).fetchone() == (0,)

    # FATAL still quarantines the artefact — mirrors src/silver/promote.py.
    quarantined = admin.execute(
        sql.SQL(
            "SELECT reason FROM {}.quarantined_artefact WHERE entity_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (entity,),
    ).fetchone()
    assert quarantined is not None
    assert quarantined[0] == "missing required column(s): cost_centre"


@pytest.mark.asyncio
async def test_a_partially_bad_file_promotes_the_good_rows_and_records_the_rest(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    entity: uuid.UUID,
) -> None:
    """Tenant migration 012's whole point: one bad row must not fail the file.

    Two rows, one good and one with an uncoercible taxable_value. The batch is
    created from the one that parsed; the one that didn't lands in
    rejected_row, FK'd to that same batch — not silently dropped, and not
    failing the row that was fine.
    """
    spec = spec_for("PURCHASE_REGISTER")
    good = synthetic_csv(spec, seed="A", overrides={"invoice_no": "PI-GOOD-1"})
    header, good_row = good.decode().strip("\n").split("\n")
    bad_row = ",".join(
        "not-a-number" if c.name == "taxable_value"
        else "PI-BAD-1" if c.name == "invoice_no"
        else _cell(spec, c.name, c.kind.value, "A")
        for c in spec.columns
    )
    data = f"{header}\n{good_row}\n{bad_row}\n".encode()

    outcome = await _load(app_pool, tenant_a, spec, data, entity_id=entity)
    assert outcome.row_count == 1
    assert outcome.inserted == 1
    assert outcome.rejected == 1

    live = _live(admin, tenant_a, spec, entity, "invoice_no")
    assert [row[2] for row in live] == ["PI-GOOD-1"]

    rejected = admin.execute(
        sql.SQL(
            "SELECT source_line, column_name, message FROM {}.rejected_row"
            " WHERE batch_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (outcome.batch_id,),
    ).fetchall()
    assert len(rejected) == 1
    source_line, column_name, message = rejected[0]
    assert source_line == 3  # header=1, good row=2, bad row=3
    assert column_name == "taxable_value"
    assert "not a number" in message

    # No quarantine — the artefact was not refused, only one of its rows was.
    quarantined = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.quarantined_artefact WHERE entity_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (entity,),
    ).fetchone()
    assert quarantined == (0,)


@pytest.mark.asyncio
async def test_a_register_loaded_for_one_tenant_is_invisible_to_the_other(
    app_pool: TenantScopedPool,
    admin: psycopg.Connection[tuple[object, ...]],
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    entity: uuid.UUID,
) -> None:
    """The same entity_id, the same GSTIN, the same bytes — in one tenant only.

    Colliding data on purpose (see tests/conftest.py): if acme's rows said 'acme'
    a leak would be obvious by inspection, and a test that merely counts rows
    would pass a broken boundary. The positive control matters as much as the
    negative one — without it a failed load makes this pass vacuously.
    """
    spec = spec_for("BANK_STATEMENT_OUTWARD")
    await _load(app_pool, tenant_a, spec, BANK_HANDWRITTEN.read_bytes(), entity_id=entity)

    assert len(_live(admin, tenant_a, spec, entity)) == 4
    assert _live(admin, tenant_b, spec, entity) == []
