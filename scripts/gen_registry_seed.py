#!/usr/bin/env python3
"""Generate the Document Type Registry seed migration from the reviewed CSV.

`registry/document_types.csv` is the source of truth — it is what the CA
engagement team reviews, in the register's own vocabulary. This script compiles
it to SQL.

The generated migration is COMMITTED, not generated at deploy time. Migrations
are checksum-pinned (src/migrate/runner.py); a migration whose content depends
on a file outside the migration chain would break that guarantee silently. Run
this after editing the CSV, and commit both.

    python scripts/gen_registry_seed.py && make migrate

Resolution rules, derived from the CSV rather than hardcoded:

  umbrella  a ref that resolves to several codes (A1.20 -> D1.01-07). Produces
            ref rows only, never a document_type row.
  alias     a ref describing a document already canonical elsewhere
            (D5.06 -> B3.05). Produces a ref row only.
  canonical everything else. Produces a document_type row, its canonical ref
            row, and a non-canonical ref row for each entry in alias_refs.
"""

from __future__ import annotations

import csv
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Imported rather than re-implemented: the grammar the runtime enforces at
# ingestion is the grammar a contract is checked against here, so a cell that
# would be rejected at ingestion cannot reach the CSV.
# Same reasoning, for the label:value extraction rule (src/extraction/spec.py)
# — a corrective refactor, 2026-08-11: this used to be 36 hardcoded Python
# ClassVar-only subclasses across src/extraction/*_types.py. See spec.py's
# module docstring for the full story.
# Same reasoning again, for dispatch_mechanism: the four routing mechanisms
# it names are real code, so what counts as "this doc_type_code is
# PDF_EXTRACTION" or "REGISTER_LOADER" is read from that code, not re-typed
# by hand into the CSV. See _check_dispatch_mechanism.
from src.catalogue.document_schema import (  # noqa: E402
    RegistryFacts,
    derive_document_schemas,
)
from src.extraction.register_types import REGISTER_DOCUMENT_EXTRACTORS  # noqa: E402
from src.extraction.spec import SpecError, parse_extraction_spec  # noqa: E402
from src.silver.contract import ContractError, parse_contract  # noqa: E402
from src.silver.promote import _RETIRED_ARCHETYPE_1_TYPES  # noqa: E402
from src.silver.registers.catalog import SPEC_BY_DOC_TYPE  # noqa: E402

CSV_PATH = ROOT / "registry" / "document_types.csv"
OUT_PATH = ROOT / "migrations" / "shared" / "004_document_registry_seed.sql"
CADENCE_PATH = ROOT / "migrations" / "shared" / "005_document_registry_cadence.sql"
CONTRACT_PATH = ROOT / "migrations" / "shared" / "009_document_registry_field_contract.sql"
EXTRACTION_SPEC_PATH = ROOT / "migrations" / "shared" / "016_extraction_spec.sql"
#: 017-022 are inafin-api's (a separate repo's) migrations, sharing this
#: chain's numbering space — 017 was NOT free. Confirmed with Steve
#: (dispatch item #2's design session, 2026-08-13): leave that chain
#: untouched, number past it.
DISPATCH_MECHANISM_PATH = ROOT / "migrations" / "shared" / "023_dispatch_mechanism.sql"
SCHEMA_CATALOGUE_PATH = (
    ROOT / "migrations" / "shared" / "036_document_schema_catalogue_seed.sql"
)

#: The four values dispatch_mechanism may hold. Kept here, not just as a DB
#: CHECK constraint, so _check_dispatch_mechanism can validate the CSV before
#: it ever reaches SQL.
DISPATCH_MECHANISMS = frozenset({
    "PDF_EXTRACTION", "SALES_REGISTER", "REGISTER_LOADER", "ARCHETYPE1_PROMOTE",
    # Added shared migration 033, hand-written (023_dispatch_mechanism.sql is
    # already applied and pinned — see 033's own header for why this value
    # widens that CHECK constraint in a new migration rather than by
    # regenerating 023 in place). Kept here only so this validation set
    # matches what's actually allowed in the live cluster; DISPATCH_MECHANISM_PATH
    # must NOT be regenerated to add it.
    "GSTN_JSON_PROMOTE",
})

# A new CSV column becomes a NEW migration, never an edit to an applied one —
# migrations are checksum-pinned (src/migrate/runner.py). 004 is generated from
# the columns it already knew about, so regenerating it after adding a column
# still produces byte-identical output and its checksum holds.

