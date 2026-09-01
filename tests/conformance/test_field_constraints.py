"""The published constraints must equal the database's own — src/catalogue/
field_constraints.py, shared migrations 041 and 042.

WHAT THIS GATE IS FOR. The whole value of publishing constraints is that a
client can generate an export the database will actually accept. That holds
only while the published values still describe the live DDL, and nothing about
a seeded migration makes that self-maintaining: alter a column in a new tenant
migration and migration 042's rows silently become a lie, in exactly the
direction that hurts — a client trusting a constraint that no longer exists,
or unaware of one that now does.

So this re-runs the derivation against LIVE DDL and compares it to the LIVE
rows. That is not comparing the derivation to itself: the rows are frozen in
an applied, checksum-pinned migration while the DDL is free to move, so any
divergence between them is real drift. Same shape as
`test_register_specs.py::test_spec_matches_the_table`, which reads
`information_schema` and `pg_index` for the same reason one layer down.

WHEN THIS FAILS, REGENERATING 042 IS NOT THE FIX. It is applied and pinned;
rewriting it breaks `make migrate` (and `gen_field_constraints.py` refuses to
anyway). Carry the delta in a NEW migration.
"""

from __future__ import annotations

import psycopg
import pytest
from tests.conftest import SeededTenant

from src.catalogue.field_constraints import derive_field_constraints

pytestmark = pytest.mark.conformance


def _stored_fields(
    admin: psycopg.Connection[tuple[object, ...]],
) -> dict[tuple[str, str, str], tuple[object, ...]]:
    rows = admin.execute(
        "SELECT doc_type_code, scope, field_name, sql_domain, pattern,"
        "       allowed_values, min_value, max_value, max_length,"
        "       numeric_precision, numeric_scale"
        "  FROM platform_ref.document_type_field f"
        "  JOIN platform_ref.document_type_schema USING (doc_type_code)"
        "  JOIN platform_ref.document_type USING (doc_type_code)"
        " WHERE provenance = 'DERIVED' AND table_name IS NOT NULL"
    ).fetchall()
    return {(str(r[0]), str(r[1]), str(r[2])): r[3:] for r in rows}


