"""Deriving a validity window from a "FY24-25"-shaped column header.

`SHAREHOLDING_PATTERN`'s table carries two %-holding columns headed
`"% Holding (FY24-25)"` and `"% Holding (FY23-24)"` — each is a fact valid for
one specific financial year, not "current." `KMP_LIST`'s `"Annual Report FY
2024-25"` will later derive `as_of_date` the same way.

**Why this is a function taking `start_month`, not a hardcoded April.** This
whole repo's tax vocabulary — GSTIN, HSN, `tax_rate`'s domain — is India-only
by design, but a financial year's START MONTH specifically is a policy
choice a future non-India deployment would need to change, and there is no
reason to make that a code edit when it can be a config value
(`Settings.fiscal_year_start_month`, `src/core/config.py`). This module is
therefore pure: it takes `start_month` as an explicit argument everywhere and
never reads `Settings` itself, so it is testable against any convention and
the caller (an extractor) is the only place that ever reads the config.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Final

#: "FY24-25", "FY 2024-25", "FY2024-25", "2024-25" — a two-digit or
#: four-digit start year, a separator, and a two-digit end year. The end
#: year's last two digits are checked against the start year's own last two
#: digits + 1 at parse time (not by the regex) — "24-25" is a financial year,
#: "24-99" is not, and a regex cannot express "one more than" on its own.
_FY_RE: Final = re.compile(
    r"\bFY\s*(?P<start>\d{2}|\d{4})[\s\-–]+(?P<end>\d{2})\b", re.IGNORECASE  # noqa: RUF001
)


def parse_fy_label(raw: str) -> tuple[int, int] | None:
    """The `(start_year, end_year)` a "FYxx-yy" token names, as full 4-digit
    years — or `None` if `raw` contains no such token.

    `None`, not an exception: a caller deciding "no FY here" from an absent
    match is normal, the same reasoning `labelvalue.py`'s `_find_value`
    returning `None` already establishes for a missing label — an error is
    for a token that WAS found and did not make sense, not for one that was
    never there.
    """
    m = _FY_RE.search(raw)
    if m is None:
        return None

    start_raw = m.group("start")
    start_year = int(start_raw) if len(start_raw) == 4 else 2000 + int(start_raw)
    end_two_digit = int(m.group("end"))

    # The end year must be exactly one more than the start year's own last two
    # digits — "FY24-25" is real, "FY24-99" is not a financial year at all,
    # just two numbers this regex happened to also match.
    if end_two_digit != (start_year + 1) % 100:
        return None

    return start_year, start_year + 1


def fy_bounds(start_year: int, end_year: int, *, start_month: int) -> tuple[dt.date, dt.date]:
    """The inclusive validity window `[valid_from, valid_to]` one financial
    year covers, given where in the calendar it starts.

    `valid_to` is the day BEFORE the next cycle's start — April-starting FY
    2024-25 with `start_month=4` runs 2024-04-01 through 2025-03-31, matching
    `entity_master_record`'s own "the date the facts are true as of" framing.
    `end_year` is taken from the caller rather than re-derived here, because
    `parse_fy_label` already resolved the "+1" rule once; recomputing it here
    would be a second place that rule could disagree with the first.
    """
    valid_from = dt.date(start_year, start_month, 1)
    valid_to = dt.date(end_year, start_month, 1) - dt.timedelta(days=1)
    return valid_from, valid_to


def fy_containing(as_of: dt.date, *, start_month: int) -> tuple[int, int]:
    """The `(start_year, end_year)` of the financial year `as_of` falls in.

    Used rather than re-parsing "FY24-25" out of the table's column-header
    text: `SHAREHOLDING_PATTERN`'s two %-holding columns are always "the
    document's own financial year" and "the one before it," in that fixed
    order, and the document's `as_of_date` already names the first one —
    re-deriving it from a second, position-dependent regex pass over the
    header line would be a second place this fact could disagree with the
    first, for no more coverage than this function already gives.
    """
    start_year = as_of.year if as_of.month >= start_month else as_of.year - 1
    return start_year, start_year + 1


__all__ = ["fy_bounds", "fy_containing", "parse_fy_label"]