# Tables deliberately shared by several document types. The list is short on
# purpose — see shared migration 013 for why the marketplace reports are on it
# and why nothing else in the 125 qualifies.
SHARED_TABLES = frozenset({"platform_settlement_report"})

UMBRELLA_MARKER = "this ref is an alias"
ALIAS_MARKER = "canonical row is"


def q(s: str) -> str:
    """Single-quote a SQL string literal."""
    return "'" + s.replace("'", "''") + "'"


def arr(sections: str) -> str:
    parts = [p.strip() for p in sections.split(",") if p.strip()]
    return "ARRAY[" + ", ".join(q(p) for p in parts) + "]::text[]"


#: Generated files whose on-disk content NO LONGER matches what this script
#: produces — and where that is correct, permanent, and must not be "fixed".
#:
#: Both were corrected AFTER they were applied, by a later migration, with the
#: correction also written back into the CSV cell so the CSV stays the source
#: of truth. The pinned file therefore lags the CSV on purpose:
#:
#:   004  ITC_04_ACKNOWLEDGEMENT's archetype 5 -> 6, by shared 014
#:   023  GSTR_2B/GSTR_3B's dispatch rows and the widened CHECK, by shared 033
#:
#: Listing them here is what lets a routine run stay quiet about these two while
#: still treating ANY OTHER changed file as a hard error. Removing an entry does
#: not make the drift go away; it makes the generator refuse to run.
KNOWN_SEALED: dict[pathlib.Path, str] = {
    OUT_PATH: "ITC_04 archetype corrected 5->6 by shared migration 014",
    DISPATCH_MECHANISM_PATH: (
        "GSTR_2B/GSTR_3B rows and the widened CHECK added by shared migration 033"
    ),
}


def _emit(path: pathlib.Path, text: str, *, force: bool) -> list[str]:
    """Write a generated migration, refusing to silently rewrite a pinned one.

    THIS FUNCTION EXISTS BECAUSE THE HAZARD IS REAL AND HAS BEEN HIT TWICE.
    Every file this script writes is applied and checksum-pinned, so rewriting
    one breaks `make migrate` for everybody. Two of them (see KNOWN_SEALED)
    have since been corrected by a later migration, which means a fresh
    generation legitimately DIFFERS from what is on disk. The fifth session
    caught that by eye and reverted with `git checkout`; the eleventh session
    tripped over it again while adding the schema catalogue. Eye-checking a
    diff is not a control.

    Three outcomes, and only three:
      * path is in KNOWN_SEALED  -> never written; a note is printed. Expected.
      * content is unchanged     -> written (a no-op) and silent.
      * content would change     -> NOT written, returned as an error.

    Returns the errors it declined to raise, so one refusal does not abort the
    run before a genuinely new file (the schema catalogue) gets written.
    `--force` overrides everything, for the one legitimate case: a file that
    has never been applied anywhere.
    """
    if force:
        path.write_text(text)
        return []

    if path in KNOWN_SEALED:
        if path.exists() and path.read_text() != text:
            print(
                f"  sealed, not rewritten: {path.name} "
                f"({KNOWN_SEALED[path]})",
                file=sys.stderr,
            )
        return []

    if path.exists() and path.read_text() != text:
        return [
            f"{path.name} exists and its content would change. It is applied "
            f"and checksum-pinned, so rewriting it breaks `make migrate`. "
            f"Carry the delta in a NEW migration instead (precedent: shared "
            f"014 for 004, shared 033 for 023). Use --force only if this file "
            f"has never been applied."
        ]

    path.write_text(text)
    return []


