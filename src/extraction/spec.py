"""The per-document-type extraction spec, parsed from the registry.

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

A field token is `name:type[!]:"Label Text"` — the same `name:type!`
shorthand `field_contract` already uses, with a quoted PDF label appended.
Types are `src/extraction/labelvalue.py`'s `FIELD_TYPES` vocabulary (`text`,
`date`, `date_range`, `money`, `int`, `gstin`, `hsn`) — reused, not
reinvented, so this grammar cannot drift from what `parse_label_value`
actually accepts.
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
_CLAUSE_KEYS: Final[frozenset[str]] = frozenset({"authority", "fields"})

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
class ExtractionSpec:
    """What one document type needs `parse_label_value` to look for."""

    authority: str | None
    fields: tuple[ExtractionField, ...]

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

    return ExtractionSpec(authority=authority, fields=fields)


__all__ = ["ExtractionField", "ExtractionSpec", "SpecError", "parse_extraction_spec"]
