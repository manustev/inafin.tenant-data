"""The repeating-row grammar: `extraction_spec`'s `table_pattern`/
`table_columns` clauses (`src/extraction/spec.py`), `parse_table_rows`
(`src/extraction/tablevalue.py`), and the fiscal-year date derivation
(`src/extraction/fiscal_year.py`) `SHAREHOLDING_PATTERN`'s extractor uses.

Full end-to-end coverage against the real specimen (12 fact rows, resubmit
supersedes) lives in `test_extraction_archetypes_6_7_4_8.py`, alongside the
other archetype-7 types. This file is the grammar/parser unit layer under
that — it does not touch the database.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.extraction.fiscal_year import fy_bounds, fy_containing, parse_fy_label
from src.extraction.spec import SpecError, parse_extraction_spec
from src.extraction.tablevalue import parse_table_rows

pytestmark = pytest.mark.conformance

_SHAREHOLDING_SPEC = (
    'fields=reference_number:text!:"Financial Year",as_of_date:date!:"Financial Year";'
    r'table_pattern="(?P<holder_name>(?:\S+\s+){0,5}?\S+)\s+(?P<shares>[\d,]+)\s+'
    r'(?P<pct_current_fy>[\d.]+)%\s+(?P<pct_prior_fy>[\d.]+)%\s+(?P<pledged>Yes|No)";'
    "table_columns=holder_name:text,shares:money,pct_current_fy:money,"
    "pct_prior_fy:money,pledged:text"
)


# --- fiscal_year.py -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FY24-25", (2024, 2025)),
        ("FY 2024-25", (2024, 2025)),
        ("FY2024-25", (2024, 2025)),
        ("2024–25 (as on 31 March 2025)", None),  # noqa: RUF001 — no leading "FY"
        ("FY24-99", None),  # end year is not start+1 — not a real financial year
        ("Annual Report", None),
    ],
)
def test_parse_fy_label(raw: str, expected: tuple[int, int] | None) -> None:
    assert parse_fy_label(raw) == expected


def test_fy_bounds_april_start() -> None:
    assert fy_bounds(2024, 2025, start_month=4) == (
        dt.date(2024, 4, 1), dt.date(2025, 3, 31),
    )


def test_fy_bounds_is_configurable_not_hardcoded() -> None:
    """A different `start_month` produces a different window — the whole
    point of taking it as an argument rather than assuming April."""
    assert fy_bounds(2024, 2025, start_month=1) == (
        dt.date(2024, 1, 1), dt.date(2024, 12, 31),
    )


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (dt.date(2025, 3, 31), (2024, 2025)),  # last day of FY24-25
        (dt.date(2024, 4, 1), (2024, 2025)),  # first day of FY24-25
        (dt.date(2024, 3, 31), (2023, 2024)),  # last day of the PRIOR fy
    ],
)
def test_fy_containing(as_of: dt.date, expected: tuple[int, int]) -> None:
    assert fy_containing(as_of, start_month=4) == expected


# --- spec.py: table_pattern/table_columns --------------------------------------


def test_table_clause_parses() -> None:
    spec = parse_extraction_spec(_SHAREHOLDING_SPEC)
    assert spec.table is not None
    assert {c.name for c in spec.table.columns} == {
        "holder_name", "shares", "pct_current_fy", "pct_prior_fy", "pledged",
    }


def test_table_clause_absent_is_none() -> None:
    spec = parse_extraction_spec('fields=reference_number:text!:"CIN"')
    assert spec.table is None


def test_table_pattern_without_table_columns_is_rejected() -> None:
    with pytest.raises(SpecError, match="must both be declared"):
        parse_extraction_spec(r'fields=;table_pattern="(?P<x>\d+)"')


def test_table_columns_without_table_pattern_is_rejected() -> None:
    with pytest.raises(SpecError, match="must both be declared"):
        parse_extraction_spec("fields=;table_columns=x:int")


def test_table_pattern_group_column_mismatch_is_rejected() -> None:
    with pytest.raises(SpecError, match="must exactly match"):
        parse_extraction_spec(
            r'fields=;table_pattern="(?P<x>\d+)";table_columns=y:int'
        )


def test_table_pattern_unquoted_is_rejected() -> None:
    with pytest.raises(SpecError, match="quoted regex"):
        parse_extraction_spec(r"fields=;table_pattern=(?P<x>\d+);table_columns=x:int")


def test_table_pattern_invalid_regex_is_rejected() -> None:
    with pytest.raises(SpecError, match="not a valid regex"):
        parse_extraction_spec('fields=;table_pattern="(?P<x>";table_columns=x:int')


# --- tablevalue.py: parse_table_rows -------------------------------------------


def test_parse_table_rows_against_the_real_specimen_shape() -> None:
    """The exact wrapping the real PDF produces — a name split onto its own
    line — reconstructed here so this grammar's line-accumulation behaviour
    is pinned independent of the live PDF file."""
    pages = [
        "Shareholder No. of Shares % Holding \n"
        "(FY24-25)\n"
        "% Holding \n"
        "(FY23-24) Pledged\n"
        "Arvind Rao Deshmukh \n"
        "(Promoter / MD) 18,50,000 37.00% 37.00% No\n"
        "Meridian Family Trust 6,00,000 12.00% 12.00% No\n"
        "Note: Shareholders holding 25% or more of voting share capital...\n"
    ]
    spec = parse_extraction_spec(_SHAREHOLDING_SPEC)
    assert spec.table is not None
    rows = parse_table_rows(pages, spec.table)
    assert [r["holder_name"] for r in rows] == [
        "Arvind Rao Deshmukh (Promoter / MD)", "Meridian Family Trust",
    ]
    assert rows[0]["shares"] == "1850000"
    assert rows[0]["pct_current_fy"] == "37.00"


def test_parse_table_rows_a_malformed_row_does_not_corrupt_the_next_one() -> None:
    """A row whose percentage does not coerce (garbage, not just out of
    range — `money`'s own coercion is what this exercises) is dropped, and
    must not bleed into the FOLLOWING valid row's name — the failure mode
    an earlier version of the look-back merge had."""
    pages = [
        "Meridian Family Trust 6,00,000 NOT_A_NUMBER% 12.00% No\n"
        "Zenith Growth Capital Fund II 8,75,000 17.50% 17.50% No\n"
    ]
    spec = parse_extraction_spec(_SHAREHOLDING_SPEC)
    assert spec.table is not None
    rows = parse_table_rows(pages, spec.table)
    assert [r["holder_name"] for r in rows] == ["Zenith Growth Capital Fund II"]


_GSTIN_SPEC = (
    'fields=;'
    r'table_pattern="(?P<gstin>[0-9]{2}\s*[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\s*[1-9A-Z]\s*Z\s*[0-9A-Z])\s+'
    r'(?P<state>.+?)\s+(?P<registration_type>Regular|Composition)\s+'
    r'(?P<effective_date>[0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})\s+'
    r'(?P<status>Active|Suspended\s*\(\s*[0-9]{1,2}-[A-Za-z]{3}-\s*[0-9]{4}\s*\))\s+'
    r'(?P<principal_place>(?:(?![0-9]{2}\s*[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\s*[1-9A-Z]\s*Z\s*[0-9A-Z]).)+)";'
    "table_columns=gstin:gstin,state:text,registration_type:text,effective_date:date,"
    "status:text,principal_place:text"
)


def test_parse_table_rows_reconstructs_a_gstin_split_mid_code_across_a_line() -> None:
    """The real `GSTIN_REGISTER` specimen wraps a GSTIN mid-code —
    `"27AABCM4521F1"` / `"Z5"` — with no delimiter joining the two
    fragments. `table_spec.pattern`'s `\\s*` tolerance plus `coerce_field_
    value`'s own whitespace-stripping (`labelvalue._parse_gstin`) is what
    reconstructs the GSTIN whole; this pins that end-to-end without needing
    the real PDF."""
    pages = [
        "GSTIN State Registration \n"
        "Type\n"
        "Effective \n"
        "Date\n"
        "Status (as on 31-\n"
        "Mar-2025)\n"
        "27AABCM4521F1\n"
        "Z5 Maharashtra Regular 14-Mar-2015 Active Head Office —\n"
        "Mumbai\n"
    ]
    spec = parse_extraction_spec(_GSTIN_SPEC)
    assert spec.table is not None
    rows = parse_table_rows(pages, spec.table)
    assert len(rows) == 1
    assert rows[0]["gstin"] == "27AABCM4521F1Z5"
    assert rows[0]["state"] == "Maharashtra"
