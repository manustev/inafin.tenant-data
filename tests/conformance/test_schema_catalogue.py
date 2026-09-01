"""The published schema catalogue, checked against everything it claims.

This catalogue is read by ANOTHER REPO (`inafin-api`) and shown to real
tenants, who then export their ERP data to match it. A wrong row here is not an
internal inconsistency — it is a tenant building an export against a column
that does not exist, discovering it only when the upload fails. So the seeded
rows are not trusted, and these gates check them from three independent
directions:

  1. against the derivation (`src/catalogue/document_schema.py`) — catches a
     seed migration that was never regenerated after a loader changed;
  2. against the LIVE Silver DDL via `information_schema` — the strongest
     check, and the one that does not share code with the thing it is
     checking. If the derivation itself were wrong, gate 1 would happily agree
     with it and only this gate would notice;
  3. against the grant surface — this data is published, so "read-only" has to
     be enforced by Postgres rather than by intention.

MUTATION CHECK for these: change one `field_name` in migration 036's seed by
hand, re-migrate, and confirm exactly `test_the_catalogue_matches_the_derivation`
fails. Drop a column from a Silver table and confirm
`test_derived_columns_exist_in_the_silver_table` fails for that type alone.
"""

from __future__ import annotations

import psycopg
import pytest
from tests.conftest import SeededTenant

from src.catalogue import (
    Provenance,
    RegistryFacts,
    SchemaKind,
    derive_document_schema,
    derive_document_schemas,
)
from src.silver.registers.spec import ENVELOPE_COLUMNS

pytestmark = pytest.mark.conformance


def _facts(admin: psycopg.Connection[tuple[object, ...]]) -> list[RegistryFacts]:
    """Build the derivation's input from the DATABASE, not the CSV.

    The generator builds these from `registry/document_types.csv`. Reading them
    back from `platform_ref.document_type` here means a CSV-vs-database
    divergence shows up as a catalogue failure too, not only through
    `test_registry.py`'s own comparison.
    """
    return [
        RegistryFacts(
            doc_type_code=str(code),
            in_scope=bool(in_scope),
            dispatch_mechanism=str(mechanism),
            field_contract=str(contract),
            extraction_spec=str(spec),
        )
        for code, in_scope, mechanism, contract, spec in admin.execute(
            "SELECT doc_type_code, in_scope, dispatch_mechanism, field_contract,"
            " extraction_spec FROM platform_ref.document_type"
        ).fetchall()
    ]


