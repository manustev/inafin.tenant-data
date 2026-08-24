"""Every RegisterSpec, checked against the table it claims to describe.

**This is the gate that makes a declarative loader safe.** `src/silver/registers/`
replaces 23 copies of `sales_register.py` with one loader driven by a spec, and
the risk it trades into is drift: rename a column in a migration and the spec
still names the old one, with nothing failing until a real client file arrives.

So the spec is not trusted. `information_schema` and `pg_index` on a live tenant
schema are, and these tests compare the two on every axis the loader depends on —
column set, column ORDER, required flags, type kinds, the period column, and the
unique index the upsert keys on. A spec that disagrees with its table fails here,
at `make ci`, not in production.

The mutation check for these: rename or drop a column in
`migrations/tenant/010_a1_registers.sql`, re-migrate from a clean cluster, and
confirm exactly one of these fails for exactly that table. Restore.
"""

from __future__ import annotations

import psycopg
import pytest
from tests.conftest import SeededTenant

from src.silver.registers import SPECS, KeyKind, Kind, RegisterSpec
from src.silver.registers.catalog import SPEC_BY_DOC_TYPE
from src.silver.registers.spec import ENVELOPE_COLUMNS

pytestmark = pytest.mark.conformance

# What each Kind is allowed to be in the database. Domains report as their base
# type in information_schema.columns.data_type, with domain_name carrying the
# domain — both are checked, because `money_inr` and `qty` are both numeric and
# only the domain distinguishes them.
KIND_TO_SQL_TYPES: dict[Kind, frozenset[str]] = {
    Kind.TEXT: frozenset({"text", "character", "character varying"}),
    Kind.DATE: frozenset({"date"}),
    Kind.DECIMAL: frozenset({"numeric"}),
    Kind.INTEGER: frozenset({"integer", "bigint", "smallint"}),
    Kind.BOOLEAN: frozenset({"boolean"}),
}

SPEC_IDS = [s.table for s in SPECS]


def _columns(
    admin: psycopg.Connection[tuple[object, ...]], schema: str, table: str
) -> list[tuple[str, str, str | None, bool, bool]]:
    """(name, data_type, domain_name, not_null, has_default), in ordinal order."""
    return [
        (name, data_type, domain_name, is_nullable == "NO", default is not None)
        for name, data_type, domain_name, is_nullable, default in admin.execute(
            """
            SELECT column_name, data_type, domain_name, is_nullable, column_default
              FROM information_schema.columns
             WHERE table_schema = %s AND table_name = %s
             ORDER BY ordinal_position
            """,
            (schema, table),
        ).fetchall()
    ]


def _live_unique_index_columns(
    admin: psycopg.Connection[tuple[object, ...]], schema: str, table: str
) -> list[str]:
    """Columns of the table's partial unique index on live rows, in index order.

    Read from pg_index rather than from the migration text: the migration is what
    we wrote, the catalog is what the database actually has, and only the second
    one is what the upsert will contend with.
    """
    rows = admin.execute(
        """
        SELECT a.attname
          FROM pg_index i
          JOIN pg_class c   ON c.oid = i.indrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
          JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
         WHERE n.nspname = %s AND c.relname = %s
           AND i.indisunique AND i.indpred IS NOT NULL
         ORDER BY k.ord
        """,
        (schema, table),
    ).fetchall()
    return [str(r[0]) for r in rows]