def _write_cadence(canonical: list[dict[str, str]], *, force: bool) -> list[str]:
    """Emit the refresh_cadence column as its own migration."""
    scoped = [r for r in canonical if r["stream"] != "CORPUS"]
    out: list[str] = []
    w = out.append

    w("-- =============================================================================")
    w("-- Shared migration 005 — refresh_cadence.")
    w("--")
    w("-- GENERATED by scripts/gen_registry_seed.py from registry/document_types.csv.")
    w("--")
    w("-- Doc 4 Section 2: \"In Operational Mode, Mandatory documents are ingested at")
    w("-- onboarding and refreshed at configured intervals.\" This is the column that")
    w("-- drives that. Read from the PLATFORM's refresh perspective, not the")
    w("-- document's own periodicity:")
    w("--")
    w("--   ONE_TIME    requested at onboarding; re-requested only on change or expiry")
    w("--   PERIODIC    re-fetched or re-requested on a schedule")
    w("--   CONTINUOUS  delta-loaded as transactions occur")
    w("--")
    w("-- NULL where in_scope is false: cadence is meaningless for a document this")
    w("-- repo does not ingest.")
    w("-- =============================================================================")
    w("")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD COLUMN IF NOT EXISTS refresh_cadence text;")
    w("")
    w("UPDATE platform_ref.document_type d")
    w("   SET refresh_cadence = v.cadence")
    w("  FROM (VALUES")
    w(",\n".join(
        f"    ({q(r['doc_type_code'])}, {q(r['refresh_cadence'])})" for r in scoped
    ))
    w("  ) AS v(code, cadence)")
    w(" WHERE d.doc_type_code = v.code;")
    w("")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD CONSTRAINT document_type_cadence_values_ck")
    w("    CHECK (refresh_cadence IN ('ONE_TIME', 'PERIODIC', 'CONTINUOUS'));")
    w("")
    w("-- Mirrors document_type_scope_ck: everything we ingest is fully specified,")
    w("-- everything we do not is uniformly empty rather than defaulted.")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD CONSTRAINT document_type_cadence_scope_ck")
    w("    CHECK ((in_scope AND refresh_cadence IS NOT NULL)")
    w("        OR (NOT in_scope AND refresh_cadence IS NULL));")
    w("")

    return _emit(CADENCE_PATH, "\n".join(out), force=force)


def _write_field_contract(canonical: list[dict[str, str]], *, force: bool) -> list[str]:
    """Emit the per-document-type field contract as its own migration.

    This is what ARCHITECTURE.md 6 means by holding the per-type column contract
    as data. The archetype tables are fixed; what a SHIPPING_BILL requires and a
    PURCHASE_REGISTER does not is a string in this column, so the twelfth
    transaction type is an INSERT rather than a schema change.
    """
    populated = [r for r in canonical if r["field_contract"]]
    out: list[str] = []
    w = out.append

    w("-- =============================================================================")
    w("-- Shared migration 009 — the per-document-type field contract.")
    w("--")
    w("-- GENERATED by scripts/gen_registry_seed.py from registry/document_types.csv.")
    w("--")
    w("-- ARCHITECTURE.md 6: the archetype tables are fixed; what varies per document")
    w("-- type lives here as data. Grammar and parser: src/silver/contract.py, which")
    w("-- the generator imports — so a contract that would be rejected at ingestion")
    w("-- cannot be committed to the CSV.")
    w("--")
    w("--     direction=OUTWARD;counterparty=OPTIONAL;doc=irn:hash64!,ack_number:text")
    w("--")
    w("-- Empty for a document type whose archetype does not read it. NOT null: the")
    w("-- absence of extra fields is a contract too, and '' parses to exactly that,")
    w("-- whereas NULL would be indistinguishable from 'nobody has filled this in'.")
    w("-- =============================================================================")
    w("")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD COLUMN IF NOT EXISTS field_contract text NOT NULL DEFAULT '';")
    w("")
    w("UPDATE platform_ref.document_type d")
    w("   SET field_contract = v.contract")
    w("  FROM (VALUES")
    w(",\n".join(
        f"    ({q(r['doc_type_code'])}, {q(r['field_contract'])})" for r in populated
    ))
    w("  ) AS v(code, contract)")
    w(" WHERE d.doc_type_code = v.code;")
    w("")
    w("-- A contract may only exist where an archetype reads one. Out-of-scope rows")
    w("-- have no ingestion path at all, so a contract on one is unreachable code in")
    w("-- table form — and unreachable code is where wrong assumptions survive.")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD CONSTRAINT document_type_field_contract_scope_ck")
    w("    CHECK (field_contract = '' OR in_scope);")
    w("")

    return _emit(CONTRACT_PATH, "\n".join(out), force=force)


