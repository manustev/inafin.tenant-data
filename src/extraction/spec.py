r"""The per-document-type extraction spec, parsed from the registry.

Corrective refactor (2026-08-11): the extraction work first built as 36
`ClassVar`-only Python subclasses (`entitlement_types.py`, `entity_master_
types.py`, `proceeding_event_types.py`, `financial_statement_types.py`, half
of `transaction_types.py`) — each pinning `doc_type_code`, a `label_spec`
dict and a fixed vocabulary constant (`issuing_authority`/`authority`) as
class attributes. That put a per-type PDF extraction rule behind a code
change and a deploy, which is exactly the defect ARCHITECTURE.md 6 names as
the reason the archetype design exists at all — and the exact problem this
repo already solved once, for Archetype 1/2, via
`platform_ref.document_type.field_contract` (`src/silver/contract.py`).

This module is that same mechanism applied to the label:value extraction
rule. `registry/document_types.csv` carries one `extraction_spec` cell per
archetype 3/4/6/7/8 row (and the two label:value archetype-1 types), and the
runtime reads it back from `platform_ref.document_type.extraction_spec`. Two
consumers, one grammar, parsed here — `scripts/gen_registry_seed.py` imports
this module precisely so a spec that would fail at extraction time cannot be
committed to the CSV in the first place, the same guarantee `parse_contract`
already gives `field_contract`.

    authority=GSTN;fields=instrument_number:text!:"LUT ARN",validity:date_range!:"Validity"

Clauses, separated by `;`:

    authority   a fixed `Issuing_Authority`/`Proceeding_Authority` vocabulary
                value (optional — only meaningful for archetypes 3 and 6;
                absent for 1/4/7/8)
    fields      the label:value fields this type binds, comma-separated
                (required, may be empty — `fields=` — for a type whose
                specimens have no clean label:value line at all, e.g. every
                archetype-8 narrative contract)
    table_pattern, table_columns
                the type's REPEATING table, if it has one — both-or-neither
                (optional; most types have no table at all). See below.

A field token is `name:type[!]:"Label Text"` — the same `name:type!`
shorthand `field_contract` already uses, with a quoted PDF label appended.
Types are `src/extraction/labelvalue.py`'s `FIELD_TYPES` vocabulary (`text`,
`date`, `date_range`, `money`, `int`, `gstin`, `hsn`) — reused, not
reinvented, so this grammar cannot drift from what `parse_label_value`
actually accepts.

**`table_pattern`/`table_columns`** — for a document whose real content is a
table, not header facts (`SHAREHOLDING_PATTERN`'s shareholder list, first
built against this grammar — see `src/extraction/tablevalue.py`):

    table_pattern="(?P<holder>.+?)\s+(?P<shares>[\d,]+)\s+(?P<pct1>[\d.]+)%\s+(?P<pct2>[\d.]+)%\s+(?P<pledged>Yes|No)";table_columns=holder:text,shares:money,pct1:money,pct2:money,pledged:text

`table_pattern` is ONE quoted regex with a named group per column, matched
whole against each row (`re.fullmatch` — no need to hand-write `^`/`$`).
`table_columns` is `name:type` pairs and must name EXACTLY the regex's named
groups, in either order — checked at parse time, same strictness as every
other clause here. Both are optional but must appear together; a type with
no table declares neither.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from src.extraction.labelvalue import FIELD_TYPES, LabelField, LabelSpec


class SpecError(ValueError):
    """An `extraction_spec` cell is not well formed. Raised at parse time,
    both in the generator (so it cannot be committed) and at runtime registry
    build (so a hand-edited registry row cannot silently widen what is
    accepted) — mirrors `src.silver.contract.ContractError`."""


_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_CLAUSE_KEYS: Final[frozenset[str]] = frozenset(
    {"authority", "fields", "table_pattern", "table_columns"}
)
_COLUMN_RE: Final = re.compile(r"^(?P<name>[a-z][a-z0-9_]*):(?P<type>[a-z_]+)$")

#: `name:type[!]:"Label Text"` — trailing `!` optional (defaults to required,
#: matching `field_contract`'s convention and `LabelField.required`'s
#: default), label quoted since it is free text that may contain punctuation
#: `field_contract` attributes never need to carry.
_FIELD_RE: Final = re.compile(
    r'^(?P<name>[a-z][a-z0-9_]*):(?P<type>[a-z_]+)(?P<required>!)?:"(?P<label>.*)"$'
)


@dataclass(frozen=True, slots=True)
class ExtractionField:
    name: str
    type: str
    required: bool
    label: str


@dataclass(frozen=True, slots=True)
class TableColumn:
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class TableSpec:
    """One repeating table row's shape — the colon-less counterpart to
    `LabelSpec`. `pattern` is ONE regex with a named group per column; a row
    either matches whole or is not a row of this table at all (a header/
    footer/title line simply fails to match and is skipped), unlike
    `Label: Value` binding, which finds each fact independently.
    """

    pattern: re.Pattern[str]
    columns: tuple[TableColumn, ...]


@dataclass(frozen=True, slots=True)
class ExtractionSpec:
    """What one document type needs `parse_label_value` (and, if declared,
    `parse_table_rows`) to look for."""

    authority: str | None
    fields: tuple[ExtractionField, ...]
    table: TableSpec | None = None

    @property
    def label_spec(self) -> LabelSpec:
        """The `LabelSpec` dict `DocumentExtractor.extract()` already
        consumes unchanged — the whole point of this module is that nothing
        downstream of this property needs to know the spec came from data."""
        return {
            f.name: LabelField(f.label, f.type, required=f.required) for f in self.fields
        }


def _parse_fields(raw: str) -> tuple[ExtractionField, ...]:
    out: list[ExtractionField] = []
    seen: set[str] = set()

    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        m = _FIELD_RE.match(token)
        if not m:
            raise SpecError(
                f"field {token!r} must be name:type[!]:\"Label Text\""
            )
        name = m.group("name")
        type_name = m.group("type")
        if not _NAME_RE.match(name):
            raise SpecError(f"field name {name!r} must match [a-z][a-z0-9_]*")
        if type_name not in FIELD_TYPES:
            raise SpecError(
                f"field {name!r} has unknown type {type_name!r}; "
                f"known types: {', '.join(sorted(FIELD_TYPES))}"
            )
        if name in seen:
            raise SpecError(f"field {name!r} declared twice")
        seen.add(name)
        out.append(
            ExtractionField(
                name=name, type=type_name, required=m.group("required") is not None,
                label=m.group("label"),
            )
        )

    return tuple(out)


def _parse_table_columns(raw: str) -> tuple[TableColumn, ...]:
    out: list[TableColumn] = []
    seen: set[str] = set()
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        m = _COLUMN_RE.match(token)
        if not m:
            raise SpecError(f"table column {token!r} must be name:type")
        name, type_name = m.group("name"), m.group("type")
        if type_name not in FIELD_TYPES:
            raise SpecError(
                f"table column {name!r} has unknown type {type_name!r}; "
                f"known types: {', '.join(sorted(FIELD_TYPES))}"
            )
        if name in seen:
            raise SpecError(f"table column {name!r} declared twice")
        seen.add(name)
        out.append(TableColumn(name=name, type=type_name))
    return tuple(out)


def _parse_table(clauses: dict[str, str]) -> TableSpec | None:
    """`table_pattern`/`table_columns` are both-or-neither: a regex with no
    declared columns has nothing to coerce its groups against, and declared
    columns with no regex have no row to apply them to."""
    has_pattern = "table_pattern" in clauses
    has_columns = "table_columns" in clauses
    if not has_pattern and not has_columns:
        return None
    if has_pattern != has_columns:
        raise SpecError("table_pattern and table_columns must both be declared, or neither")

    raw_pattern = clauses["table_pattern"]
    if not (raw_pattern.startswith('"') and raw_pattern.endswith('"') and len(raw_pattern) >= 2):
        raise SpecError('table_pattern must be a quoted regex: table_pattern="..."')
    try:
        pattern = re.compile(raw_pattern[1:-1])
    except re.error as exc:
        raise SpecError(f"table_pattern is not a valid regex: {exc}") from None

    columns = _parse_table_columns(clauses["table_columns"])
    if not columns:
        raise SpecError("table_columns must declare at least one column")

    group_names = set(pattern.groupindex)
    column_names = {c.name for c in columns}
    if group_names != column_names:
        raise SpecError(
            f"table_pattern's named groups {sorted(group_names)} must exactly match "
            f"table_columns {sorted(column_names)}"
        )

    return TableSpec(pattern=pattern, columns=columns)


def parse_extraction_spec(raw: str) -> ExtractionSpec:
    """Parse one `extraction_spec` cell. Raises `SpecError` on anything odd.

    Strict rather than forgiving, same reasoning `parse_contract` gives: an
    unrecognised clause is an error, not something to skip silently.
    """
    clauses: dict[str, str] = {}
    for chunk in (c.strip() for c in raw.split(";")):
        if not chunk:
            continue
        key, sep, value = chunk.partition("=")
        key = key.strip()
        if not sep:
            raise SpecError(f"clause {chunk!r} must be key=value")
        if key not in _CLAUSE_KEYS:
            raise SpecError(
                f"unknown clause {key!r}; known clauses: {', '.join(sorted(_CLAUSE_KEYS))}"
            )
        if key in clauses:
            raise SpecError(f"clause {key!r} declared twice")
        clauses[key] = value.strip()

    if "fields" not in clauses:
        raise SpecError("spec must declare fields (may be empty: fields=)")

    authority = clauses.get("authority") or None
    fields = _parse_fields(clauses["fields"])
    table = _parse_table(clauses)

    return ExtractionSpec(authority=authority, fields=fields, table=table)


__all__ = [
    "ExtractionField", "ExtractionSpec", "SpecError", "TableColumn", "TableSpec",
    "parse_extraction_spec",
]
