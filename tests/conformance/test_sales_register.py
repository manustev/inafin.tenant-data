"""A1.01 SALES_REGISTER — the typed-table reference pattern, end to end.

TYPED-TABLES-PLAN.md build order step 3. These gates are meant to be reviewed
alongside the migration, because they are what the other 23 A1 types will copy.

Three of them carry most of the weight:

  * `test_resubmitting_an_unchanged_file_is_a_no_op` and its two siblings are
    TODO.md's "Resubmission produces duplicate batches for the same period",
    which §5 closes by moving idempotency from the batch to the row. If these
    fail, the withdrawn resolution table (superset comparison, HITL queue,
    PENDING_REVIEW) is back on the table.

  * `test_a_header_total_that_disagrees_with_its_lines_is_stored` is why header
    and line are separate tables at all (§6).

  * `test_v1_view_exposes_the_common_core_only` is what keeps the view a
    contract once tenant extension columns exist (§7).
"""

from __future__ import annotations

import datetime as dt
import pathlib
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.core.errors import TenantBoundaryViolation, ValidationRejected
from src.core.pool import TenantScopedPool
from src.core.tenant import Role
from src.silver.sales_register import (
    SalesRegisterLoader,
    parse_sales_register_csv,
)

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"

HANDWRITTEN = FIXTURES / "sales_register_typed_handwritten.csv"
TOTALS_DISAGREE = FIXTURES / "sales_register_totals_disagree_handwritten.csv"
NO_INVOICE_TOTALS = FIXTURES / "sales_register_no_invoice_totals_handwritten.csv"
HEADER_DISAGREEMENT = FIXTURES / "sales_register_header_disagreement_rejected.csv"

PERIOD_START = dt.date(2026, 4, 1)
PERIOD_END = dt.date(2026, 4, 30)
SUPPLIER_GSTIN = "27AAFCI9876P1ZQ"


async def _load(
    pool: TenantScopedPool,
    tenant: SeededTenant,
    data: bytes,
    *,
    entity_id: uuid.UUID,
    period_start: dt.date = PERIOD_START,
    period_end: dt.date = PERIOD_END,
):
    return await SalesRegisterLoader(pool).load(
        tenant.ctx,
        entity_id=entity_id,
        gstin=SUPPLIER_GSTIN,
        ingest_id=uuid.uuid4(),
        data=data,
        period_start=period_start,
        period_end=period_end,
    )


def _live_rows(
    admin: psycopg.Connection[tuple[object, ...]], tenant: SeededTenant, entity_id: uuid.UUID
) -> list[tuple[object, ...]]:
    return admin.execute(
        sql.SQL(
            "SELECT invoice_no, total_value, document_hash, id"
            "  FROM {}.sales_register"
            " WHERE entity_id = %s AND superseded_at IS NULL"
            " ORDER BY invoice_no"
        ).format(sql.Identifier(tenant.ctx.silver_schema)),
        (entity_id,),
    ).fetchall()


@pytest.fixture
def entity() -> uuid.UUID:
    """A fresh business entity per test.

    The natural key is (entity_id, gstin, invoice_no), so a fresh entity gives
    each test its own key space without dropping rows between tests — which
    would also hide a supersession bug.
    """
    return uuid.uuid4()


# =============================================================================
# Parsing — no database
# =============================================================================


def test_the_handwritten_fixture_parses_into_headers_and_lines() -> None:
    """The flat ERP export becomes header/line pairs. Six rows, five invoices."""
    result = parse_sales_register_csv(HANDWRITTEN.read_bytes())
    assert result.ok, result.rejections
    assert [i.invoice_no for i in result.invoices] == [
        "SI/2026-27/00001",
        "SI/2026-27/00002",
        "SI/2026-27/00003",
        "SI/2026-27/00004",
        "SI/2026-27/00005",
    ]
    assert [len(i.lines) for i in result.invoices] == [2, 1, 1, 1, 1]
    assert result.line_count == 6

    # B2CS and EXP both have no customer GSTIN, and that absence is a FACT. The
    # registry records this type as counterparty=OPTIONAL; a parser that required
    # one would reject every retail invoice and every export.
    assert result.invoices[2].invoice_type == "B2CS"
    assert result.invoices[2].customer_gstin is None
    assert result.invoices[4].invoice_type == "EXP"
    assert result.invoices[4].currency == "USD"


