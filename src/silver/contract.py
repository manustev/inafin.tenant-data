"""The per-document-type field contract, parsed from the registry.

ARCHITECTURE.md 6 sets the bar: adding the twelfth transaction document type
must be a registry row, not a sprint. That is only true if the thing that varies
per type lives as *data*. This module is the grammar for that data.

`registry/document_types.csv` carries one `field_contract` cell per archetype 1
and 2 row, and the runtime reads it back from
`platform_ref.document_type.field_contract`. Two consumers, one grammar, parsed
here — `scripts/gen_registry_seed.py` imports this module precisely so a
contract that would fail at ingestion cannot be committed to the CSV in the
first place.

    direction=OUTWARD;counterparty=OPTIONAL;doc=irn:hash64!,ack_number:text

Clauses, separated by `;`:

    direction      INWARD | OUTWARD | INTERNAL   (mandatory)
    counterparty   REQUIRED | OPTIONAL | FOREIGN (mandatory)
    doc            header attributes, comma-separated  (optional)
    line           line attributes, comma-separated    (optional)

An attribute is `name:type` with a trailing `!` for mandatory. Types are the
cleansing rules in `src/silver/transaction.py`, not storage types — the values
land in a `jsonb` column either way.

**What is deliberately NOT here: ERP column aliasing.** A Tally purchase
register and a SAP one use different headers for the same field, and that
varies by *client*, not by document type. Putting it in the registry would make
the shared vocabulary carry one tenant's spreadsheet conventions. Header mapping
belongs to the connector; the registry declares only the canonical contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class ContractError(ValueError):
    """A field contract is not well formed. Raised at parse time, both in the
    generator (so it cannot be committed) and at ingestion (so a hand-edited
    registry row cannot silently widen what is accepted)."""


class Direction(StrEnum):
    INWARD = "INWARD"
    OUTWARD = "OUTWARD"
    INTERNAL = "INTERNAL"


class Counterparty(StrEnum):
    #: A GSTIN is mandatory — a registered supplier on a purchase register.
    REQUIRED = "REQUIRED"
    #: A GSTIN may be absent. B2C sales and unregistered GTAs are the cases;
    #: absence is a legitimate fact, not a missing field.
    OPTIONAL = "OPTIONAL"
    #: There is no GSTIN to have. An import Bill of Entry names an overseas
    #: supplier, so demanding a GSTIN would reject every valid row — but the
    #: name must then be present, or the counterparty is unidentifiable.
    FOREIGN = "FOREIGN"


#: Cleansing rules an attribute value is put through. Kept small on purpose: a
#: type that appears once is a validation rule wearing a costume.
ATTR_TYPES: Final[frozenset[str]] = frozenset(
    {"text", "date", "money", "qty", "rate", "int", "gstin", "hsn", "hash64"}
)

_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_CLAUSE_KEYS: Final[frozenset[str]] = frozenset({"direction", "counterparty", "doc", "line"})


@dataclass(frozen=True, slots=True)
class Attribute:
    name: str
    type: str
    required: bool


@dataclass(frozen=True, slots=True)
class FieldContract:
    """What one document type adds to the archetype's fixed shape."""

    direction: Direction
    counterparty: Counterparty
    doc_attributes: tuple[Attribute, ...] = ()
    line_attributes: tuple[Attribute, ...] = ()

    @property
    def required_doc_attributes(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.doc_attributes if a.required)

    @property
    def required_line_attributes(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.line_attributes if a.required)

    def doc_attribute(self, name: str) -> Attribute | None:
        return next((a for a in self.doc_attributes if a.name == name), None)

    def line_attribute(self, name: str) -> Attribute | None:
        return next((a for a in self.line_attributes if a.name == name), None)


def _parse_attributes(raw: str, *, clause: str) -> tuple[Attribute, ...]:
    out: list[Attribute] = []
    seen: set[str] = set()

    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        required = token.endswith("!")
        body = token[:-1] if required else token

        name, sep, type_name = body.partition(":")
        if not sep:
            raise ContractError(f"{clause} attribute {token!r} must be name:type")
        if not _NAME_RE.match(name):
            raise ContractError(
                f"{clause} attribute name {name!r} must match [a-z][a-z0-9_]*"
            )
        if type_name not in ATTR_TYPES:
            raise ContractError(
                f"{clause} attribute {name!r} has unknown type {type_name!r}; "
                f"known types: {', '.join(sorted(ATTR_TYPES))}"
            )
        if name in seen:
            raise ContractError(f"{clause} attribute {name!r} declared twice")
        seen.add(name)
        out.append(Attribute(name=name, type=type_name, required=required))

    return tuple(out)


def parse_contract(raw: str) -> FieldContract:
    """Parse one `field_contract` cell. Raises ContractError on anything odd.

    Strict rather than forgiving: an unrecognised clause is an error, not
    something to skip. A typo'd `directon=OUTWARD` that parsed to "no direction
    declared" would silently apply the wrong counterparty rules to a whole
    document type, and the resulting rows would look fine.
    """
    clauses: dict[str, str] = {}
    for chunk in (c.strip() for c in raw.split(";")):
        if not chunk:
            continue
        key, sep, value = chunk.partition("=")
        key = key.strip()
        if not sep:
            raise ContractError(f"clause {chunk!r} must be key=value")
        if key not in _CLAUSE_KEYS:
            raise ContractError(
                f"unknown clause {key!r}; known clauses: {', '.join(sorted(_CLAUSE_KEYS))}"
            )
        if key in clauses:
            raise ContractError(f"clause {key!r} declared twice")
        clauses[key] = value.strip()

    for mandatory in ("direction", "counterparty"):
        if mandatory not in clauses:
            raise ContractError(f"contract must declare {mandatory}")

    try:
        direction = Direction(clauses["direction"])
    except ValueError:
        raise ContractError(
            f"direction {clauses['direction']!r} must be one of "
            f"{', '.join(d.value for d in Direction)}"
        ) from None
    try:
        counterparty = Counterparty(clauses["counterparty"])
    except ValueError:
        raise ContractError(
            f"counterparty {clauses['counterparty']!r} must be one of "
            f"{', '.join(c.value for c in Counterparty)}"
        ) from None

    return FieldContract(
        direction=direction,
        counterparty=counterparty,
        doc_attributes=_parse_attributes(clauses.get("doc", ""), clause="doc"),
        line_attributes=_parse_attributes(clauses.get("line", ""), clause="line"),
    )
