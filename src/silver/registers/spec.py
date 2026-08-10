"""RegisterSpec — what one flat A1 register looks like, declared once.

TYPED-TABLES-PLAN.md build order step 5. `src/silver/sales_register.py` is the
reviewed reference pattern and stays hand-written; A1.01 is the only type with a
header/line split, and the split is the whole reason it is hand-written.

The other 23 are line-grain and flat (tenant migration 010's header says so, and
`reference/inafin_a1_schema.sql` is where it comes from). Copying
`sales_register.py` 23 times would be ~11,000 lines in which every type's parse,
hash, supersede and insert differ only by a column list — and a bug fixed in one
would survive in twenty-two.

**THE RISK THIS TRADES INTO, stated plainly.** A declarative spec can drift from
the DDL it describes: rename a column in a migration and the spec still names the
old one, and nothing fails until a real file arrives. That is a worse failure
than the duplication it avoids, so it is not merely accepted —
`tests/conformance/test_register_specs.py::test_spec_matches_the_table` reads
`information_schema` and `pg_index` on a live tenant schema and asserts, per
type: the column set, the column ORDER, the required flags, the type kinds, the
period column, and the unique index's columns. The spec is not the source of
truth; the table is, and the gate says so.

Two things are deliberately NOT in the spec:

  * **CHECK constraint vocabularies** (`note_type IN ('CN','DN')`, the six
    `category` values, …). Re-declaring them here would be a second copy of a
    rule the database already enforces, free to disagree with it. A bad value is
    rejected by Postgres with the constraint name in the message, which is a
    better error than one this layer could invent.

  * **The GSTIN shape.** `platform_ref.gstin` is a domain. Same argument, and
    `test_gstin_domain_accepts_valid_registrations` already covers the case
    (TDS/TCS registrations) that a hand-rolled regex here would get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Envelope columns. Ours, not the reference's — tenant migration 010's header
# explains each. Named here because the spec's business columns are defined as
# "everything that is not one of these", and the DDL gate subtracts exactly this
# set before comparing.
ENVELOPE_COLUMNS = frozenset(
    {
        "id",
        "entity_id",
        "gstin",
        "tax_period",
        "fy",
        "doc_type_code",
        "batch_id",
        "bronze_ingest_id",
        "row_hash",
        "valid_from",
        "valid_to",
        "recorded_at",
        "superseded_at",
        "created_at",
        "created_by",
        "modified_at",
        "modified_by",
    }
)

# The envelope values a key column may name, as opposed to a business column.
# Both natural keys and content keys are indexed on some of these.
ENVELOPE_KEY_COLUMNS = frozenset({"entity_id", "gstin", "tax_period", "fy", "row_hash"})


class Kind(StrEnum):
    """How a CSV cell becomes a Python value.

    Coarser than the SQL type on purpose. `money_inr`, `qty` and `tax_rate` are
    three different domains with three different scales and one parse rule —
    `Decimal(raw)` — and the domain, not this enum, is what rejects 0.001 rupees
    or a 200% tax rate. Splitting the enum to mirror the domains would add three
    names that behave identically here and invite the spec to disagree with the
    DDL about which one a column has.
    """

    TEXT = "text"
    DATE = "date"
    DECIMAL = "decimal"
    INTEGER = "integer"
    BOOLEAN = "boolean"


class KeyKind(StrEnum):
    """Which of tenant migration 010's two key strategies this register uses.

    NATURAL — the document carries an identifier, so a resubmitted correction
    supersedes its predecessor.

    CONTENT — the document carries none, so identical content is deduped and a
    correction lands as a NEW row beside the original. That is a limitation of
    these registers, recorded rather than papered over; see the migration header.
    """

    NATURAL = "natural"
    CONTENT = "content"


@dataclass(frozen=True, slots=True)
class Column:
    """One business column.

    ``required`` means the CSV cell must carry a value, and it is derived from
    the DDL by exactly one rule, asserted by the gate:

        required  <=>  NOT NULL and no DEFAULT

    A NOT NULL column WITH a default (`rcm_flag boolean not null default false`)
    is not required, because a blank cell has a defined meaning. A nullable
    column with `default 0` (`cgst platform_ref.money_inr default 0`) is not
    required either, and a blank cell there becomes NULL rather than 0 — the
    default never fires, because the loader names every column in its INSERT.
    That is the reviewed behaviour of `sales_register.py`: absent means the ERP
    made no claim, which is not a claim of zero.
    """

    name: str
    kind: Kind
    required: bool = False


@dataclass(frozen=True, slots=True)
class RegisterSpec:
    """One flat register: its table, its key, and its columns in DDL order."""

    doc_type_codes: tuple[str, ...]
    """Registry codes that load into this table.

    A tuple, not a string, for `platform_settlement_report` alone — seven
    marketplace codes share it (shared migration 013 relaxed the unique index to
    allow it, and records why nothing else qualifies). Where there is one code it
    is the default; where there are seven the caller must say which.
    """

    table: str
    period_column: str
    """``tax_period`` (a date) or ``fy`` (a '2026-27' string).

    Three registers are annual — `audited_pl_account`, `trial_balance`,
    `balance_sheet` — and the reference schema keys them on a financial year
    rather than a month. The loader derives the FY from the batch period and
    refuses a batch that straddles two of them.
    """

    key_kind: KeyKind
    key_columns: tuple[str, ...]
    """Exactly the columns of the table's partial unique index, in index order.

    Not "the business key" in the abstract — the index's columns, so the gate can
    compare them to `pg_index` and a divergence is a test failure rather than a
    duplicate row nobody notices. This is the mutation check the last session
    found a natural-key test failing: pinning the loader's lookup while leaving
    the index free to drift proves nothing.
    """

    columns: tuple[Column, ...]
    valid_from_column: str | None = None
    """Which business date opens the row's validity interval, or None.

    None means "use the batch's period_start", and it is the honest answer
    wherever the register's own dates are nullable (a fixed asset with no
    purchase date, a trial balance with no date at all). Naming a nullable column
    here would produce a NULL into `valid_from NOT NULL` on the first row that
    omits it — a crash at ingestion for a legitimate document.
    """

    def __post_init__(self) -> None:
        if self.period_column not in {"tax_period", "fy"}:
            raise ValueError(f"{self.table}: period_column must be tax_period or fy")

        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.table}: duplicate column name")
        if overlap := sorted(set(names) & ENVELOPE_COLUMNS):
            raise ValueError(f"{self.table}: business column shadows envelope: {overlap}")

        known = set(names) | ENVELOPE_KEY_COLUMNS
        if unknown := sorted(set(self.key_columns) - known):
            raise ValueError(f"{self.table}: key column(s) not on the table: {unknown}")

        if self.valid_from_column is not None:
            column = self.column(self.valid_from_column)
            if column is None or column.kind is not Kind.DATE:
                raise ValueError(
                    f"{self.table}: valid_from_column {self.valid_from_column!r}"
                    " is not a date column"
                )
            if not column.required:
                # valid_from is NOT NULL. A nullable source would crash on the
                # first row that omits it — at ingestion, on a real file.
                raise ValueError(
                    f"{self.table}: valid_from_column {self.valid_from_column!r}"
                    " is nullable; use None to fall back to period_start"
                )

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def required_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.required)

    @property
    def business_key_columns(self) -> tuple[str, ...]:
        """Key columns that are business columns, not envelope ones."""
        return tuple(c for c in self.key_columns if c not in ENVELOPE_KEY_COLUMNS)

    @property
    def default_doc_type_code(self) -> str | None:
        return self.doc_type_codes[0] if len(self.doc_type_codes) == 1 else None
