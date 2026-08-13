"""The shared `Label: Value` grammar.

`HANDOFF-2026-08-11.md` confirmed all 50 specimen PDFs are clean,
machine-generated documents with one fact per line in `Label: Value` form:

    LUT ARN: AD2704240123456
    GSTIN: 27AABCM4521F1Z5
    Validity: 01 April 2024 to 31 March 2025

This module is the one parser for that shape, driven by a per-document-type
`LabelSpec` (`{field_name: LabelField(label, type, required)}`) rather than
50 hand-rolled regexes — the same reasoning `src/silver/registers/spec.py`
gives for `RegisterSpec`: "adding the Nth type" should be a data problem.

Three outcomes, named rather than scored (`TODO.md`'s agreed design,
2026-08-06): a field parser either bound its required fields or it did not,
and a float confidence threshold would be a number nobody could defend to an
auditor.

    Extracted(fields)                    every required field bound, no
                                          coercion failures
    Partial(fields, missing, failed)     the file has SOME facts but is
                                          missing a required label or a value
                                          failed to coerce to its declared type
    NoTextLayer                          nothing to parse at all

Types are `src/silver/contract.py`'s `ATTR_TYPES` vocabulary (`text`, `date`,
`money`, `int`, `gstin`, `hsn`) so date/money/GSTIN parsing is not invented a
third time in this codebase — plus one addition, `date_range`, for the
`"X to Y"` validity-window lines several archetype-3 documents use. That is a
genuine extension beyond `ATTR_TYPES` (which has no notion of a value that is
two dates), not a duplicate of it; `parse_contract` never sees this module's
type set, so the two vocabularies cannot drift into disagreement about a name
they both claim.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final

#: Recognised field types. `src/silver/contract.py`'s ATTR_TYPES minus the
#: archetype-1/2-only ones (`qty`, `rate`, `hash64`) that no label:value
#: document in this batch uses, plus `date_range` — see module docstring.
FIELD_TYPES: Final[frozenset[str]] = frozenset(
    {"text", "date", "money", "int", "gstin", "hsn", "date_range"}
)

_GSTIN_RE = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]")
_MONEY_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_INT_RE = re.compile(r"\d+")
_HSN_RE = re.compile(r"\d{4,10}")

#: "12 May 2022", "14-Mar-2015", "02 April 2024", "2024-25" is NOT a date and
#: must not match this — it is a financial year and belongs to a `text`
#: field. Two forms cover every specimen: "DD Month YYYY" and "DD-Mon-YYYY".
#: Matched with `search`, not a full-string parse: a validity line's second
#: half is routinely "30 September 2026 (5 years, renewable)" — trailing
#: prose that is not part of the date and must not make parsing fail.
_DATE_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}"), "%d %B %Y"),
    (re.compile(r"\d{1,2}-[A-Za-z]{3}-\d{4}"), "%d-%b-%Y"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
)

#: Splits "01 April 2024 to 31 March 2025 (5 years, renewable)" into its two
#: date-ish halves. Deliberately only looks for the FIRST " to " — a date
#: range never contains a second one in these specimens, and trailing prose
#: ("(5 years, renewable)") is discarded once both dates are found.
_RANGE_SPLIT_RE = re.compile(r"\bto\b", re.IGNORECASE)


class LabelValueError(ValueError):
    """A field's raw text does not parse as its declared type. Caught by
    `_parse_field`, turned into a `failed` entry rather than propagated —
    a coercion failure is a normal, expected outcome of a real-world PDF,
    not a bug in this module."""


def _parse_date(raw: str) -> dt.date:
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(raw)
        if m is None:
            continue
        try:
            return dt.datetime.strptime(m.group(0), fmt).date()
        except ValueError:
            continue
    raise LabelValueError(f"{raw!r} does not match a known date format")


def _parse_date_range(raw: str) -> tuple[dt.date, dt.date]:
    parts = _RANGE_SPLIT_RE.split(raw, maxsplit=1)
    if len(parts) != 2:
        raise LabelValueError(f"{raw!r} does not contain a ' to ' date range")
    return _parse_date(parts[0]), _parse_date(parts[1])


def _parse_money(raw: str) -> str:
    """Indian lakh/crore-grouped rupee amount -> a plain decimal STRING.

    Returned as `str`, not `Decimal`, because the caller stores it into a
    `jsonb` scope/obligation blob (money as a first-class typed column is an
    archetype-1/2 concern, not this one) — a `str` round-trips through
    `json.dumps` without a custom encoder, a `Decimal` does not.
    """
    match = _MONEY_RE.search(raw)
    if match is None:
        raise LabelValueError(f"{raw!r} contains no number")
    return match.group(0).replace(",", "")


def _parse_int(raw: str) -> int:
    match = _INT_RE.search(raw)
    if match is None:
        raise LabelValueError(f"{raw!r} contains no integer")
    return int(match.group(0))


def _parse_gstin(raw: str) -> str:
    match = _GSTIN_RE.search(raw.replace(" ", "").upper())
    if match is None:
        raise LabelValueError(f"{raw!r} does not contain a GSTIN")
    return match.group(0)


def _parse_hsn(raw: str) -> str:
    match = _HSN_RE.search(raw)
    if match is None:
        raise LabelValueError(f"{raw!r} contains no HSN-shaped code")
    return match.group(0)


def _coerce(field_type: str, raw: str) -> object:
    match field_type:
        case "text":
            return raw.strip()
        case "date":
            return _parse_date(raw)
        case "date_range":
            return _parse_date_range(raw)
        case "money":
            return _parse_money(raw)
        case "int":
            return _parse_int(raw)
        case "gstin":
            return _parse_gstin(raw)
        case "hsn":
            return _parse_hsn(raw)
        case other:  # pragma: no cover — guarded by LabelField.__post_init__
            raise LabelValueError(f"unknown field type {other!r}")


@dataclass(frozen=True, slots=True)
class LabelField:
    """One label this document is expected to carry.

    `label` is matched against the start of a line, case-insensitively, up
    to its trailing colon — `"LUT ARN"` matches the line `"LUT ARN:
    AD2704240123456"`. Matching is per-LINE, not across a page: the specimen
    set reflows each fact onto its own line (`HANDOFF-2026-08-11.md`), and a
    label whose value wraps would legitimately fail to bind here — a real,
    named `Partial`, not a bug to paper over with a multi-line regex.
    """

    label: str
    type: str
    required: bool = True

    def __post_init__(self) -> None:
        if self.type not in FIELD_TYPES:
            raise ValueError(
                f"{self.label!r} has unknown field type {self.type!r};"
                f" known types: {', '.join(sorted(FIELD_TYPES))}"
            )


LabelSpec = dict[str, LabelField]


@dataclass(frozen=True, slots=True)
class Extracted:
    """Every required field bound, every value coerced cleanly."""

    fields: dict[str, object]


@dataclass(frozen=True, slots=True)
class Partial:
    """Some facts bound; at least one required field is missing or a value
    failed to coerce. Named fields, not a count — so a future LLM tier would
    know exactly what to look for rather than re-extracting everything
    (`TODO.md`'s agreed design)."""

    fields: dict[str, object]
    missing: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NoTextLayer:
    """Nothing to parse — no native text layer, or near-empty extracted
    text. The scanned-PDF case; see `src/extraction/reader.py`."""


ExtractionOutcome = Extracted | Partial | NoTextLayer


def _find_value(lines: list[str], label: str) -> str | None:
    """The remainder of the FIRST line starting with `label:` (or `label :`),
    case-insensitively. `None` if no such line exists.

    First match, not last: no specimen repeats a label with two different
    values, and preferring the first keeps this deterministic if one ever
    does (a corrected figure restated in a footnote must not silently win
    over the primary declaration).
    """
    prefix_re = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.*)$", re.IGNORECASE)
    for line in lines:
        m = prefix_re.match(line)
        if m:
            return m.group(1).strip()
    return None