def test_the_catalogue_matches_the_derivation(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The seeded rows are exactly what the derivation produces today.

    Fails when a `RegisterSpec` column, a `field_contract` cell or an
    `extraction_spec` cell changes and migration 036 is not followed by a new
    migration carrying the delta. THE FIX IS NEVER TO REGENERATE 036 — it is
    applied and checksum-pinned. Write a new migration, exactly as shared 014
    did for 004 and shared 033 did for 023.
    """
    expected = {
        s.doc_type_code: s for s in derive_document_schemas(_facts(admin))
    }

    actual_kinds = {
        str(code): (str(kind), str(prov))
        for code, kind, prov in admin.execute(
            "SELECT doc_type_code, schema_kind, provenance"
            " FROM platform_ref.document_type_schema"
        ).fetchall()
    }
    assert actual_kinds == {
        code: (s.schema_kind.value, s.provenance.value)
        for code, s in expected.items()
    }

    actual_fields: dict[str, list[tuple[object, ...]]] = {}
    for row in admin.execute(
        "SELECT doc_type_code, ordinal, field_name, scope, data_type, required,"
        " source_label FROM platform_ref.document_type_field"
        " ORDER BY doc_type_code, ordinal"
    ).fetchall():
        actual_fields.setdefault(str(row[0]), []).append(tuple(row[1:]))

    expected_fields = {
        code: [
            (f.ordinal, f.name, f.scope.value, f.data_type, f.required,
             f.source_label)
            for f in s.fields
        ]
        for code, s in expected.items()
        if s.fields
    }
    assert actual_fields == expected_fields


def test_every_pdf_extraction_type_publishes_document(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """`dispatch_mechanism` is authoritative for the upload shape.

    The regression this guards: `derive_document_schema` used to pick a
    source by "which one happens to be non-empty" rather than by
    `dispatch_mechanism`, so five `PDF_EXTRACTION` types
    (`BILL_OF_ENTRY`, `FIRC_BRC_REGISTER`, `FORM_15CA_15CB`,
    `GTA_INVOICE_CONSIGNMENT_NOTE`, `PAYROLL_TDS_REGISTER`) published
    `TABULAR` off a leftover `RegisterSpec`/`field_contract` cell while the
    real dispatch path (`src/dispatch/router.py`) sent the artefact to
    `pypdf` — the ERP upload E2E suite's finding (2026-09-01), and shared
    migration `043`'s fix. `test_the_catalogue_matches_the_derivation` above
    already proves the seeded rows match today's derivation; this test proves
    the derivation ITSELF cannot regress into that bug again, independent of
    what happens to be seeded — mutate `derive_document_schema` to consult
    `SPEC_BY_DOC_TYPE`/`field_contract` before `dispatch_mechanism` and this
    is what catches it, not the DDL gate below (which only ever sees a
    Silver table for the three that still happen to have one).
    """
    for facts in _facts(admin):
        if not facts.in_scope or facts.dispatch_mechanism != "PDF_EXTRACTION":
            continue
        schema = derive_document_schema(facts)
        assert schema.schema_kind is SchemaKind.DOCUMENT, (
            f"{facts.doc_type_code}: dispatch_mechanism=PDF_EXTRACTION but "
            f"published schema_kind={schema.schema_kind.value!r} — a tenant "
            f"following this contract would upload the wrong shape"
        )


def test_every_in_scope_type_is_published(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """No in-scope type may be silently absent.

    A type the portal cannot show is a type a tenant cannot supply. PENDING
    exists precisely so that "we have not published a field list for this yet"
    is a visible answer rather than a missing row.
    """
    published = {
        str(c) for (c,) in admin.execute(
            "SELECT doc_type_code FROM platform_ref.document_type_schema"
        ).fetchall()
    }
    in_scope = {
        str(c) for (c,) in admin.execute(
            "SELECT doc_type_code FROM platform_ref.document_type WHERE in_scope"
        ).fetchall()
    }
    assert published == in_scope

    out_of_scope_published = published & {
        str(c) for (c,) in admin.execute(
            "SELECT doc_type_code FROM platform_ref.document_type"
            " WHERE NOT in_scope"
        ).fetchall()
    }
    assert out_of_scope_published == set()


def test_pending_types_publish_no_fields(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The cross-table invariant migration 035's header says is NOT a CHECK.

    PENDING must mean "no fields published", and a type with fields must not be
    marked PENDING — otherwise the portal renders an empty table for a type
    that has a real schema, or claims a schema for one that does not.
    """
    rows = admin.execute(
        "SELECT s.doc_type_code, s.provenance, count(f.field_name)"
        " FROM platform_ref.document_type_schema s"
        " LEFT JOIN platform_ref.document_type_field f"
        "   ON f.doc_type_code = s.doc_type_code"
        " GROUP BY s.doc_type_code, s.provenance"
    ).fetchall()
    for code, provenance, n_fields in rows:
        if str(provenance) == Provenance.PENDING.value:
            assert n_fields == 0, f"{code} is PENDING but publishes {n_fields} fields"
        else:
            assert n_fields > 0, f"{code} is {provenance} but publishes no fields"


def test_derived_columns_exist_in_the_silver_table(
    admin: psycopg.Connection[tuple[object, ...]], tenant_a: SeededTenant,
) -> None:
    """THE GATE THAT DOES NOT SHARE CODE WITH WHAT IT CHECKS.

    Every DERIVED tabular field must name a real column on the type's own
    Silver table. `test_the_catalogue_matches_the_derivation` compares the
    seed to the derivation — if the derivation itself named a column that does
    not exist, both would agree and neither would notice. This reads
    `information_schema` instead.

    Envelope columns are subtracted, not asserted: a tenant never supplies
    `batch_id` or `row_hash`, so their ABSENCE from the published schema is the
    correct outcome and is checked in the other direction below.

    SALES_REGISTER IS EXCLUDED, and the exclusion is the finding this gate
    produced rather than a concession to make it pass. A1.01 is the one type
    that is header/line split across TWO tables (`sales_register` and
    `sales_register_line`), and the one whose published names are deliberately
    CSV-side names that do NOT equal the column they land in: the export
    carries `invoice_cgst`, which the loader writes to the header's `cgst`,
    precisely so it cannot be confused with the LINE's `cgst`. A tenant needs
    the CSV name, so publishing it is correct — but it means "every field names
    a real column" is the wrong assertion for this one type.

    Its equivalent guarantee is stronger and comes from elsewhere: the
    published fields are read straight off `sales_register.py`'s HEADER_FIELDS
    and LINE_FIELDS, which are the same tuples the loader validates a real
    upload against (`REQUIRED_COLUMNS`, checked at parse time). The catalogue
    cannot disagree with what the loader accepts, because it IS what the loader
    accepts.
    """
    silver = f"t_{tenant_a.slug}_silver"
    columns_by_table: dict[str, set[str]] = {}
    for table, column in admin.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = %s",
        (silver,),
    ).fetchall():
        columns_by_table.setdefault(str(table), set()).add(str(column))

    published = admin.execute(
        "SELECT f.doc_type_code, d.table_name, f.field_name"
        " FROM platform_ref.document_type_field f"
        " JOIN platform_ref.document_type_schema s"
        "   ON s.doc_type_code = f.doc_type_code"
        " JOIN platform_ref.document_type d"
        "   ON d.doc_type_code = f.doc_type_code"
        " WHERE s.provenance = %s AND s.schema_kind = %s"
        "   AND d.table_name IS NOT NULL"
        "   AND f.doc_type_code <> 'SALES_REGISTER'",
        (Provenance.DERIVED.value, SchemaKind.TABULAR.value),
    ).fetchall()
    assert published, "fixture assumption: some DERIVED tabular types have tables"

    missing: list[str] = []
    for code, table, field in published:
        known = columns_by_table.get(str(table), set())
        assert known, f"{code} names table {table}, which does not exist in {silver}"
        if str(field) not in known:
            missing.append(f"{code}.{field} (not a column of {table})")
    assert not missing, "published fields naming no real column: " + ", ".join(missing)