# =============================================================================
# The spec against the table
# =============================================================================


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
def test_spec_business_columns_match_the_table_exactly(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant, spec: RegisterSpec
) -> None:
    """Same columns, same order.

    Order matters and is not cosmetic: `row_hash` is computed over the spec's
    columns in spec order, so a reordered spec silently changes every hash and
    turns the next resubmission of an unchanged file into a full re-insert.
    """
    schema = tenant_a.ctx.silver_schema
    live = _columns(admin, schema, spec.table)
    assert live, f"{schema}.{spec.table} does not exist"

    business = [name for name, *_ in live if name not in ENVELOPE_COLUMNS]
    assert business == list(spec.column_names)


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
def test_required_means_not_null_without_a_default(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant, spec: RegisterSpec
) -> None:
    """The one rule `Column.required` is derived from, asserted per column.

    A NOT NULL column WITH a default (`rcm_flag boolean not null default false`)
    is not required — a blank cell has a defined meaning. Get this backwards in
    the permissive direction and the loader lets a NULL reach a NOT NULL column,
    which is a crash mid-batch on a real file rather than a clean rejection.
    """
    schema = tenant_a.ctx.silver_schema
    live = {
        name: (not_null, has_default)
        for name, _dt, _dom, not_null, has_default in _columns(admin, schema, spec.table)
    }
    for column in spec.columns:
        not_null, has_default = live[column.name]
        assert column.required == (not_null and not has_default), column.name


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
def test_kinds_are_compatible_with_the_column_types(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant, spec: RegisterSpec
) -> None:
    """A DATE parsed as TEXT would insert '03-04-2026' into a date column."""
    schema = tenant_a.ctx.silver_schema
    live = {
        name: (data_type, domain)
        for name, data_type, domain, _nn, _hd in _columns(admin, schema, spec.table)
    }
    for column in spec.columns:
        data_type, domain = live[column.name]
        allowed = KIND_TO_SQL_TYPES[column.kind]
        assert data_type in allowed, (
            f"{spec.table}.{column.name} is {data_type}"
            f" (domain {domain}) but the spec calls it {column.kind}"
        )


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
def test_period_column_exists_on_the_table(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant, spec: RegisterSpec
) -> None:
    """`fy` and `tax_period` are not interchangeable — three registers are annual."""
    live = {name for name, *_ in _columns(admin, tenant_a.ctx.silver_schema, spec.table)}
    assert spec.period_column in live
    assert ({"tax_period", "fy"} & live) == {spec.period_column}


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
def test_key_columns_are_the_live_unique_index(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant, spec: RegisterSpec
) -> None:
    """The upsert's key and the index's key are the same key.

    This is the mutation check the last session's natural-key test failed: it
    pinned the loader's lookup while leaving the index free to drift. Reading
    pg_index closes that — a migration that changes the index without changing
    the spec fails here, rather than producing duplicate live rows nobody sees.
    """
    live = _live_unique_index_columns(admin, tenant_a.ctx.silver_schema, spec.table)
    assert live == list(spec.key_columns)


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
def test_content_keys_are_keyed_on_the_row_hash_and_natural_keys_are_not(
    spec: RegisterSpec,
) -> None:
    """The two strategies are distinguishable, and by the right thing.

    A natural key that included row_hash would never supersede — every corrected
    row would look new. A content key that omitted it would collapse unrelated
    rows onto one live version.
    """
    if spec.key_kind is KeyKind.CONTENT:
        assert "row_hash" in spec.key_columns
        assert spec.business_key_columns == ()
    else:
        assert "row_hash" not in spec.key_columns
        assert spec.business_key_columns != ()


# =============================================================================
# The catalog against the registry
# =============================================================================


def test_every_doc_type_maps_to_the_table_the_registry_names(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Shared migration 013 is the registry's answer; the catalog must agree.

    Two sources of truth for "which table does PURCHASE_REGISTER live in" is one
    too many. If they diverge, a loader writes to one table while every reader
    resolves the other, and both look healthy.
    """
    registry = {
        str(code): str(table)
        for code, table in admin.execute(
            "SELECT doc_type_code, table_name FROM platform_ref.document_type"
            " WHERE table_name IS NOT NULL"
        ).fetchall()
    }
    for code, spec in SPEC_BY_DOC_TYPE.items():
        assert registry.get(code) == spec.table, code


def test_the_catalog_covers_every_a1_type_except_sales_register(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """23 loaders, and the 24th is `src/silver/sales_register.py`.

    Fails when a new type gets a table and a registry row but no spec — which is
    otherwise invisible until someone uploads one and gets a KeyError.

    `GSTR_2B` (2026-08-19) and `GSTR_3B` (2026-08-24) are the other named
    exceptions: archetype-5 typed tables (`GSTN_JSON_PROMOTE`,
    `src/silver/gstn_returns/gstr2b.py`/`gstr3b.py`), not A1 registers — this
    test's scope has always been A1 coverage specifically, not "every
    table_name in the registry", so named non-A1 exceptions are the correct
    fix here, not a widened assertion that would stop catching the A1 gap
    this test exists for.
    """
    with_tables = {
        str(code)
        for (code,) in admin.execute(
            "SELECT doc_type_code FROM platform_ref.document_type"
            " WHERE table_name IS NOT NULL"
        ).fetchall()
    }
    assert with_tables - set(SPEC_BY_DOC_TYPE) == {
        "SALES_REGISTER",
        "GSTR_2B",
        "GSTR_3B",
    }


def test_v1_view_exposes_every_business_column(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """The v1_ view is v2's contract, and the loader writes what v2 must read.

    A business column that lands in Silver but is missing from the view is data
    the platform holds and cannot reason with — the failure mode is silence, not
    an error, which is why it is asserted rather than assumed.
    """
    schema = tenant_a.ctx.silver_schema
    for spec in SPECS:
        exposed = {
            name for name, *_ in _columns(admin, schema, f"v1_{spec.table}")
        }
        assert not set(spec.column_names) - exposed, spec.table