def _write_extraction_spec(canonical: list[dict[str, str]], *, force: bool) -> list[str]:
    """Emit the per-document-type PDF extraction rule as its own migration.

    Corrective refactor, 2026-08-11: this is what used to be 36 hand-written
    Python `ClassVar`-only subclasses across `src/extraction/*_types.py` —
    each pinning a `doc_type_code`, a `label_spec` dict and a fixed
    vocabulary constant as class attributes, one class per document type.
    That put a per-type extraction rule (which PDF label maps to which
    field) behind a code change and a deploy — the same defect
    `field_contract` already fixed for the Archetype 1/2 CSV contract, now
    fixed here the identical way. Grammar and parser:
    `src/extraction/spec.py`, which this generator imports — so a spec that
    would be rejected at extraction time cannot be committed to the CSV.

        authority=GSTN;fields=instrument_number:text!:"LUT ARN",validity:date_range!:"Validity"

    Empty for a document type whose archetype does not read one, and for the
    eleven narrative-contract types whose specimens have no clean
    label:value line at all (`fields=` — a real, explicit "nothing to bind"
    value, not an oversight). NOT null, same reasoning `field_contract`
    already gives: '' parses to "no fields", NULL would mean "nobody has
    filled this in yet".
    """
    populated = [r for r in canonical if r["extraction_spec"]]
    out: list[str] = []
    w = out.append

    w("-- =============================================================================")
    w("-- Shared migration 016 — the per-document-type PDF extraction spec.")
    w("--")
    w("-- GENERATED by scripts/gen_registry_seed.py from registry/document_types.csv.")
    w("--")
    w("-- Corrective refactor, 2026-08-11: replaces 36 hardcoded Python ClassVar-only")
    w("-- extractor subclasses (src/extraction/*_types.py) with data, the same fix")
    w("-- field_contract (migration 009) already applied to the Archetype 1/2 CSV")
    w("-- contract. Grammar and parser: src/extraction/spec.py, which the generator")
    w("-- imports — so a spec that would be rejected at extraction time cannot be")
    w("-- committed to the CSV.")
    w("--")
    w("--     authority=GSTN;fields=instrument_number:text!:\"LUT ARN\",")
    w("--         validity:date_range!:\"Validity\"")
    w("--")
    w("-- Empty for a document type whose archetype does not read one. NOT null: see")
    w("-- field_contract's identical reasoning (migration 009).")
    w("-- =============================================================================")
    w("")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD COLUMN IF NOT EXISTS extraction_spec text NOT NULL DEFAULT '';")
    w("")
    w("UPDATE platform_ref.document_type d")
    w("   SET extraction_spec = v.spec")
    w("  FROM (VALUES")
    w(",\n".join(
        f"    ({q(r['doc_type_code'])}, {q(r['extraction_spec'])})" for r in populated
    ))
    w("  ) AS v(code, spec)")
    w(" WHERE d.doc_type_code = v.code;")
    w("")
    w("-- Same scope guard field_contract carries: a spec on an out-of-scope row is")
    w("-- unreachable code in table form.")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD CONSTRAINT document_type_extraction_spec_scope_ck")
    w("    CHECK (extraction_spec = '' OR in_scope);")
    w("")

    return _emit(EXTRACTION_SPEC_PATH, "\n".join(out), force=force)


def _write_dispatch_mechanism(canonical: list[dict[str, str]], *, force: bool) -> list[str]:
    """Emit the per-document-type Bronze->Silver routing mechanism as its own
    migration.

    Dispatch item #2: before this, nothing in `platform_ref.document_type`
    named which of the four existing write paths (PDF extraction, the flat
    `RegisterLoader`, the hand-written `SalesRegisterLoader`, or
    archetype-1's `promote_transaction_documents`) handles a given
    `doc_type_code` — `src/dispatch/router.py` reads this column instead of
    re-deriving the routing decision from `archetype`/`table_name`, both of
    which are classification data, not a write-path switch (see
    `src/silver/promote.py`'s `_RETIRED_ARCHETYPE_1_TYPES` docstring for why
    `archetype` specifically cannot double as one). Adding the next document
    type under an existing mechanism is a CSV row, same as
    `field_contract`/`extraction_spec`; adding a genuinely new mechanism
    still needs one new branch in the dispatcher, once.
    """
    populated = [r for r in canonical if r["dispatch_mechanism"]]
    out: list[str] = []
    w = out.append

    w("-- =============================================================================")
    w("-- Shared migration 023 — dispatch_mechanism.")
    w("--")
    w("-- GENERATED by scripts/gen_registry_seed.py from registry/document_types.csv.")
    w("--")
    w("-- Which of the four Bronze->Silver write paths a doc_type_code routes")
    w("-- through: PDF_EXTRACTION (src/extraction/registry.py's extractor")
    w("-- registry), SALES_REGISTER (src/silver/sales_register.py, the one")
    w("-- hand-written loader), REGISTER_LOADER (src/silver/registers/catalog.py's")
    w("-- spec_for), or ARCHETYPE1_PROMOTE (src/silver/promote.py). Empty for a")
    w("-- document type with no upload-triggered write path yet (Stream A polled")
    w("-- types, or an in-scope type sampled but not yet built an extractor for).")
    w("-- NOT null: see field_contract's identical reasoning (migration 009).")
    w("-- =============================================================================")
    w("")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD COLUMN IF NOT EXISTS dispatch_mechanism text NOT NULL DEFAULT '';")
    w("")
    w("UPDATE platform_ref.document_type d")
    w("   SET dispatch_mechanism = v.mechanism")
    w("  FROM (VALUES")
    w(",\n".join(
        f"    ({q(r['doc_type_code'])}, {q(r['dispatch_mechanism'])})" for r in populated
    ))
    w("  ) AS v(code, mechanism)")
    w(" WHERE d.doc_type_code = v.code;")
    w("")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD CONSTRAINT document_type_dispatch_mechanism_values_ck")
    w("    CHECK (dispatch_mechanism IN ("
      + ", ".join(q(m) for m in sorted(DISPATCH_MECHANISMS))
      + ", ''));")
    w("")
    w("-- Same scope guard field_contract/extraction_spec carry: a mechanism on an")
    w("-- out-of-scope row is unreachable code in table form.")
    w("ALTER TABLE platform_ref.document_type")
    w("    ADD CONSTRAINT document_type_dispatch_mechanism_scope_ck")
    w("    CHECK (dispatch_mechanism = '' OR in_scope);")
    w("")

    return _emit(DISPATCH_MECHANISM_PATH, "\n".join(out), force=force)