def test_every_published_constraint_matches_the_live_ddl(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    """Field by field, the seeded values against a fresh derivation.

    Compared as a whole rather than per-facet: a partial assertion (say, only
    `sql_domain`) would pass while `allowed_values` drifted, and the point of
    publishing the set is that a client relies on all of it.
    """
    derived, _rules = derive_field_constraints(admin, tenant_a.ctx.silver_schema)
    stored = _stored_fields(admin)

    assert {(c.doc_type_code, c.scope, c.field_name) for c in derived} == set(stored), (
        "the set of published DERIVED fields no longer matches the derivation"
    )

    mismatches: list[str] = []
    for c in derived:
        key = (c.doc_type_code, c.scope, c.field_name)
        want = (
            c.sql_domain, c.pattern,
            list(c.allowed_values) if c.allowed_values else None,
            c.min_value, c.max_value, c.max_length,
            c.numeric_precision, c.numeric_scale,
        )
        got = stored[key]
        if want != got:
            mismatches.append(f"{key}\n     stored={got}\n     live  ={want}")

    assert not mismatches, (
        "published constraints have drifted from the live DDL "
        f"({len(mismatches)} field(s)). Carry the delta in a NEW migration; "
        "do NOT regenerate 042.\n\n" + "\n".join(mismatches)
    )


def test_every_published_rule_matches_the_live_ddl(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant
) -> None:
    _fields, derived = derive_field_constraints(admin, tenant_a.ctx.silver_schema)
    stored = {
        (str(r[0]), str(r[1])): (str(r[2]), list(r[3]))
        for r in admin.execute(
            "SELECT doc_type_code, constraint_name, expression, columns"
            "  FROM platform_ref.document_type_rule"
        ).fetchall()
    }
    assert {
        (r.doc_type_code, r.constraint_name): (r.expression, list(r.columns))
        for r in derived
    } == stored


def test_the_gstin_domain_reaches_every_field_that_uses_it(
    admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    """The headline case from the E2E finding.

    `supplier_gstin` published as bare `text` is what let a client generate a
    CSV valid against the downloaded schema and still be refused —
    `tests/handoff/test_trigger_dispatch.py` reproduces that refusal. Both
    halves must hold: the domain is NAMED (stable, self-describing) and its
    regex is CARRIED (actionable without a lookup).
    """
    rows = admin.execute(
        "SELECT doc_type_code, field_name, pattern FROM platform_ref.document_type_field"
        " WHERE sql_domain = 'platform_ref.gstin'"
    ).fetchall()
    assert rows, "no field publishes the gstin domain"
    for code, field, pattern in rows:
        assert pattern == "^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$", (
            f"{code}.{field} names the gstin domain but not its shape"
        )


def test_bounded_and_fixed_width_columns_publish_their_limits(
    admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    """The other two classes the finding names by name: a tax rate's bounds
    and the fixed-width codes.

    `character(3)` for a currency and `character(2)` for DR/CR are refused by
    Postgres on the first over-long value, and nothing else in the published
    schema hints at the width."""
    rate = admin.execute(
        "SELECT min_value, max_value, numeric_precision, numeric_scale"
        "  FROM platform_ref.document_type_field"
        " WHERE doc_type_code = 'PURCHASE_REGISTER' AND field_name = 'gst_rate'"
    ).fetchone()
    assert rate == (0, 100, 6, 3)

    widths = dict(
        admin.execute(
            "SELECT field_name, max_length FROM platform_ref.document_type_field"
            " WHERE max_length IS NOT NULL"
        ).fetchall()
    )
    assert widths.get("currency") == 3
    assert widths.get("dr_cr") == 2


def test_a_vocabulary_is_published_whole_and_in_order(
    admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    """A partial vocabulary would be worse than none — a client would trust
    it and be refused by the value it omitted. Order matters because the
    constraint declares one and a diff against the DDL should be readable."""
    row = admin.execute(
        "SELECT allowed_values FROM platform_ref.document_type_field"
        " WHERE doc_type_code = 'PURCHASE_REGISTER' AND field_name = 'itc_eligibility'"
    ).fetchone()
    assert row is not None
    assert row[0] == ["ELIGIBLE", "INELIGIBLE", "BLOCKED", "PARTIAL"]


def test_envelope_constraints_are_not_published(
    admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    """`valid_to > valid_from`, `doc_type_code = 'X'` and the
    superseded_at/modified_at pairing are real constraints a tenant can
    neither satisfy nor violate — those columns are written by the loader, not
    supplied in an export. Publishing them would tell a client to send
    something the loader owns.

    Asserted against the RULE table because that is where a multi-column
    envelope check would land if the "every column must be published" filter
    were dropped."""
    leaked = admin.execute(
        "SELECT doc_type_code, constraint_name, columns"
        "  FROM platform_ref.document_type_rule"
        " WHERE columns && ARRAY['valid_from', 'valid_to', 'superseded_at',"
        "                        'modified_at', 'doc_type_code', 'batch_id',"
        "                        'row_hash', 'bronze_ingest_id']"
    ).fetchall()
    assert leaked == [], f"envelope constraints published as tenant rules: {leaked}"


def test_declared_types_publish_no_constraints(
    admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    """A DECLARED type's fields come from a registry grammar cell and own no
    table, so there is no constraint to read. Empty is the honest answer;
    publishing zeros or empty arrays would imply the database accepts
    anything, and `provenance` already tells a client which kind this is."""
    stray = admin.execute(
        "SELECT f.doc_type_code, f.field_name"
        "  FROM platform_ref.document_type_field f"
        "  JOIN platform_ref.document_type_schema s USING (doc_type_code)"
        " WHERE s.provenance <> 'DERIVED'"
        "   AND (f.sql_domain IS NOT NULL OR f.pattern IS NOT NULL"
        "        OR f.allowed_values IS NOT NULL OR f.max_length IS NOT NULL)"
    ).fetchall()
    assert stray == []