def test_document_hash_changes_when_a_line_changes_but_the_header_does_not() -> None:
    """The hole in TYPED-TABLES-PLAN.md 5, closed.

    §5 makes resubmission an upsert keyed on row_hash. A corrected line under an
    unchanged header produces an IDENTICAL header hash — so a header-only hash
    would no-op the resubmission and keep the stale line forever. This is why
    the table carries document_hash as well, and why the upsert compares that.
    """
    original = HANDWRITTEN.read_bytes()
    # Change one line's description. Every header field is untouched.
    corrected = original.replace(b"Switched mode power supply 650W", b"SMPS 650W ATX")

    before = parse_sales_register_csv(original).invoices[0]
    after = parse_sales_register_csv(corrected).invoices[0]

    assert before.row_hash == after.row_hash, "the header genuinely did not change"
    assert before.document_hash != after.document_hash, (
        "a corrected line must change the document hash, or the resubmission "
        "no-ops and the stale line survives"
    )


def test_rows_disagreeing_on_a_header_field_are_rejected() -> None:
    """Two rows, one invoice number, two places of supply.

    Taking the first row's value would silently discard one of two contradictory
    claims about the same invoice. The reason is asserted, not merely the
    failure — a validator that rejected everything would pass otherwise.
    """
    result = parse_sales_register_csv(HEADER_DISAGREEMENT.read_bytes())
    assert not result.ok
    assert any(
        "disagree on header field(s)" in r and "place_of_supply" in r
        for r in result.rejections
    ), result.rejections


def test_a_missing_required_column_is_reported_once() -> None:
    """Not once per row. A file with a structural problem should produce one
    legible rejection, not one per data row."""
    body = HANDWRITTEN.read_text().replace("place_of_supply,", "", 1)
    result = parse_sales_register_csv(body.encode())
    assert not result.ok
    assert len(result.rejections) == 1
    assert "place_of_supply" in result.rejections[0]


# =============================================================================
# Loading
# =============================================================================


@pytest.mark.asyncio
async def test_the_handwritten_fixture_loads_end_to_end(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    entity: uuid.UUID,
) -> None:
    outcome = await _load(app_pool, tenant_a, HANDWRITTEN.read_bytes(), entity_id=entity)
    assert (outcome.inserted, outcome.unchanged, outcome.superseded) == (5, 0, 0)

    rows = _live_rows(admin, tenant_a, entity)
    assert [r[0] for r in rows] == [
        "SI/2026-27/00001",
        "SI/2026-27/00002",
        "SI/2026-27/00003",
        "SI/2026-27/00004",
        "SI/2026-27/00005",
    ]

    lines = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.sales_register_line l"
            "  JOIN {}.sales_register h ON h.id = l.header_id"
            " WHERE h.entity_id = %s"
        ).format(
            sql.Identifier(tenant_a.ctx.silver_schema),
            sql.Identifier(tenant_a.ctx.silver_schema),
        ),
        (entity,),
    ).fetchone()
    assert lines == (6,)


@pytest.mark.asyncio
async def test_a_header_total_that_disagrees_with_its_lines_is_stored(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    entity: uuid.UUID,
) -> None:
    """The reason header and line are separate tables (TYPED-TABLES-PLAN.md 6).

    The fixture's header claims 100000.00 taxable against a single 90000.00
    line. transaction_document carried CHECK (total_value = taxable + tax),
    which would reject this file outright — destroying the evidence and
    reporting a real finding as a parse error. It must load, and the header
    total must be stored exactly as supplied rather than reconciled to the line
    sum at load time.
    """
    outcome = await _load(
        app_pool, tenant_a, TOTALS_DISAGREE.read_bytes(), entity_id=entity,
        period_start=dt.date(2026, 5, 1), period_end=dt.date(2026, 5, 31),
    )
    assert outcome.inserted == 1

    row = admin.execute(
        sql.SQL(
            "SELECT h.taxable_value, sum(l.taxable_value)"
            "  FROM {}.sales_register h"
            "  JOIN {}.sales_register_line l ON l.header_id = h.id"
            " WHERE h.entity_id = %s GROUP BY h.taxable_value"
        ).format(
            sql.Identifier(tenant_a.ctx.silver_schema),
            sql.Identifier(tenant_a.ctx.silver_schema),
        ),
        (entity,),
    ).fetchone()
    assert row is not None
    header_total, line_sum = row
    assert header_total == 100000
    assert line_sum == 90000, "the line was reconciled away — the finding is gone"