def _write_schema_catalogue(canonical: list[dict[str, str]], *, force: bool) -> list[str]:
    """Emit the tenant-facing schema catalogue seed (tables: migration 035).

    The derivation lives in `src/catalogue/document_schema.py`, not here, for
    one reason: `tests/conformance/test_schema_catalogue.py` re-runs the SAME
    function against the LIVE rows and fails on divergence. A derivation
    written inline in this generator could only ever be checked against itself.

    REGENERATION IS NOT THE FIX WHEN THIS DRIFTS. Migration 036 is applied and
    checksum-pinned like every other; re-running this generator after adding a
    RegisterSpec column would rewrite it and break `make migrate`. The drift
    gate's failure message says so and names the alternative — a NEW migration
    carrying the delta, exactly the precedent `table_name` (see
    `_check_table_names`) and shared 033's dispatch-mechanism widen already
    set. This function exists to produce 036 once and to reproduce it
    byte-identically thereafter.
    """
    facts = [
        RegistryFacts(
            doc_type_code=r["doc_type_code"],
            in_scope=r["stream"] != "CORPUS",
            dispatch_mechanism=r["dispatch_mechanism"],
            field_contract=r["field_contract"],
            extraction_spec=r["extraction_spec"],
        )
        for r in canonical
    ]
    schemas = derive_document_schemas(facts)

    out: list[str] = []
    w = out.append
    w("-- =============================================================================")
    w("-- Shared migration 036 — the published schema catalogue, seeded.")
    w("--")
    w("-- GENERATED by scripts/gen_registry_seed.py from registry/document_types.csv")
    w("-- and src/catalogue/document_schema.py. Tables: migration 035, read its")
    w("-- header first — it states what these rows are and what they are NOT (they")
    w("-- describe the tenant-facing export contract, not the Silver table).")
    w("--")
    w("-- Every field below traces to a loader or a registry grammar cell. Nothing")
    w("-- here is written by hand or inferred from a column name.")
    w("--")
    w("-- DO NOT regenerate this file to fix drift — it is applied and checksum-")
    w("-- pinned. Carry the delta in a NEW migration; see _write_schema_catalogue.")
    w("-- =============================================================================")
    w("")
    w("INSERT INTO platform_ref.document_type_schema")
    w("    (doc_type_code, schema_kind, provenance)")
    w("VALUES")
    w(",\n".join(
        f"    ({q(s.doc_type_code)}, {q(s.schema_kind.value)}, {q(s.provenance.value)})"
        for s in schemas
    ))
    w(";")
    w("")

    rows: list[str] = []
    for sch in schemas:
        for f in sch.fields:
            rows.append(
                f"    ({q(sch.doc_type_code)}, {f.ordinal}, {q(f.name)}, "
                f"{q(f.scope.value)}, {q(f.data_type)}, "
                f"{'TRUE' if f.required else 'FALSE'}, "
                f"{q(f.source_label) if f.source_label is not None else 'NULL'})"
            )
    w("INSERT INTO platform_ref.document_type_field")
    w("    (doc_type_code, ordinal, field_name, scope, data_type, required,")
    w("     source_label)")
    w("VALUES")
    w(",\n".join(rows))
    w(";")
    w("")

    return _emit(SCHEMA_CATALOGUE_PATH, "\n".join(out), force=force)


