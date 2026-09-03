"""The shared repeating-row grammar — the colon-less counterpart to
`labelvalue.py`'s `Label: Value` binding.

A document's real content is sometimes a TABLE, not a set of header facts:
`SHAREHOLDING_PATTERN`'s 6 shareholders was the first case built against this
grammar (see `src/extraction/fiscal_year.py` and tenant migration 032). This
module is the parsing side; `entity_master_types.py` writes the result.

**Why one regex per row, not a generic column-splitter.** `pypdf` flattens a
table into plain lines with no column delimiter —
`"Arvind Rao Deshmukh 18,50,000 37.00% 37.00% No"` — so there is no reliable
generic split rule (a holder's name is itself multiple whitespace-separated
words). A named-group regex per document type, declared as
`extraction_spec`'s `table_pattern`/`table_columns` clauses
(`src/extraction/spec.py`), is the same "regex as declared metadata" idiom
`field_constraints.py`'s `pattern` column already uses.

**Why the algorithm reconstructs a row from a WINDOW of physical lines, not
one line with a one-line look-back.** Verified against 4 real specimens
(`SHAREHOLDING_PATTERN`, `GSTIN_REGISTER`, `DIRECTOR_LIST_WITH_DIN`,
`KMP_LIST`): a table row routinely wraps across 2-5 physical lines, and a
value can wrap MID-TOKEN (`GSTIN_REGISTER`'s own GSTIN splits as
`"27AABCM4521F1"` / `"Z5"` across a line break, with the second fragment
itself containing no separating delimiter). A single-fragment look-back
cannot reconstruct this; `_locate_row` instead tries, for each starting
physical line, every window from 1 line up to `max_window_lines`, joined with
a single space, and takes the SHORTEST window that `table_pattern` fullmatches
— "shortest, not longest" specifically because a later free-text column
(anchored only by "not the next row's anchor token", never by a fixed length)
would otherwise happily keep absorbing every following row's text into one
match (verified: an unbounded "longest window wins" strategy merged 3
GSTIN rows into one before this was caught).

**Why an unmatched leading line does not corrupt the next row.** A table's
own header row, split across several physical lines with no delimiter either,
is exactly the same shape as a wrapped DATA row from this algorithm's point
of view — nothing here can tell "a row's own name wrapped" apart from "the
column header text above it" by shape alone. Two real, verified defenses
exist instead: (1) `_locate_row` is tried starting at EVERY physical line, so
a header line that never anchors is simply skipped, one line at a time, not
accumulated without bound; (2) a leading free-text column (a name) is
declared in `table_pattern` bounded to a small number of words (see
`registry/document_types.csv`'s `SHAREHOLDING_PATTERN`/`GSTIN_REGISTER`/
`DIRECTOR_LIST_WITH_DIN`/`KMP_LIST` rows, `(?:\\S+\\s+){0,5}?\\S+`) — a
genuine person/entity name is realistically a handful of words, so this
bound, chosen from the real specimens' own longest names (not tuned to make
one test pass), keeps a header's much longer word run from ever satisfying
the pattern by masquerading as a name. Both together were needed: neither
alone was sufficient against `DIRECTOR_LIST_WITH_DIN`'s real header, whose
final wrapped word ("Active") is short enough to slip under any believable
name-length bound — that case additionally needed a small, grounded negative
lookahead naming the document's OWN observed header words (not an invented
vocabulary; see that type's `table_pattern`).

**`RELATED_PARTY_REGISTER` is deliberately NOT built against this grammar.**
Its last two columns (nature of relationship, basis of relationship) are both
open-ended free prose with no delimiter and no anchor between them — verified
against the real specimen: every row-boundary strategy tried left a fragment
of one row's trailing prose bleeding into the next row's name, corrupting
data rather than merely dropping a row. Per this repo's own rule ("never
invent a vocabulary or a payload shape with no specimen... reject rather than
guess" — see CLAUDE.md), this is a genuine, open gap, not a design this
module papers over; captured in `SESSION-HISTORY.md`/`TODO.md`'s backlog for
a future PDF-layout-aware approach (real column boundaries, not flattened
text) rather than forced here.

**Why a row that fails to coerce is silently dropped, not escalated.** An
earlier version of this module tried to tell "a genuinely malformed row"
apart from "unrelated prose that happens to contain a digit" and that
heuristic was WRONG (see git history) — a stronger per-type-declared signal
does not exist yet, so, same as the `Label: Value` grammar, a window that
never cleanly matches is simply not a row.
"""

from __future__ import annotations

import logging
import re

from src.extraction.labelvalue import LabelValueError, coerce_field_value
from src.extraction.spec import TableSpec

logger = logging.getLogger(__name__)

#: How many physical lines a single row is allowed to reconstruct from.
#: Chosen from the real specimens' worst case (`GSTIN_REGISTER`'s Haryana
#: row: GSTIN split across a line, then state/type/date/status, then a
#: further-wrapped suspension date, then a wrapped principal-place value —
#: 5 physical lines) with headroom, not tuned to exactly one row.
_MAX_WINDOW_LINES = 8


def parse_table_rows(pages: list[str], table_spec: TableSpec) -> list[dict[str, object]]:
    """Every row of `table_spec`'s table, coerced per its declared columns,
    in the order the rows appear.

    For each physical line in turn, tries progressively longer windows of
    following lines (joined with a single space) and accepts the SHORTEST one
    `table_spec.pattern` fullmatches — see this module's docstring for why
    shortest-not-longest and why a starting line that can never anchor a row
    (a header, a title, a footer note) is simply skipped rather than
    accumulated. A window whose regex matches but whose typed coercion fails
    on any column is treated the same as no match at all: the window is
    rejected outright (not partially accepted), and the SAME starting line is
    retried with a longer window in case a real row shape still lies further
    ahead — the coercion failure does not stop the scan.
    """
    text = "\n".join(pages)
    physical = [line.strip() for line in text.splitlines() if line.strip()]
    rows: list[dict[str, object]] = []

    i = 0
    n = len(physical)
    while i < n:
        end = _locate_row(physical, i, table_spec)
        if end is None:
            i += 1
            continue
        candidate = " ".join(physical[i:end])
        match = table_spec.pattern.fullmatch(candidate)
        assert match is not None  # _locate_row only returns a confirmed match

        row: dict[str, object] = {}
        ok = True
        for column in table_spec.columns:
            raw = match.group(column.name)
            try:
                row[column.name] = coerce_field_value(column.type, raw)
            except LabelValueError:
                logger.warning(
                    "table row column %r failed to coerce as %r: %r",
                    column.name, column.type, raw,
                )
                ok = False
                break
        if ok:
            rows.append(row)
        i = end

    return rows


def _locate_row(physical: list[str], start: int, table_spec: TableSpec) -> int | None:
    """The exclusive end index of the SHORTEST window starting at `start`
    whose joined text `table_spec.pattern` fullmatches AND whose columns all
    coerce cleanly — or `None` if no window up to `_MAX_WINDOW_LINES` does."""
    n = len(physical)
    for end in range(start + 1, min(n, start + _MAX_WINDOW_LINES) + 1):
        candidate = " ".join(physical[start:end])
        match = table_spec.pattern.fullmatch(candidate)
        if match is None:
            continue
        if _coerces_cleanly(match, table_spec):
            return end
    return None


def _coerces_cleanly(match: re.Match[str], table_spec: TableSpec) -> bool:
    for column in table_spec.columns:
        try:
            coerce_field_value(column.type, match.group(column.name))
        except LabelValueError:
            return False
    return True


__all__ = ["parse_table_rows"]