@pytest.mark.asyncio
async def test_an_export_with_no_invoice_totals_stores_null_not_the_line_sum(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    entity: uuid.UUID,
) -> None:
    """The reference export format carries no invoice-level totals at all.

    Three states have to stay distinguishable: the ERP claimed nothing (NULL),
    the ERP claimed zero, and the ERP claimed something that disagrees with its
    lines. Defaulting NULL to the line sum collapses the first into the third's
    opposite — every invoice then agrees with itself by construction and the
    comparison Pipeline 2 performs is worthless.
    """
    await _load(
        app_pool, tenant_a, NO_INVOICE_TOTALS.read_bytes(), entity_id=entity,
        period_start=dt.date(2026, 6, 1), period_end=dt.date(2026, 6, 30),
    )

    row = admin.execute(
        sql.SQL(
            "SELECT h.taxable_value, h.total_value, sum(l.taxable_value)"
            "  FROM {}.sales_register h"
            "  JOIN {}.sales_register_line l ON l.header_id = h.id"
            " WHERE h.entity_id = %s GROUP BY h.taxable_value, h.total_value"
        ).format(
            sql.Identifier(tenant_a.ctx.silver_schema),
            sql.Identifier(tenant_a.ctx.silver_schema),
        ),
        (entity,),
    ).fetchone()
    assert row is not None
    header_taxable, header_total, line_sum = row
    assert header_taxable is None, "a claim was manufactured from the line sum"
    assert header_total is None
    assert line_sum == 80000


@pytest.mark.asyncio
async def test_a_rejected_file_writes_nothing(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    entity: uuid.UUID,
) -> None:
    """Including the batch manifest. A READY batch describing rows that were
    never written is worse than no batch at all."""
    with pytest.raises(ValidationRejected):
        await _load(app_pool, tenant_a, HEADER_DISAGREEMENT.read_bytes(), entity_id=entity)

    assert _live_rows(admin, tenant_a, entity) == []
    assert admin.execute(
        sql.SQL("SELECT count(*) FROM {}.ingest_batch WHERE entity_id = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (entity,),
    ).fetchone() == (0,)


# =============================================================================
# Resubmission — TODO.md's open gap, closed at the row
# =============================================================================


@pytest.mark.asyncio
async def test_resubmitting_an_unchanged_file_is_a_no_op(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    entity: uuid.UUID,
) -> None:
    """The weekly full-file export, which is what created the gap.

    Under batch-level idempotency this produced a second READY batch for the
    same period with nothing to decide which one Pipeline 2 should believe. At
    the row it is four no-ops.
    """
    data = HANDWRITTEN.read_bytes()
    await _load(app_pool, tenant_a, data, entity_id=entity)
    second = await _load(app_pool, tenant_a, data, entity_id=entity)

    assert (second.inserted, second.unchanged, second.superseded) == (0, 5, 0)
    assert len(_live_rows(admin, tenant_a, entity)) == 5, "duplicate live rows"


@pytest.mark.asyncio
async def test_a_growing_register_inserts_only_what_is_new(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    entity: uuid.UUID,
) -> None:
    """Week two of the same export, with one more invoice on the end."""
    lines = HANDWRITTEN.read_text().rstrip("\n").split("\n")
    week_one = "\n".join(lines[:-1]) + "\n"
    week_two = "\n".join(lines) + "\n"

    first = await _load(app_pool, tenant_a, week_one.encode(), entity_id=entity)
    assert first.inserted == 4

    second = await _load(app_pool, tenant_a, week_two.encode(), entity_id=entity)
    assert (second.inserted, second.unchanged, second.superseded) == (1, 4, 0)
    assert len(_live_rows(admin, tenant_a, entity)) == 5


@pytest.mark.asyncio
async def test_a_corrected_invoice_supersedes_rather_than_duplicating(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    entity: uuid.UUID,
) -> None:
    """Close the live row, insert the new version. Both remain readable.

    The correction here is to a LINE, with every header field untouched — the
    case a header-only hash would miss.
    """
    original = HANDWRITTEN.read_bytes()
    corrected = original.replace(b"Switched mode power supply 650W", b"SMPS 650W ATX")

    await _load(app_pool, tenant_a, original, entity_id=entity)
    second = await _load(app_pool, tenant_a, corrected, entity_id=entity)
    assert (second.inserted, second.unchanged, second.superseded) == (0, 4, 1)

    assert len(_live_rows(admin, tenant_a, entity)) == 5, "the superseded row is still live"

    versions = admin.execute(
        sql.SQL(
            "SELECT superseded_at IS NULL, modified_by FROM {}.sales_register"
            " WHERE entity_id = %s AND invoice_no = 'SI/2026-27/00001'"
            " ORDER BY id"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (entity,),
    ).fetchall()
    assert [v[0] for v in versions] == [False, True], "expected one closed, one live"
    assert versions[0][1] == tenant_a.ctx.role_name(Role.INGEST), (
        "modified_by must record the role the DATABASE saw, not one the "
        "application supplied"
    )

    # The superseded version keeps its own lines. That is why the line table has
    # no superseded_at of its own.
    stale = admin.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.sales_register_line l"
            "  JOIN {}.sales_register h ON h.id = l.header_id"
            " WHERE h.entity_id = %s AND h.superseded_at IS NOT NULL"
            "   AND l.description = 'Switched mode power supply 650W'"
        ).format(
            sql.Identifier(tenant_a.ctx.silver_schema),
            sql.Identifier(tenant_a.ctx.silver_schema),
        ),
        (entity,),
    ).fetchone()
    assert stale == (1,)


