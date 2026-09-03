"""Archetype 7 — the seven entity/counterparty master types.

Corrective refactor (2026-08-11): the seven hand-written `EntityMasterExtractor`
subclasses this module used to hold are gone — their `doc_type_code` +
`label_spec` now live as data in `registry/document_types.csv`'s
`extraction_spec` column, read at runtime by `src/extraction/registry.py`'s
`build_extractor_registry`. `EntityMasterExtractor` (`src/extraction/base.py`)
is instantiated directly for the remaining, header-only types.

**Header-fact binding, updated 2026-09-02.** `SHAREHOLDING_PATTERN` and
`DIRECTOR_LIST_WITH_DIN`'s `as_of_date` were mislabeled contracts, not
genuine tier-1 gaps (shared migrations 048/050) — both now extract
`Extracted`, not `Partial`. `GSTIN_REGISTER` and `KMP_LIST` remain named
`Partial(missing=("as_of_date",))`: neither specimen states its own as-of
date on any colon-bearing line at all (`GSTIN_REGISTER`'s date sits inside a
table COLUMN HEADER, `KMP_LIST` states only a reporting period, "Annual
Report FY 2024-25" — a fiscal-year-end convention, not a stated date). Both
are genuine product decisions, not label fixes, and are open; the TABLE
content below is captured regardless of this header gap.

**Table-shaped types, 2026-09-02/03.** Four of the seven types' real content
is a PDF table, not header facts — `entity_master_record.details jsonb`
existed for that content and sat empty since the table was created, because
nothing ever turned a table row into a fact. `TableFactExtractor` below is
the generic base every table-shaped type but one now uses: `doc_type_code`/
`label_spec`/`table_spec` come from the registry (`extraction_spec`'s
`table_pattern`/`table_columns` clauses, `src/extraction/spec.py`), rows come
from `src/extraction/tablevalue.py`'s `parse_table_rows`, and each row is
written as-is via `entity_master_fact.py`'s generic writer —
`GSTIN_REGISTER`, `DIRECTOR_LIST_WITH_DIN`, `KMP_LIST` are exactly this,
differing only in `_fact_table` and their registry-declared columns.
`SHAREHOLDING_PATTERN` is the one exception, kept as its own subclass, because
its ONE-PDF-ROW-TO-TWO-FACTS financial-year expansion is real business
logic, not a straight row-to-fact write — the same "what computes stays code"
reasoning `BillOfEntryExtractor._csv_row` (`transaction_types.py`) already
gives for archetype 1's bespoke subclasses.

**`RELATED_PARTY_REGISTER` has no table-shaped extractor.** Its real
content is ALSO a table, but its last two columns are open-ended free prose
with no delimiter or anchor between them — verified against the real
specimen, every row-boundary strategy tried corrupted data (one row's
trailing prose bled into the next row's name) rather than merely dropping a
row. See `src/extraction/tablevalue.py`'s module docstring for the specifics.
This is a genuine, open gap (`SESSION-HISTORY.md`/`TODO.md`), not something
forced here against ground truth the specimen does not support.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal, InvalidOperation
from typing import cast

import psycopg

from src.core.config import get_settings
from src.core.pool import TenantScopedPool
from src.core.tenant import TenantContext
from src.extraction.base import EntityMasterExtractor
from src.extraction.fiscal_year import fy_bounds, fy_containing
from src.extraction.labelvalue import (
    Extracted,
    ExtractionOutcome,
    LabelSpec,
    Partial,
    parse_label_value,
)
from src.extraction.spec import TableSpec
from src.extraction.tablevalue import parse_table_rows
from src.provisioning.objectstore import ObjectStorePort
from src.silver.entity_master_fact import FactRowEnvelope, close_prior_fact_rows, record_fact_rows

#: Each table-shaped type's own fact table name — a fixed identifier, not
#: registry data, same reasoning `_ARCHETYPE1_ENVELOPE_TABLES`-style constants
#: elsewhere in this codebase give: WHICH table a type's facts land in does
#: not vary per tenant or change at runtime, so it is not something
#: `extraction_spec` needs to declare.
_FACT_TABLE = "shareholding_pattern_holder"

#: The two table columns `parse_table_rows` returns that are financial-year
#: percentages, and the row key naming each — see `table_pattern`'s named
#: groups in `registry/document_types.csv`'s SHAREHOLDING_PATTERN cell.
_CURRENT_FY_COLUMN = "pct_current_fy"
_PRIOR_FY_COLUMN = "pct_prior_fy"


class TableFactExtractor(EntityMasterExtractor):
    """The generic table-shaped archetype-7 extractor: parse the header
    facts, parse the table via `table_spec`, write each row AS-IS to the
    subclass's `_fact_table` — no per-type Python beyond naming that table.

    Every table-shaped type except `SHAREHOLDING_PATTERN` (which computes a
    financial-year expansion, real business logic — see
    `ShareholdingPatternExtractor` below) fits this shape directly.
    """

    #: Set by each subclass — the tenant migration's table name.
    _fact_table: str = ""

    def __init__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        table_spec: TableSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> None:
        super().__init__(
            pool, doc_type_code=doc_type_code, label_spec=label_spec,
            authority=authority, store=store,
        )
        self._table_spec = table_spec

    def extract(self, pages: list[str]) -> ExtractionOutcome:
        outcome = parse_label_value(pages, self.label_spec)
        if not isinstance(outcome, Extracted):
            return outcome

        rows = parse_table_rows(pages, self._table_spec)
        if not rows:
            # A table this type declares but that produced zero rows is a
            # missing required fact — same reasoning
            # `ShareholdingPatternExtractor.extract` below gives.
            return Partial(fields=outcome.fields, missing=("table_rows",))

        fields = dict(outcome.fields)
        fields["_table_rows"] = rows
        return Extracted(fields=fields)

    async def _extra_silver_write(
        self,
        conn: psycopg.AsyncConnection[tuple[object, ...]],
        *,
        record_id: uuid.UUID,
        prior_record_id: uuid.UUID | None,
        ctx: TenantContext,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        batch_id: uuid.UUID,
        ingest_run_id: uuid.UUID,
        outcome: Extracted,
    ) -> None:
        rows = cast("list[dict[str, object]]", outcome.fields["_table_rows"])

        if prior_record_id is not None:
            await close_prior_fact_rows(
                conn, schema=ctx.silver_schema, table=self._fact_table,
                prior_record_id=prior_record_id,
            )

        await record_fact_rows(
            conn, schema=ctx.silver_schema, table=self._fact_table,
            envelope=FactRowEnvelope(
                entity_id=entity_id, record_id=record_id, batch_id=batch_id,
                bronze_ingest_id=ingest_id, extraction_run_id=ingest_run_id,
            ),
            rows=rows,
        )


class GstinRegisterExtractor(TableFactExtractor):
    """`GSTIN_REGISTER` — one fact row per GSTIN under the PAN. Writes to
    `gstin_register_entry` (tenant migration 033)."""

    _fact_table = "gstin_register_entry"


class DirectorListExtractor(TableFactExtractor):
    """`DIRECTOR_LIST_WITH_DIN` — one fact row per director. Writes to
    `director_list_din` (tenant migration 034)."""

    _fact_table = "director_list_din"


class KmpListExtractor(TableFactExtractor):
    """`KMP_LIST` — one fact row per key managerial person. Writes to
    `kmp_list_member` (tenant migration 035)."""

    _fact_table = "kmp_list_member"


class ShareholdingPatternExtractor(TableFactExtractor):
    """`doc_type_code`/`label_spec`/`table_spec` come from the registry
    (`SHAREHOLDING_PATTERN`'s `extraction_spec` cell) — only the FY
    expansion below is per-type Python, and only because it computes
    (`fy_containing`/`fy_bounds` against the document's own `as_of_date`),
    it does not bind a label. Overrides `TableFactExtractor.extract`/
    `_extra_silver_write` rather than using them as-is, because one PDF row
    becomes TWO facts (current FY, prior FY), not one.
    """

    _fact_table = _FACT_TABLE

    def __init__(
        self,
        pool: TenantScopedPool,
        *,
        doc_type_code: str,
        label_spec: LabelSpec,
        table_spec: TableSpec,
        authority: str | None = None,
        store: ObjectStorePort | None = None,
    ) -> None:
        super().__init__(
            pool, doc_type_code=doc_type_code, label_spec=label_spec,
            table_spec=table_spec, authority=authority, store=store,
        )
        self._fiscal_year_start_month = get_settings().fiscal_year_start_month

    def extract(self, pages: list[str]) -> ExtractionOutcome:
        outcome = parse_label_value(pages, self.label_spec)
        if not isinstance(outcome, Extracted):
            return outcome

        rows = parse_table_rows(pages, self._table_spec)
        if not rows:
            # A table this type declares but that produced zero rows is a
            # missing required fact, folded into the SAME Partial/quarantine
            # path the header binding already uses — no second outcome type.
            # A row that matched some but not all (a name found, a %
            # genuinely unparseable) is NOT separately escalated here — see
            # `tablevalue.py`'s module docstring for why a reliable "this
            # was a malformed row, not unrelated prose" signal does not
            # exist yet at this phase.
            return Partial(fields=outcome.fields, missing=("shareholder_rows",))

        as_of_date = cast("dt.date", outcome.fields["as_of_date"])
        current_start, current_end = fy_containing(
            as_of_date, start_month=self._fiscal_year_start_month
        )
        current_from, current_to = fy_bounds(
            current_start, current_end, start_month=self._fiscal_year_start_month
        )
        prior_from, prior_to = fy_bounds(
            current_start - 1, current_end - 1, start_month=self._fiscal_year_start_month
        )

        fields = dict(outcome.fields)
        fields["_shareholding_windows"] = (
            rows, (current_from, current_to), (prior_from, prior_to),
        )
        return Extracted(fields=fields)

    async def _extra_silver_write(
        self,
        conn: psycopg.AsyncConnection[tuple[object, ...]],
        *,
        record_id: uuid.UUID,
        prior_record_id: uuid.UUID | None,
        ctx: TenantContext,
        entity_id: uuid.UUID,
        ingest_id: uuid.UUID,
        batch_id: uuid.UUID,
        ingest_run_id: uuid.UUID,
        outcome: Extracted,
    ) -> None:
        rows, current_window, prior_window = cast(
            "tuple[list[dict[str, object]], tuple[dt.date, dt.date], tuple[dt.date, dt.date]]",
            outcome.fields["_shareholding_windows"],
        )

        if prior_record_id is not None:
            await close_prior_fact_rows(
                conn, schema=ctx.silver_schema, table=_FACT_TABLE,
                prior_record_id=prior_record_id,
            )

        facts: list[dict[str, object]] = []
        for row in rows:
            for pct_column, (valid_from, valid_to) in (
                (_CURRENT_FY_COLUMN, current_window),
                (_PRIOR_FY_COLUMN, prior_window),
            ):
                try:
                    pct = Decimal(cast("str", row[pct_column]))
                except (InvalidOperation, KeyError):
                    continue
                facts.append({
                    "holder_name": cast("str", row["holder_name"]).strip(),
                    "shares": Decimal(cast("str", row["shares"])) if row.get("shares") else None,
                    "pct_holding": pct,
                    "pledged": cast("str", row["pledged"]).strip().casefold() == "yes",
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                })

        await record_fact_rows(
            conn, schema=ctx.silver_schema, table=_FACT_TABLE,
            envelope=FactRowEnvelope(
                entity_id=entity_id, record_id=record_id, batch_id=batch_id,
                bronze_ingest_id=ingest_id, extraction_run_id=ingest_run_id,
            ),
            rows=facts,
        )