def _check_dispatch_mechanism(canonical: list[dict[str, str]]) -> list[str]:
    """Cross-check dispatch_mechanism against the code it is meant to describe.

    Not free invention: a value here is only ever correct if the mechanism it
    names actually resolves this doc_type_code today.  Mirrors
    _check_table_names's framing (`TYPED-TABLES-PLAN.md 3`) applied to the
    fourth registry column that must stay a projection of running code, not a
    parallel source of truth that can drift from it silently.
    """
    pdf_register_codes = set(REGISTER_DOCUMENT_EXTRACTORS)
    errors: list[str] = []
    for r in canonical:
        mech = r["dispatch_mechanism"]
        in_scope = r["stream"] != "CORPUS"
        code = r["doc_type_code"]

        if not mech:
            continue
        if mech not in DISPATCH_MECHANISMS:
            errors.append(
                f"{r['ref']} ({code}): dispatch_mechanism {mech!r} is not one of "
                f"{sorted(DISPATCH_MECHANISMS)}"
            )
            continue
        if not in_scope:
            errors.append(
                f"{r['ref']} ({code}): out-of-scope row carries a dispatch_mechanism, "
                f"which nothing will ever read"
            )
            continue

        if mech == "PDF_EXTRACTION":
            if not r["extraction_spec"] and code not in pdf_register_codes:
                errors.append(
                    f"{r['ref']} ({code}): dispatch_mechanism=PDF_EXTRACTION but "
                    f"extraction_spec is empty and this is not one of the "
                    f"PDF-shaped register types in REGISTER_DOCUMENT_EXTRACTORS "
                    f"({sorted(pdf_register_codes)}) — build_extractor_registry "
                    f"would never serve an extractor for it"
                )
        elif mech == "SALES_REGISTER":
            if code != "SALES_REGISTER":
                errors.append(
                    f"{r['ref']} ({code}): dispatch_mechanism=SALES_REGISTER but "
                    f"SalesRegisterLoader only ever handles the literal code "
                    f"SALES_REGISTER"
                )
        elif mech == "REGISTER_LOADER":
            if code not in SPEC_BY_DOC_TYPE:
                errors.append(
                    f"{r['ref']} ({code}): dispatch_mechanism=REGISTER_LOADER but "
                    f"spec_for({code!r}) has no entry in "
                    f"src/silver/registers/catalog.py's SPEC_BY_DOC_TYPE"
                )
        elif mech == "ARCHETYPE1_PROMOTE":
            if r["archetype"] != "1":
                errors.append(
                    f"{r['ref']} ({code}): dispatch_mechanism=ARCHETYPE1_PROMOTE but "
                    f"archetype is {r['archetype']!r}, not '1' — "
                    f"promote_transaction_documents would refuse it"
                )
            if code in _RETIRED_ARCHETYPE_1_TYPES:
                errors.append(
                    f"{r['ref']} ({code}): dispatch_mechanism=ARCHETYPE1_PROMOTE but "
                    f"{code} is in _RETIRED_ARCHETYPE_1_TYPES — "
                    f"promote_transaction_documents would refuse it"
                )
    return errors


def _check_extraction_specs(canonical: list[dict[str, str]]) -> list[str]:
    """Parse every populated `extraction_spec` cell.

    Presence is not required the way `field_contract` is for archetype 1 —
    not every in-scope document type has a PDF extractor built yet (this
    batch samples 47 of them; the rest of archetypes 1/3/4/6/7/8 have no
    specimen PDF to extract from at all), so an empty cell is simply "no
    extractor for this type yet", not a gap to flag.
    """
    errors: list[str] = []
    for r in canonical:
        raw = r["extraction_spec"]
        if not raw:
            continue
        in_scope = r["stream"] != "CORPUS"
        try:
            parse_extraction_spec(raw)
        except SpecError as exc:
            errors.append(f"{r['ref']} ({r['doc_type_code']}): {exc}")
        if not in_scope:
            errors.append(
                f"{r['ref']} ({r['doc_type_code']}): out-of-scope row carries an "
                f"extraction_spec, which nothing will ever read"
            )
    return errors


def _check_contracts(canonical: list[dict[str, str]]) -> list[str]:
    """Parse every contract, and require one for every archetype-1 type.

    The presence rule is a test, not a CHECK constraint: it tightens as each
    archetype lands, and encoding "archetype 2 does not need one yet" in a
    migration would mean editing that migration when archetype 2 arrives —
    which the checksum pinning forbids.
    """
    errors: list[str] = []
    for r in canonical:
        raw = r["field_contract"]
        in_scope = r["stream"] != "CORPUS"
        archetype = r["archetype"] if in_scope else ""

        if raw:
            try:
                parse_contract(raw)
            except ContractError as exc:
                errors.append(f"{r['ref']} ({r['doc_type_code']}): {exc}")
            if not in_scope:
                errors.append(
                    f"{r['ref']} ({r['doc_type_code']}): out-of-scope row carries a "
                    f"field_contract, which nothing will ever read"
                )
        elif archetype == "1":
            errors.append(
                f"{r['ref']} ({r['doc_type_code']}): archetype 1 requires a "
                f"field_contract; the promotion path reads it to decide what is "
                f"mandatory"
            )
    return errors


