"""Flat Group A1 register loaders — one spec per type, one loader for all.

TYPED-TABLES-PLAN.md build order step 5. `src/silver/sales_register.py` remains
separate and hand-written: A1.01 is the only A1 type with a header/line split.

    from src.silver.registers import RegisterLoader, spec_for

    spec = spec_for("PURCHASE_REGISTER")
    outcome = await RegisterLoader(pool, spec).load(ctx, ...)
"""

from src.silver.registers.catalog import SPEC_BY_DOC_TYPE, SPEC_BY_TABLE, SPECS, spec_for
from src.silver.registers.loader import (
    ParseResult,
    RegisterLoader,
    RegisterOutcome,
    RegisterRow,
    RowRejection,
    financial_year,
    parse_register_csv,
    parse_register_ndjson,
)
from src.silver.registers.spec import Column, KeyKind, Kind, RegisterSpec

__all__ = [
    "SPECS",
    "SPEC_BY_DOC_TYPE",
    "SPEC_BY_TABLE",
    "Column",
    "KeyKind",
    "Kind",
    "ParseResult",
    "RegisterLoader",
    "RegisterOutcome",
    "RegisterRow",
    "RegisterSpec",
    "RowRejection",
    "financial_year",
    "parse_register_csv",
    "parse_register_ndjson",
    "spec_for",
]
