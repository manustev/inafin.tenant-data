"""Typed-table gates — the shared half.

TYPED-TABLES-PLAN.md build order step 1: the domains every typed table draws on,
and `document_type.table_name`, the code -> table map.

These cover the shared migration only. The per-tenant typed tables arrive at
step 3 and bring their own gates.

Each assertion here is a mutation check waiting to happen: break the constraint
in migrations/shared/010_typed_table_domains.sql, confirm exactly one of these
fails, restore. A suite nobody has seen fail is indistinguishable from
`assert True`.
"""

from __future__ import annotations

import psycopg
import pytest
from scripts.gen_registry_seed import SHARED_TABLES

pytestmark = pytest.mark.conformance


# =============================================================================
# Domains
# =============================================================================


@pytest.mark.parametrize(
    "value",
    [
        "27AAACR5055K1Z5",  # regular taxpayer
        "27AAACR5055K1D5",  # TDS deductor — position 14 is 'D', not 'Z'
        "29AAAAA0000A1Z5",
        "07AABCU9603R1ZM",  # checksum position is alphabetic
    ],
)
def test_gstin_domain_accepts_valid_registrations(
    admin: psycopg.Connection[tuple[object, ...]], value: str
) -> None:
    """A domain that rejects valid data is worse than the loose CHECK it replaced.

    The TDS case is the one that matters. The widely-published GSTIN regex pins
    position 14 to 'Z'; TDS ('D'), TCS ('C') and UIN registrations do not carry
    it, and all three legitimately appear as counterparties on a purchase
    register. Pinning it would drop those rows at ingestion.
    """
    assert admin.execute(
        "SELECT %s::platform_ref.gstin", (value,)
    ).fetchone() == (value,)


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("27AAAAAAAAAAAAA", "15 chars but no PAN — passes the old loose CHECK"),
        ("27AAACR5055K1Z", "14 chars"),
        ("27AAACR5055K1Z55", "16 chars"),
        ("27AAAC55055K1Z5", "digit inside the PAN name block"),
        ("27aaacr5055k1z5", "lowercase"),
        ("", "empty"),
    ],
)
def test_gstin_domain_rejects_malformed_values(
    admin: psycopg.Connection[tuple[object, ...]], value: str, why: str
) -> None:
    """The first case is the reason this domain exists.

    '27AAAAAAAAAAAAA' satisfies ^[0-9]{2}[A-Z0-9]{13}$ — the CHECK on
    transaction_document.counterparty_gstin — and is not a GSTIN.
    """
    with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
        admin.execute("SELECT %s::platform_ref.gstin", (value,))


def test_tax_rate_domain_bounds_the_percentage(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Structural only — WHICH rate is correct for an HSN is the corpus' answer."""
    assert admin.execute("SELECT 18.00::platform_ref.tax_rate").fetchone() == (18,)
    # Parenthesised on purpose: `-1::platform_ref.tax_rate` parses as
    # `-(1::platform_ref.tax_rate)`, so the cast sees 1, passes, and the negation
    # happens afterwards on the plain numeric — the domain is never consulted.
    for out_of_range in ("150.5", "(-1)"):
        with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
            admin.execute(f"SELECT {out_of_range}::platform_ref.tax_rate")


def test_money_domain_admits_negatives(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Deliberate. A credit note line, an ITC reversal and a debit adjustment are
    all legitimately negative, so a >= 0 CHECK would have to be dropped by the
    second document type that needed one."""
    assert admin.execute("SELECT (-1250.75)::platform_ref.money_inr").fetchone() == (
        -1250.75,
    )


# =============================================================================
# document_type.table_name — the code -> table map
# =============================================================================


def _probe_rejects(
    admin: psycopg.Connection[tuple[object, ...]], code: str, table_name: str
) -> None:
    """Assert the database refuses `table_name` for `code`, and leave no trace.

    The unconditional Rollback is load-bearing, and it is here because the
    mutation check caught its absence. Written as
    `with pytest.raises(CheckViolation), admin.transaction():` the probe UPDATE
    is rolled back only *by the constraint firing* — so the moment someone drops
    that constraint, the test both fails AND commits a bad row, and restoring
    the constraint then fails against the row the test just wrote. A gate must
    not corrupt the thing it is guarding when it fails.
    """
    with admin.transaction():
        with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
            admin.execute(
                "UPDATE platform_ref.document_type SET table_name = %s"
                " WHERE doc_type_code = %s",
                (table_name, code),
            )
        raise psycopg.Rollback


def test_only_allowlisted_tables_are_shared_by_several_types(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """One table per document type, with one declared exception.

    TYPED-TABLES-PLAN.md 8 forbade collapsing types onto a shared table, and
    shared 010 enforced it with a UNIQUE index on table_name. Shared 013 dropped
    that index because the customer's reference schema puts the seven D1.*
    marketplace reports in one table behind a report_type discriminator, and
    that was confirmed over §8 on 2026-08-07.

    Dropping the index turned a constraint into a convention, so this replaces
    it. Sharing must be DECLARED — the point is to catch a table name pasted
    into the wrong CSV cell, which the unique index used to catch for free and
    now nothing else would.
    """
    shared = admin.execute(
        "SELECT table_name, array_agg(doc_type_code ORDER BY doc_type_code)"
        "  FROM platform_ref.document_type"
        " WHERE table_name IS NOT NULL"
        " GROUP BY table_name HAVING count(*) > 1"
    ).fetchall()

    unexpected = {name: codes for name, codes in shared if name not in SHARED_TABLES}
    assert not unexpected, (
        f"table(s) shared by several document types without being declared in "
        f"scripts/gen_registry_seed.py::SHARED_TABLES: {unexpected}"
    )

    # And the declared one is actually shared — an allowlist entry that stopped
    # applying would silently widen what the check above permits.
    assert dict(shared).keys() == {
        n for n in SHARED_TABLES if n in dict(shared)
    }, "SHARED_TABLES has an entry that no longer describes reality"


def test_table_name_must_be_a_plain_unqualified_identifier(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Schema-qualifying it would break the isolation model.

    The same code names a table in every tenant's schema; the schema is supplied
    by the migration runner's substitution, never by the registry
    (TYPED-TABLES-PLAN.md 2). The length bound keeps `<table_name>_line` and the
    index names built from it inside NAMEDATALEN, where over-long names are
    silently truncated rather than rejected.
    """
    for bad in ("t_acme_silver.sales_register", "SalesRegister", "1sales", "a" * 51):
        _probe_rejects(admin, "SALES_REGISTER", bad)


def test_out_of_scope_types_cannot_name_a_table(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Mirrors the field_contract scope check (shared 009). A landing table for a
    document type with no ingestion path is unreachable configuration, and
    unreachable configuration is where wrong assumptions survive."""
    out_of_scope = admin.execute(
        "SELECT doc_type_code FROM platform_ref.document_type"
        " WHERE NOT in_scope LIMIT 1"
    ).fetchone()
    assert out_of_scope is not None, "expected the corpus-owned rows to exist"
    _probe_rejects(admin, out_of_scope[0], "somewhere")