def _check_table_names(canonical: list[dict[str, str]]) -> list[str]:
    """Validate `table_name`, which is the one registry column this script does
    NOT emit.

    TYPED-TABLES-PLAN.md 3: the code -> table map is catalog data, so the CSV
    stays its source of truth. But it is populated a batch at a time as each
    typed table is built, and regenerating a seed migration on every batch would
    edit a file that is already applied — which checksum pinning forbids
    (src/migrate/runner.py). So the VALUES land in the shared migration that
    accompanies each batch of tables, and this function checks the CSV side.

    The two halves are held together by
    tests/conformance/test_registry.py::test_registry_matches_reviewed_csv, which
    compares table_name in the database against this column. Adding a value here
    and forgetting the migration fails that test.
    """
    errors: list[str] = []
    seen: dict[str, list[str]] = {}
    for r in canonical:
        name = r["table_name"]
        if not name:
            continue
        if r["stream"] == "CORPUS":
            errors.append(
                f"{r['ref']} ({r['doc_type_code']}): out-of-scope row names a table "
                f"'{name}', which nothing will ever write to"
            )
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", name):
            errors.append(
                f"{r['ref']} ({r['doc_type_code']}): table_name '{name}' is not a "
                f"plain lowercase identifier of 50 chars or fewer"
            )
        seen.setdefault(name, []).append(r["doc_type_code"])

    # Shared tables are allowed but must be declared, not discovered. The unique
    # index on table_name was dropped in shared 013 for the marketplace reports
    # (TYPED-TABLES-PLAN.md 8, reversed 2026-08-07 on the reference schema), so
    # this is the only thing left standing between "types that genuinely share a
    # shape" and "someone pasted the wrong table name into a CSV cell".
    for name, users in sorted(seen.items()):
        if len(users) > 1 and name not in SHARED_TABLES:
            errors.append(
                f"table_name '{name}' is used by {len(users)} document types "
                f"({', '.join(sorted(users))}) but is not in SHARED_TABLES. "
                f"One table per document type unless the sharing is deliberate."
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    # `--force` overrides _emit's refusal to rewrite an existing generated file
    # whose content would change. Read _emit's docstring before using it: three
    # of these files are applied, pinned, and legitimately differ from what a
    # fresh generation produces.
    force = "--force" in (argv if argv is not None else sys.argv[1:])

    rows = list(csv.DictReader(CSV_PATH.open()))

    umbrella = [r for r in rows if UMBRELLA_MARKER in r["notes"]]
    aliases = [r for r in rows if ALIAS_MARKER in r["notes"]]
    canonical = [r for r in rows if r not in umbrella and r not in aliases]

    # An alias row must name exactly one canonical ref, and that ref must exist.
    by_ref = {r["ref"]: r for r in rows}
    for a in aliases:
        target = a["alias_refs"]
        if target not in by_ref:
            print(f"ERROR: alias {a['ref']} names unknown ref {target}", file=sys.stderr)
            return 1
        if by_ref[target] not in canonical:
            print(f"ERROR: alias {a['ref']} points at non-canonical {target}", file=sys.stderr)
            return 1

    codes = {r["doc_type_code"] for r in canonical}
    if len(codes) != len(canonical):
        print("ERROR: duplicate doc_type_code among canonical rows", file=sys.stderr)
        return 1

    problems = (
        _check_contracts(canonical)
        + _check_table_names(canonical)
        + _check_extraction_specs(canonical)
        + _check_dispatch_mechanism(canonical)
    )
    if problems:
        for e in problems:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    out: list[str] = []
    w = out.append

    w("-- =============================================================================")
    w("-- Shared migration 004 — Document Type Registry seed.")
    w("--")
    w("-- GENERATED by scripts/gen_registry_seed.py from registry/document_types.csv.")
    w("-- Do not edit by hand: edit the CSV and regenerate, or the reviewed source")
    w("-- and the deployed data diverge with nothing to detect it.")
    w("--")
    in_scope_n = sum(1 for r in canonical if r["stream"] != "CORPUS")
    w(f"-- {len(rows)} register refs -> {len(canonical)} document types "
      f"({in_scope_n} in scope, {len(canonical) - in_scope_n} corpus-owned).")
    w("-- =============================================================================")
    w("")
    w("-- Every document type is a universal_master value first — the FK from")
    w("-- document_type and from every tenant's artefact_ledger depends on it.")
    w("INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES")
    lines = [
        f"    ('Document_Type', {q(r['doc_type_code'])}, {q(r['name'][:400])})"
        for r in canonical
    ]
    w(",\n".join(lines))
    w("ON CONFLICT (value_type, value) DO NOTHING;")
    w("")
    w("")
    w("INSERT INTO platform_ref.document_type (")
    w("    doc_type_code, name, category, grp, obligation, mode, sections,")
    w("    stream, archetype, silver_storage, source_system, in_scope, notes")
    w(") VALUES")

    lines = []
    for r in canonical:
        in_scope = r["stream"] != "CORPUS"
        stream = q(r["stream"]) if in_scope else "NULL"
        archetype = r["archetype"] if in_scope else "NULL"
        lines.append(
            f"    ({q(r['doc_type_code'])}, {q(r['name'])}, {q(r['category'])}, "
            f"{q(r['grp'])}, {q(r['obligation'])}, {q(r['mode'])}, {arr(r['sections'])}, "
            f"{stream}, {archetype}, {q(r['silver_storage'])}, {q(r['source_system'])}, "
            f"{'TRUE' if in_scope else 'FALSE'}, {q(r['notes'])})"
        )
    w(",\n".join(lines))
    w("ON CONFLICT (doc_type_code) DO NOTHING;")
    w("")
    w("")
    w("INSERT INTO platform_ref.document_type_ref"
      " (register_ref, doc_type_code, is_canonical) VALUES")

    lines = []
    for r in canonical:
        lines.append(f"    ({q(r['ref'])}, {q(r['doc_type_code'])}, TRUE)")
        # alias_refs on a canonical row: other refs for the SAME document.
        for alias_ref in (a.strip() for a in r["alias_refs"].split(",") if a.strip()):
            lines.append(f"    ({q(alias_ref)}, {q(r['doc_type_code'])}, FALSE)")
    # Alias rows emit nothing: each one's ref was already produced by its
    # canonical row's alias_refs. Emitting both would violate the PK — which is
    # the check that the two directions agree.
    for u in umbrella:
        for code_ref in (c.strip() for c in u["alias_refs"].split(",") if c.strip()):
            target = by_ref[code_ref]
            lines.append(f"    ({q(u['ref'])}, {q(target['doc_type_code'])}, FALSE)")
    w(",\n".join(lines))
    w("ON CONFLICT (register_ref, doc_type_code) DO NOTHING;")
    w("")

    # Every writer is attempted before any refusal is reported, so a pinned
    # file that would change does not stop a genuinely new one being written.
    write_errors = (
        _emit(OUT_PATH, "\n".join(out), force=force)
        + _write_cadence(canonical, force=force)
        + _write_field_contract(canonical, force=force)
        + _write_extraction_spec(canonical, force=force)
        + _write_dispatch_mechanism(canonical, force=force)
        + _write_schema_catalogue(canonical, force=force)
    )
    if write_errors:
        for e in write_errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    n_contracts = sum(1 for r in canonical if r["field_contract"])
    n_specs = sum(1 for r in canonical if r["extraction_spec"])
    n_tables = sum(1 for r in canonical if r["table_name"])
    n_mechanisms = sum(1 for r in canonical if r["dispatch_mechanism"])
    for path in (
        OUT_PATH, CADENCE_PATH, CONTRACT_PATH, EXTRACTION_SPEC_PATH,
        DISPATCH_MECHANISM_PATH, SCHEMA_CATALOGUE_PATH,
    ):
        verb = "sealed" if path in KNOWN_SEALED else "wrote"
        print(f"{verb} {path.relative_to(ROOT)}")
    print(f"  {len(rows)} register refs")
    print(f"  {n_contracts} field contract(s)")
    print(f"  {n_specs} extraction spec(s)")
    print(f"  {n_mechanisms} dispatch mechanism(s)")
    print(f"  {n_tables} typed table(s) — checked, not emitted; see _check_table_names")
    print(f"  {len(canonical)} document types ({in_scope_n} in scope, "
          f"{len(canonical) - in_scope_n} corpus-owned)")
    print(f"  {len(umbrella)} umbrella ref(s), {len(aliases)} alias ref(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
