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
from src.silver.contract import parse_contract
from src.silver.registers.catalog import SPEC_BY_DOC_TYPE
from src.silver.registers.spec import Column, Kind

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

#: Which `dispatch_mechanism` implies which upload shape, for types where no
#: field list resolves. Mirrors `src/dispatch/router.py`'s own table — adding a
#: mechanism there without adding it here leaves types UNSPECIFIED, which
#: `_check` in the generator reports rather than silently accepting.
_KIND_BY_MECHANISM: dict[str, SchemaKind] = {
    "SALES_REGISTER": SchemaKind.TABULAR,
    "REGISTER_LOADER": SchemaKind.TABULAR,
    "ARCHETYPE1_PROMOTE": SchemaKind.TABULAR,
    "PDF_EXTRACTION": SchemaKind.DOCUMENT,
    "GSTN_JSON_PROMOTE": SchemaKind.JSON,
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


def _from_field_contract(contract: str) -> tuple[SchemaField, ...]:
    parsed = parse_contract(contract)
    fields: list[SchemaField] = []
    ordinal = 1
    for attrs, scope in (
        (parsed.doc_attributes, Scope.HEADER),
        (parsed.line_attributes, Scope.LINE),
    ):
        for attr in attrs:
            fields.append(
                SchemaField(
                    ordinal=ordinal,
                    name=attr.name,
                    scope=scope,
                    data_type=attr.type,
                    required=attr.required,
                )
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

    Resolution order is strongest-source-first, and it is an order rather than
    a lookup because several types legitimately carry more than one source:
    `CREDIT_DEBIT_NOTE_REGISTER` has both a `RegisterSpec` and an archetype-1
    `field_contract` (the sixth session's `_RETIRED_ARCHETYPE_1_TYPES` finding
    — both paths would once have accepted it). The RegisterSpec wins here for
    the same reason `dispatch_mechanism` names REGISTER_LOADER for it: that is
    the path a real upload actually takes.
    """
    if not facts.in_scope:
        raise ValueError(
            f"{facts.doc_type_code} is out of scope — the catalogue publishes "
            f"in-scope types only, and an out-of-scope row has no upload path "
            f"to describe"
        )

    code = facts.doc_type_code

    if code in SPEC_BY_DOC_TYPE:
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.TABULAR,
            provenance=Provenance.DERIVED,
            fields=_from_register_columns(SPEC_BY_DOC_TYPE[code].columns),
        )

    if code == SALES_REGISTER_CODE:
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.TABULAR,
            provenance=Provenance.DERIVED,
            fields=_from_sales_register(),
        )

    if facts.field_contract:
        fields = _from_field_contract(facts.field_contract)
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.TABULAR,
            provenance=Provenance.DECLARED if fields else Provenance.PENDING,
            fields=fields,
        )

    if facts.extraction_spec:
        fields = _from_extraction_spec(facts.extraction_spec)
        # `fields=` with nothing after it is a real value, not an oversight —
        # the eleven narrative-contract types genuinely have no clean
        # label:value line (migration 019's header). Published as PENDING with
        # kind DOCUMENT: "send us the document, we read it as prose."
        return DocumentSchema(
            doc_type_code=code,
            schema_kind=SchemaKind.DOCUMENT,
            provenance=Provenance.DECLARED if fields else Provenance.PENDING,
            fields=fields,
        )

    return DocumentSchema(
        doc_type_code=code,
        schema_kind=_KIND_BY_MECHANISM.get(
            facts.dispatch_mechanism, SchemaKind.UNSPECIFIED
        ),
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