def parse_label_value(pages: list[str], spec: LabelSpec) -> ExtractionOutcome:
    """Parse a PDF's per-page text against one document type's label spec.

    Reused verbatim by every archetype base class (`src/extraction/base.py`)
    that reads a header block of `Label: Value` facts — archetypes 3, 6, 7,
    and the header portion of 4/8's contract-derived rows. What differs per
    document type is only the spec, never this function.
    """
    text = "\n".join(pages)
    if len(text.strip()) < 20:
        return NoTextLayer()

    lines = text.splitlines()
    fields: dict[str, object] = {}
    missing: list[str] = []
    failed: list[str] = []

    for name, lf in spec.items():
        raw = _find_value(lines, lf.label)
        if raw is None or not raw:
            if lf.required:
                missing.append(name)
            continue
        try:
            fields[name] = _coerce(lf.type, raw)
        except LabelValueError:
            if lf.required:
                failed.append(name)
            # An optional field that fails to coerce is simply absent —
            # there is nothing to escalate for a fact the document type
            # never promised.

    if missing or failed:
        return Partial(fields=fields, missing=tuple(missing), failed=tuple(failed))
    return Extracted(fields=fields)


__all__ = [
    "Extracted",
    "ExtractionOutcome",
    "LabelField",
    "LabelSpec",
    "LabelValueError",
    "NoTextLayer",
    "Partial",
    "parse_label_value",
]
