"""Derive the tenant-facing field schema for every in-scope document type.

WHAT THIS IS FOR. `inafin-portal` shows a tenant, per document type, the exact
columns their ERP export must carry, and lets them download that schema before
they upload anything. That answer already exists in this repo — spread across
`RegisterSpec`, `sales_register.py`'s field tuples, `field_contract` and
`extraction_spec` — but only in shapes the loaders consume. This module is the
one place those are projected into a single published form.

**Nothing here invents a field.** Every emitted row traces to one of four
sources of truth, and which one it came from is recorded on the row as
`provenance` rather than being flattened away:

  DERIVED   the field list comes from a loader whose columns are already
            gated against live DDL — `RegisterSpec` (via
            `tests/conformance/test_register_specs.py::test_spec_matches_the_table`,
            which reads `information_schema` and `pg_index`) or
            `sales_register.py`, the reviewed reference loader. This is the
            strongest kind: the published schema cannot drift from the table
            without a test failing.
  DECLARED  the field list comes from a registry grammar cell —
            `field_contract` (archetype 1) or `extraction_spec` (the
            label:value archetypes). Real, parsed by the same parsers
            ingestion uses, but describing what we read OUT of a document
            rather than a table we own.
  PENDING   no field list is published for this type yet. An honest empty,
            not a silent omission: the portal can say "sample document only"
            instead of rendering a blank table.

**Descriptions and examples are deliberately absent.** No column comment
exists anywhere in this schema (checked: `pg_description` holds zero column
comments across the Silver tables), and writing 250-odd prose descriptions
from the column names alone would be exactly the invented-content mistake
CLAUDE.md already names once. `description` is nullable and unset; where a
genuine human-facing label DOES exist — the PDF label an `extraction_spec`
binds — it is carried as `source_label`, because that one came from a real
document.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from src.extraction.spec import parse_extraction_spec
from src.silver import sales_register
from src.silver.contract import Counterparty, parse_contract
from src.silver.registers.catalog import SPEC_BY_DOC_TYPE
from src.silver.registers.spec import Column, Kind
from src.silver.validate import REQUIRED_COLUMNS as _TRANSACTION_REQUIRED_COLUMNS

#: `SALES_REGISTER` is the one type with a header/line split and the one type
#: with a hand-written loader rather than a `RegisterSpec` — see
#: `TYPED-TABLES-PLAN.md` step 5. Its field tuples are imported directly, so
#: this catalogue and that loader cannot disagree about what a caller must send.
SALES_REGISTER_CODE = "SALES_REGISTER"


class SchemaKind(StrEnum):
    """How the tenant supplies this document type — what the portal renders.

    This is NOT the same question as "which loader runs" (that is
    `dispatch_mechanism`, registry data). Two types can share a mechanism and
    differ here only in whether a column list exists to publish.
    """

    TABULAR = "TABULAR"
    """A CSV/NDJSON export whose columns are published below."""

    DOCUMENT = "DOCUMENT"
    """The source document itself (a PDF). The fields below, where present,
    are what we read OUT of it — they tell a tenant which facts the document
    must actually show, not what to put in a spreadsheet."""

    JSON = "JSON"
    """A portal/API payload uploaded exactly as the issuing authority
    produced it (the GSTN returns). We do not own or republish that shape —
    the authority does — so the sample file, not a column list, is the
    schema."""

    UNSPECIFIED = "UNSPECIFIED"
    """In scope, but no upload path is built yet — the type has no
    `dispatch_mechanism`. Listed so the portal can show the full registry
    honestly rather than silently dropping a third of it."""


class Provenance(StrEnum):
    """Where this type's field list came from, and therefore how much it is
    worth. See the module docstring."""

    DERIVED = "DERIVED"
    DECLARED = "DECLARED"
    PENDING = "PENDING"


class Scope(StrEnum):
    """Which grain a field belongs to.

    Load-bearing for `SALES_REGISTER` and for archetype 1's `field_contract`,
    both of which are header/line shaped: a tenant sending one flat CSV needs
    to know that `invoice_no` repeats down the invoice's lines while
    `line_number` does not.
    """

    ROW = "ROW"
    """Flat register — one row is one record, no split."""

    HEADER = "HEADER"
    LINE = "LINE"

    DOCUMENT = "DOCUMENT"
    """A fact read off a document rather than a column in a file."""


@dataclass(frozen=True, slots=True)
class SchemaField:
    ordinal: int
    """1-based, per document type, in the order the source declares them.

    Order is published because it is meaningful: `RegisterSpec`'s columns are
    in DDL order, and `row_hash` — which decides whether a resubmitted row is
    a correction or a duplicate — is computed over that order.
    """

    name: str
    scope: Scope
    data_type: str
    required: bool
    source_label: str | None = None
    """The literal label this field is bound to on a PDF, for
    `extraction_spec`-derived rows only. A real string off a real specimen,
    never invented."""

    description: str | None = None
    """Always None today. See the module docstring — there is no source of
    truth for prose descriptions in this repo, and inventing one is worse
    than leaving the column honestly empty."""


@dataclass(frozen=True, slots=True)
class RegistryFacts:
    """The registry columns this derivation reads, and nothing else.

    A record rather than a live query on purpose: the generator builds these
    from `registry/document_types.csv` (offline, no database) and the
    conformance gate builds them from `platform_ref.document_type` (live).
    Both then run the identical derivation, so a divergence between the CSV
    and the database surfaces as a catalogue mismatch as well as through
    `test_registry.py`'s own comparison.
    """

    doc_type_code: str
    in_scope: bool
    dispatch_mechanism: str
    field_contract: str
    extraction_spec: str


@dataclass(frozen=True, slots=True)
class DocumentSchema:
    doc_type_code: str
    schema_kind: SchemaKind
    provenance: Provenance
    fields: tuple[SchemaField, ...]


#: `Kind` is coarser than the SQL type on purpose (see `registers/spec.py`) and
#: this catalogue publishes the coarse name, not the domain. A tenant preparing
#: a CSV needs to know "a decimal goes here"; `money_inr` vs `qty` vs
#: `tax_rate` is a database-side validation detail, and republishing it here
#: would invite this table to disagree with the domain that actually enforces it.
_KIND_NAMES: dict[Kind, str] = {
    Kind.TEXT: "text",
    Kind.DATE: "date",
    Kind.DECIMAL: "decimal",
    Kind.INTEGER: "integer",
    Kind.BOOLEAN: "boolean",
}


def _from_register_columns(columns: Sequence[Column]) -> tuple[SchemaField, ...]:
    return tuple(
        SchemaField(
            ordinal=i,
            name=c.name,
            scope=Scope.ROW,
            data_type=_KIND_NAMES[c.kind],
            required=c.required,
        )
        for i, c in enumerate(columns, start=1)
    )


def _from_sales_register() -> tuple[SchemaField, ...]:
    """A1.01, the only header/line type, read straight off its loader.

    `REQUIRED_VALUES` is a subset of the field names rather than a flag per
    field — the loader's own shape — so required-ness is a membership test
    here, not a second list this module could get wrong.
    """
    fields: list[SchemaField] = []
    ordinal = 1
    for names, scope in (
        (sales_register.HEADER_FIELDS, Scope.HEADER),
        (sales_register.LINE_FIELDS, Scope.LINE),
    ):
        for name in names:
            fields.append(
                SchemaField(
                    ordinal=ordinal,
                    name=name,
                    scope=scope,
                    # The loader parses these positionally against typed
                    # dataclasses rather than declaring a Kind per field, and
                    # inferring "decimal" from a name like `freight` would be a
                    # guess. `text` is what the CSV literally carries; the
                    # database's own domains are what reject a bad value.
                    data_type="text",
                    required=name in sales_register.REQUIRED_VALUES,
                )
            )
            ordinal += 1
    return tuple(fields)


#: The fixed archetype-1 columns, in the order a client should see them.
#: Required-ness for the ones in `_TRANSACTION_REQUIRED_COLUMNS` is a
#: membership test against `src/silver/validate.py`'s own constant — the
#: loader's actual requirement, never a second copy of it that could drift.
#: `counterparty_gstin`/`counterparty_name` are not in that frozenset because
#: their requiredness depends on `Counterparty`, handled separately below.
#: The rest (`currency`, `description`, `uom`, the four tax-head amounts,
#: `itc_amount`) are genuinely optional columns `validate_transaction_csv`
#: reads via `row.get(...)` with a default — real, accepted columns, not a
#: guess.
_TRANSACTION_HEADER_ENVELOPE: tuple[str, ...] = ("doc_number", "doc_date", "currency")
_TRANSACTION_LINE_ENVELOPE: tuple[str, ...] = (
    "line_number", "hsn_sac", "description", "quantity", "uom", "unit_price",
    "taxable_value", "gst_rate", "cgst_amount", "sgst_amount", "igst_amount",
    "cess_amount", "itc_amount",
)

#: Coarse `data_type` per envelope column — the same "what the CSV cell
#: literally is" reasoning `_from_sales_register` documents, not a guess at
#: the underlying domain. `document_type_field`'s constraint columns
#: (migration 041/042) are DERIVED types only (`field_constraints.py`'s own
#: docstring), so an ARCHETYPE1_PROMOTE (DECLARED) column has no second place
#: to carry a real Postgres type — this is deliberately the coarse answer.
_TRANSACTION_ENVELOPE_TYPES: dict[str, str] = {
    "doc_number": "text", "doc_date": "date", "currency": "text",
    "counterparty_gstin": "text", "counterparty_name": "text",
    "line_number": "integer", "hsn_sac": "text", "description": "text",
    "quantity": "decimal", "uom": "text", "unit_price": "decimal",
    "taxable_value": "decimal", "gst_rate": "decimal",
    "cgst_amount": "decimal", "sgst_amount": "decimal",
    "igst_amount": "decimal", "cess_amount": "decimal", "itc_amount": "decimal",
}


def _from_field_contract(contract: str) -> tuple[SchemaField, ...]:
    """An ARCHETYPE1_PROMOTE type's full upload shape.

    THE GAP THIS CLOSES. `field_contract` names only what a type ADDS on top
    of the archetype's fixed shape (module docstring: "what one document type
    adds to the archetype's fixed shape") — but this function used to publish
    ONLY those additions, never the fixed columns underneath them. Every
    ARCHETYPE1_PROMOTE upload actually goes through
    `src/silver/validate.py::validate_transaction_csv`, which always demands
    `doc_number`, `doc_date`, `line_number`, `hsn_sac`, `quantity`,
    `unit_price`, `taxable_value`, `gst_rate` (`REQUIRED_COLUMNS`) plus a
    counterparty column decided by `Counterparty` — none of which
    `field_contract` ever names, so none of it was ever published. Found by
    the ERP upload E2E suite (2026-09-01) against all five in-scope
    ARCHETYPE1_PROMOTE types at once, because the gap is systemic, not
    per-type: `EWAY_BILL_OUTWARD_REGISTER`, `ICEGATE_BILL_OF_ENTRY`,
    `ICEGATE_SHIPPING_BILL`, `IRN_IRP_REGISTER`, `SEZ_BILL_OF_ENTRY` are the
    entire in-scope set for this mechanism.

    The envelope is read off `validate.py`'s own `REQUIRED_COLUMNS` and
    `Counterparty` handling, not restated by hand, for the same reason
    `_from_sales_register` reads `REQUIRED_VALUES` off its loader.
    """
    parsed = parse_contract(contract)
    fields: list[SchemaField] = []
    ordinal = 1

    def add(name: str, scope: Scope, required: bool) -> None:
        nonlocal ordinal
        fields.append(
            SchemaField(
                ordinal=ordinal, name=name, scope=scope,
                data_type=_TRANSACTION_ENVELOPE_TYPES[name], required=required,
            )
        )
        ordinal += 1

    for name in _TRANSACTION_HEADER_ENVELOPE:
        add(name, Scope.HEADER, name in _TRANSACTION_REQUIRED_COLUMNS)

    # A FOREIGN counterparty has no GSTIN to supply — validate.py REJECTS one
    # if present — so the column is not published at all for that case,
    # rather than published as an optional field a tenant could legally send.
    if parsed.counterparty is not Counterparty.FOREIGN:
        add("counterparty_gstin", Scope.HEADER, parsed.counterparty is Counterparty.REQUIRED)
    add("counterparty_name", Scope.HEADER, parsed.counterparty is Counterparty.FOREIGN)

    for attr in parsed.doc_attributes:
        fields.append(
            SchemaField(ordinal=ordinal, name=attr.name, scope=Scope.HEADER,
                        data_type=attr.type, required=attr.required)
        )
        ordinal += 1

    for name in _TRANSACTION_LINE_ENVELOPE:
        add(name, Scope.LINE, name in _TRANSACTION_REQUIRED_COLUMNS)

    for attr in parsed.line_attributes:
        fields.append(
            SchemaField(ordinal=ordinal, name=attr.name, scope=Scope.LINE,
                        data_type=attr.type, required=attr.required)
        )
        ordinal += 1

    return tuple(fields)


def _from_extraction_spec(spec: str) -> tuple[SchemaField, ...]:
    parsed = parse_extraction_spec(spec)
    return tuple(
        SchemaField(
            ordinal=i,
            name=f.name,
            scope=Scope.DOCUMENT,
            data_type=f.type,
            required=f.required,
            source_label=f.label,
        )
        for i, f in enumerate(parsed.fields, start=1)
    )


def derive_document_schema(facts: RegistryFacts) -> DocumentSchema:
    """One type's published schema.

    `dispatch_mechanism` IS the resolution order — not a fallback consulted
    only once every other source has come up empty, which is what this
    function did until the ERP upload E2E suite (2026-09-01, sixteenth
    session) found it publishing `TABULAR` for five `PDF_EXTRACTION` types
    (`BILL_OF_ENTRY`, `FIRC_BRC_REGISTER`, `FORM_15CA_15CB`,
    `GTA_INVOICE_CONSIGNMENT_NOTE`, `PAYROLL_TDS_REGISTER`). All five carry a
    LEFTOVER `RegisterSpec`/`field_contract` cell from an earlier design (three
    of them predate `dispatch_mechanism` entirely — tenant migration `020`
    built them as `REGISTER_LOADER` types before the CSV column existed) that
    the real dispatch path no longer reads: `src/dispatch/router.py` resolves
    `dispatch_mechanism` fresh, per trigger, straight from the database, and
    for these five it says `PDF_EXTRACTION` — so a tenant who filled in the
    published CSV contract had it handed to `pypdf`, which is not a CSV
    reader, and got `PdfStreamError` wrapped in an opaque HTTP 500.

    `RegistryFacts.dispatch_mechanism` is therefore checked FIRST, and each
    mechanism looks at only the ONE source that describes what it actually
    consumes — never "whichever source happens to be non-empty", which is
    exactly the bug. A `field_contract`/`RegisterSpec` cell left over from a
    superseded design is now correctly invisible to this derivation once
    `dispatch_mechanism` says something else runs instead.

    `CREDIT_DEBIT_NOTE_REGISTER` is the type that originally justified a
    "strongest source wins" reading — the sixth session's
    `_RETIRED_ARCHETYPE_1_TYPES` finding, which had it accepted by both a
    `RegisterSpec` AND an archetype-1 `field_contract` at once. That case is
    still handled correctly here, for the more precise reason: its
    `dispatch_mechanism` is `REGISTER_LOADER`, so only the `RegisterSpec`
    branch below is ever consulted — the `field_contract` cell is simply never
    read for it, not out-competed.
    """
    if not facts.in_scope:
        raise ValueError(
            f"{facts.doc_type_code} is out of scope — the catalogue publishes "
            f"in-scope types only, and an out-of-scope row has no upload path "
            f"to describe"
        )

    code = facts.doc_type_code
    mechanism = facts.dispatch_mechanism

    if mechanism == "REGISTER_LOADER":
        if code not in SPEC_BY_DOC_TYPE:
            # A genuine data-integrity failure, not a shape this catalogue
            # should quietly paper over: `dispatch_mechanism` claims a loader
            # that does not exist. `scripts/gen_registry_seed.py`'s
            # `_check_dispatch_mechanism` already refuses to generate a CSV
            # seed with this mismatch; raising here catches the live-DB path
            # (`test_schema_catalogue.py`) with the same rule instead of
            # silently falling through to PENDING/UNSPECIFIED.
            raise ValueError(
                f"{code}: dispatch_mechanism=REGISTER_LOADER but has no "
                f"RegisterSpec entry in src/silver/registers/catalog.py"
            )
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.TABULAR,
            provenance=Provenance.DERIVED,
            fields=_from_register_columns(SPEC_BY_DOC_TYPE[code].columns),
        )

    if mechanism == "SALES_REGISTER":
        if code != SALES_REGISTER_CODE:
            raise ValueError(
                f"{code}: dispatch_mechanism=SALES_REGISTER but "
                f"SalesRegisterLoader only ever handles {SALES_REGISTER_CODE!r}"
            )
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.TABULAR,
            provenance=Provenance.DERIVED,
            fields=_from_sales_register(),
        )

    if mechanism == "ARCHETYPE1_PROMOTE":
        fields = _from_field_contract(facts.field_contract) if facts.field_contract else ()
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.TABULAR,
            provenance=Provenance.DECLARED if fields else Provenance.PENDING,
            fields=fields,
        )

    if mechanism == "PDF_EXTRACTION":
        # Only `extraction_spec` describes a PDF_EXTRACTION intake — a
        # `field_contract`/`RegisterSpec` cell, even a non-empty one, names a
        # DIFFERENT mechanism's CSV shape and must not be read here. Three of
        # the five types this comment's neighbours were named for
        # (`FIRC_BRC_REGISTER`, `FORM_15CA_15CB`, `PAYROLL_TDS_REGISTER`) have
        # no `extraction_spec` at all — they are archetype-2's bespoke
        # per-type table parsers (`src/extraction/register_types.py`), which
        # this catalogue has no declared field list for (module docstring:
        # "not label:value-shaped, so there is no extraction_spec for them").
        # `fields=()` there is the honest answer, not a gap: PENDING/DOCUMENT,
        # "send us the document, we have no declared column list for it yet."
        fields = _from_extraction_spec(facts.extraction_spec) if facts.extraction_spec else ()
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.DOCUMENT,
            provenance=Provenance.DECLARED if fields else Provenance.PENDING,
            fields=fields,
        )

    if mechanism == "GSTN_JSON_PROMOTE":
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.JSON,
            provenance=Provenance.PENDING,
            fields=(),
        )

    # No mechanism at all (not upload-triggered yet), or a value this
    # function does not recognise. UNSPECIFIED either way — a type with no
    # working dispatch path has no upload shape to publish, and crashing the
    # whole catalogue generation over one bad cell would be worse than
    # reporting it honestly as "not built yet". `_check_dispatch_mechanism`
    # in the generator (offline, from the CSV) is what actually catches a
    # genuinely unrecognised mechanism string before it reaches here.
    return DocumentSchema(
        doc_type_code=code,
        schema_kind=SchemaKind.UNSPECIFIED,
        provenance=Provenance.PENDING,
        fields=(),
    )


def derive_document_schemas(
    facts: Iterable[RegistryFacts],
) -> list[DocumentSchema]:
    """Every in-scope type, ordered by code so the generated seed is stable."""
    return [
        derive_document_schema(f)
        for f in sorted(facts, key=lambda f: f.doc_type_code)
        if f.in_scope
    ]