@pytest.mark.asyncio
async def test_the_same_invoice_number_in_two_periods_is_caught(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity: uuid.UUID,
) -> None:
    """tax_period is excluded from the natural key ON PURPOSE.

    An invoice number must be unique for a registration regardless of which
    period's file carried it. This is also the reason the table must not be
    partitioned by tax_period: a unique index on a partitioned table must
    include every partition key column, which would force tax_period into the
    key and store both copies as live rows (TODO.md, "Partitioning").
    """
    data = HANDWRITTEN.read_bytes()
    await _load(app_pool, tenant_a, data, entity_id=entity)

    # Same invoices, re-sent under the NEXT period with a changed total, so the
    # upsert takes the supersede path rather than the no-op path.
    may = data.replace(b"533360.00", b"533400.00")
    outcome = await _load(
        app_pool, tenant_a, may, entity_id=entity,
        period_start=dt.date(2026, 5, 1), period_end=dt.date(2026, 5, 31),
    )
    assert outcome.superseded == 1, (
        "the April invoice was not recognised in the May file — the loader's "
        "lookup has picked up tax_period"
    )


def test_the_natural_key_index_excludes_tax_period(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Pins the INDEX, which the test above does not.

    Written after a mutation check found the gap: the loader's lookup and the
    unique index are two separate expressions of the same key, and adding
    tax_period to the index alone changes no test's behaviour — the loader
    supersedes before it inserts, so the second live row that would collide
    never exists. The failure only surfaces later, as a duplicated invoice in a
    report, with nothing having failed loudly.

    This is also the constraint that forbids partitioning by tax_period: a
    unique index on a partitioned table must include every partition key column
    (TODO.md, "Partitioning: decided NOT to partition").
    """
    for schema, indexdef in admin.execute(
        "SELECT schemaname, indexdef FROM pg_indexes"
        " WHERE indexname = 'sales_register_natural_key_uq'"
    ).fetchall():
        assert "(entity_id, gstin, invoice_no)" in indexdef, (
            f"{schema}: natural key is not (entity_id, gstin, invoice_no) — {indexdef}"
        )
        assert "superseded_at IS NULL" in indexdef, (
            f"{schema}: the unique index must be partial on the live version, or "
            f"superseded rows collide with their own replacements"
        )

    partitioned = admin.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname LIKE 't\\_%\\_silver' AND c.relkind = 'p'"
    ).fetchall()
    assert partitioned == [], (
        f"declarative partitioning appeared in Silver: {partitioned}. A unique "
        f"index on a partitioned table must include every partition key column, "
        f"which would force tax_period into the natural key above."
    )


# =============================================================================
# The read contract
# =============================================================================


@pytest.mark.asyncio
async def test_v1_view_exposes_the_common_core_only(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    admin: psycopg.Connection[tuple[object, ...]],
    entity: uuid.UUID,
) -> None:
    """Tenant extension columns live BELOW the view line (TYPED-TABLES-PLAN.md 7).

    If an extension column reached a v1_ view, inafinplatform/v2 would see a
    different shape per tenant and the view would stop being a contract. The
    check is that the view's column list is pinned here, so widening it is a
    deliberate edit to this test rather than a side effect of a migration.
    """
    await _load(app_pool, tenant_a, HANDWRITTEN.read_bytes(), entity_id=entity)

    columns = [
        r[0]
        for r in admin.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = 'v1_sales_register'"
            " ORDER BY ordinal_position",
            (tenant_a.ctx.silver_schema,),
        ).fetchall()
    ]
    assert columns == [
        "id", "entity_id", "gstin", "tax_period",
        "doc_type_code", "batch_id", "bronze_ingest_id",
        "row_hash", "document_hash",
        "invoice_no", "invoice_date", "customer_gstin", "invoice_type",
        "supply_type", "place_of_supply", "reverse_charge", "currency",
        "trade_discount", "freight", "packing", "insurance",
        "taxable_value", "cgst", "sgst", "igst", "cess", "total_value",
        "total_tax", "irn", "ewb_no",
        "valid_from", "valid_to", "recorded_at", "superseded_at",
    ]

    # created_by / modified_by are audit, not contract. They are the operator's
    # question, not v2's, and exposing them would make every audit column change
    # a v2-visible change.
    assert "created_by" not in columns


@pytest.mark.asyncio
async def test_recon_reaches_the_view_but_not_the_base_table(
    app_pool: TenantScopedPool, tenant_a: SeededTenant, entity: uuid.UUID
) -> None:
    """The grant matrix, re-asserted over the new tables.

    New tables are covered by app.apply_tenant_grants automatically — it
    enumerates pg_class rather than a hardcoded list. This proves the new table
    actually landed on the right side of that enumeration, which is the thing a
    dynamic matrix can get silently wrong.
    """
    await _load(app_pool, tenant_a, HANDWRITTEN.read_bytes(), entity_id=entity)

    async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
        rows = await (
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.v1_sales_register").format(
                    sql.Identifier(tenant_a.ctx.silver_schema)
                )
            )
        ).fetchone()
        assert rows is not None and rows[0] >= 5

    # TenantBoundaryViolation, not the raw psycopg error: TenantScopedPool
    # translates a privilege denial into a named boundary violation so it is
    # legible in a log rather than indistinguishable from a typo in a query.
    with pytest.raises(TenantBoundaryViolation, match="sales_register"):
        async with app_pool.transaction(tenant_a.ctx, Role.RECON) as conn:
            await conn.execute(
                sql.SQL("SELECT count(*) FROM {}.sales_register").format(
                    sql.Identifier(tenant_a.ctx.silver_schema)
                )
            )


def test_the_registry_knows_where_sales_register_rows_land(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The code -> table map resolves, and it resolves to a table that exists in
    every tenant's schema. A registry entry pointing at a table nobody created
    is worse than no entry: it reads as configured."""
    row = admin.execute(
        "SELECT table_name FROM platform_ref.document_type"
        " WHERE doc_type_code = 'SALES_REGISTER'"
    ).fetchone()
    assert row == ("sales_register",)

    missing = admin.execute(
        "SELECT n.nspname FROM pg_namespace n"
        " WHERE n.nspname LIKE 't\\_%\\_silver'"
        "   AND NOT EXISTS (SELECT 1 FROM pg_tables t"
        "                    WHERE t.schemaname = n.nspname"
        "                      AND t.tablename = 'sales_register')"
    ).fetchall()
    assert missing == [], f"registry names a table these schemas do not have: {missing}"