def test_no_envelope_column_is_ever_published(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A tenant supplies business columns, never our plumbing.

    `batch_id`, `row_hash`, `bronze_ingest_id` and the bitemporal tail are
    written by the loader. Publishing one would tell a tenant to put a value in
    a column the loader overwrites — and `row_hash` in particular decides
    whether a resubmission is a correction or a duplicate.

    SALES_REGISTER is the one exception, and a real one: its CSV genuinely
    carries `gstin` and `tax_period` per row, because the loader derives the
    header from them rather than from a batch parameter.

    Scoped to TABULAR types on purpose. ENVELOPE_COLUMNS is a set of COLUMN
    names, and a DOCUMENT-scope field is not a column — `ADVANCE_PRICING_
    AGREEMENT.valid_from` is the date printed on the agreement, bound to the
    literal PDF label "Valid From", and its collision with the bitemporal
    envelope's `valid_from` is a coincidence of English. Applying a
    column-plumbing rule to a document fact would force us to rename a field
    that a tenant reads straight off their own paperwork.
    """
    leaked = [
        (str(code), str(field))
        for code, field in admin.execute(
            "SELECT f.doc_type_code, f.field_name"
            " FROM platform_ref.document_type_field f"
            " JOIN platform_ref.document_type_schema s"
            "   ON s.doc_type_code = f.doc_type_code"
            " WHERE s.schema_kind = %s",
            (SchemaKind.TABULAR.value,),
        ).fetchall()
        if str(field) in ENVELOPE_COLUMNS and str(code) != "SALES_REGISTER"
    ]
    assert leaked == []


def test_app_login_can_read_the_catalogue_and_cannot_write_it(
    raw_app_conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The published surface, proven against Postgres rather than intended.

    `inafin-api` reads this ambiently — before any tenant role is assumed,
    because the portal shows document types during onboarding (migration 037's
    header). That ambient read is an invariant-#2 exception, so its limits are
    worth proving: readable, and not writable.

    NOTE this is a genuinely bare `app_login` connection with NO `SET LOCAL
    ROLE` — which is exactly the state `inafin-api` is in when it serves this.
    """
    n = raw_app_conn.execute(
        "SELECT count(*) FROM platform_ref.document_type_schema"
    ).fetchone()
    assert n is not None and int(str(n[0])) > 0

    n = raw_app_conn.execute(
        "SELECT count(*) FROM platform_ref.document_type_field"
    ).fetchone()
    assert n is not None and int(str(n[0])) > 0

    for stmt in (
        "INSERT INTO platform_ref.document_type_schema"
        " (doc_type_code, schema_kind, provenance)"
        " VALUES ('SALES_REGISTER', 'TABULAR', 'DERIVED')",
        "UPDATE platform_ref.document_type_field SET required = TRUE",
        "DELETE FROM platform_ref.document_type_schema",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            raw_app_conn.execute(stmt)
