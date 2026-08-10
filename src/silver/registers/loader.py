"""RegisterLoader — parse, hash and upsert one flat A1 register.

One class, driven by a `RegisterSpec`, for the 23 line-grain Group A1 types.
`src/silver/sales_register.py` is the reviewed pattern this follows; the
differences are all consequences of these registers being flat:

  * **No header/line split.** TYPED-TABLES-PLAN.md 6 exists to avoid the empty
    abstraction, and tenant migration 010's header records the finding: the
    reference schema is line-grain for all 23, so a header table would have no
    ERP-supplied totals to hold. `document_hash` therefore has nothing to cover
    that `row_hash` does not, and only `row_hash` exists on these tables.

  * **Two key strategies, not one.** `sales_register` has a natural key because
    an invoice number exists. Eleven of these do not carry any identifier, so
    they are keyed on content and a correction lands as a NEW row rather than
    superseding — see `KeyKind` and the migration header. That limitation is the
    register's, not the schema's, and the loader does not hide it: see
    `collapsed_duplicates`.

Idempotency is per row, exactly as in the reviewed pattern (TYPED-TABLES-PLAN.md
5). A weekly full-file re-export no-ops on every row it has already seen. That
is what closes TODO.md's resubmission gap, and `inserted=0, superseded=0,
unchanged=N` on a second run of an unchanged file is the observable proof.

**Row-level rejection, not file-level (tenant migration 012).** A file with one
bad row and 499 good ones promotes the 499 and records the one — it does not
quarantine the whole artefact. Two failure modes stay genuinely separate rather
than being generalised into one:

  * FATAL — bad encoding, a missing required column, zero data rows. Nothing
    can be identified as a row at all, so no batch is created; this is still
    `quarantined_artefact` (tenant 005), unchanged.
  * PER-ROW — everything else. The file parses; some rows don't. A batch is
    created from what did, and `rejected_row` (tenant 012) carries the rest,
    one row per rejection with its source line and the column at fault where
    there is one.

**One row-level rule, two framings.** `_parse_row` is the entire validation
rule — required fields, type coercion, the hash — and neither
`parse_register_csv` nor `parse_register_ndjson` reimplements any of it; each
does only the format-specific job of turning one record into `cells: dict[str,
str | None]` and a line number. A bug fixed in `_parse_row` is fixed for both
formats by construction, the same reason `src/silver/validate.py` has one
validator for eleven document types.
"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import hashlib
import io
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import cast

from psycopg import AsyncConnection, sql

from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.silver.registers.spec import KeyKind, Kind, RegisterSpec

logger = logging.getLogger(__name__)


def financial_year(day: dt.date) -> str:
    """The Indian financial year containing `day`, as '2026-27'.

    April to March. Three A1 registers are annual and the reference schema keys
    them on this string rather than a month, so it has to be derived somewhere;
    deriving it from the batch period is better than taking it from the file,
    for the same reason `gstin` is a parameter and not a column — a mislabelled
    export must not be able to choose which year it lands in.
    """
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _hash(values: list[str]) -> str:
    """Content identity over already-normalised field values.

    Unit-separated rather than concatenated: 'AB' + 'C' and 'A' + 'BC' must not
    collide, and the separator cannot occur in a parsed CSV field. Identical to
    `sales_register._hash` and deliberately not imported from it — that module is
    the reviewed A1.01 reference and this one must not be able to change its
    hashes by editing a helper.
    """
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


class _RowError(Exception):
    """Carries the column at fault, not just a formatted message.

    RowRejection.column comes from here — a customer report needs "gst_rate on
    row 42" as data, not as a substring of a sentence it would have to re-parse.
    """

    def __init__(self, column: str, detail: str) -> None:
        super().__init__(f"{column}: {detail}")
        self.column = column
        self.detail = detail


def _decimal(raw: str | None, column: str) -> decimal.Decimal | None:
    if not raw:
        return None
    try:
        return decimal.Decimal(raw)
    except decimal.InvalidOperation:
        raise _RowError(column, f"{raw!r} is not a number") from None


def _integer(raw: str | None, column: str) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise _RowError(column, f"{raw!r} is not an integer") from None


def _date(raw: str | None, column: str) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise _RowError(column, f"{raw!r} is not an ISO date") from None


def _bool(raw: str | None, column: str) -> bool:
    lowered = (raw or "").lower()
    if lowered in {"true", "t", "yes", "y", "1"}:
        return True
    if lowered in {"false", "f", "no", "n", "0", ""}:
        return False
    raise _RowError(column, f"{raw!r} is not a boolean")


def _coerce(kind: Kind, raw: str | None, column: str) -> object:
    match kind:
        case Kind.TEXT:
            return raw
        case Kind.DATE:
            return _date(raw, column)
        case Kind.DECIMAL:
            return _decimal(raw, column)
        case Kind.INTEGER:
            return _integer(raw, column)
        case Kind.BOOLEAN:
            return _bool(raw, column)


@dataclass(frozen=True, slots=True)
class RegisterRow:
    """One parsed data row: its typed values and its content identity."""

    source_line: int
    values: dict[str, object]
    row_hash: str


@dataclass(frozen=True, slots=True)
class RowRejection:
    """One row that did not make it, structured rather than a formatted string.

    `column` is None for a rejection that names no single field — a malformed
    NDJSON line, or a line that is not a JSON object at all.
    """

    source_line: int
    message: str
    column: str | None = None


@dataclass
class ParseResult:
    rows: list[RegisterRow] = field(default_factory=list)
    row_rejections: list[RowRejection] = field(default_factory=list)
    fatal: str | None = None
    """Set only when nothing in the file can be treated as a row at all — bad
    encoding, a missing required column, zero data rows. `ok` is False and the
    whole artefact is quarantined; `rows`/`row_rejections` are meaningless
    (parsing stopped before either could be populated). Distinct from
    `row_rejections`: those describe a file that DID parse, with some rows
    that didn't.
    """
    collapsed_duplicates: int = 0
    """Rows dropped because an EARLIER row in the same file had the same hash.

    Counted rather than rejected, and surfaced on the outcome. Two byte-identical
    rows in one file are ambiguous: on a bank statement they are plausibly two
    real payments of the same amount on the same day; on a trial balance they are
    an export bug. A content-keyed table cannot tell them apart — the unique
    index would reject the second either way — so the honest thing is to keep one
    and say how many were dropped, rather than to fail the file or to lose them
    silently.
    """

    @property
    def ok(self) -> bool:
        return self.fatal is None

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class RegisterOutcome:
    """What the upsert produced, counted.

    The proof that a full-file re-export is idempotent: the second run of an
    unchanged file must report inserted=0, superseded=0, unchanged=N.
    """

    doc_type_code: str
    table: str
    batch_id: uuid.UUID
    inserted: int
    unchanged: int
    superseded: int
    rejected: int
    row_count: int
    collapsed_duplicates: int


def _parse_row(
    spec: RegisterSpec, cells: dict[str, str | None], lineno: int
) -> RegisterRow | RowRejection:
    """The entire per-row rule: required fields, type coercion, the hash.

    Format-agnostic on purpose — `cells` is already normalised strings by the
    time this runs, whatever format produced them. Neither
    `parse_register_csv` nor `parse_register_ndjson` reimplements any of this.
    """
    if blank := [c for c in spec.required_names if cells.get(c) is None]:
        return RowRejection(
            source_line=lineno,
            column=blank[0],
            message=f"empty required field(s): {', '.join(blank)}",
        )

    try:
        values = {
            column.name: _coerce(column.kind, cells.get(column.name), column.name)
            for column in spec.columns
        }
    except _RowError as exc:
        return RowRejection(source_line=lineno, column=exc.column, message=exc.detail)

    # Hashed from the normalised STRINGS, not the coerced values, so the hash
    # does not change if Decimal's repr ever does.
    row_hash = _hash([cells.get(c) or "" for c in spec.column_names])
    return RegisterRow(source_line=lineno, values=values, row_hash=row_hash)


def _accumulate(
    spec: RegisterSpec,
    cells: dict[str, str | None],
    lineno: int,
    result: ParseResult,
    seen: Counter[str],
) -> None:
    """Run one row through `_parse_row` and file it: rejected, deduped, or kept."""
    parsed = _parse_row(spec, cells, lineno)
    if isinstance(parsed, RowRejection):
        result.row_rejections.append(parsed)
        return
    seen[parsed.row_hash] += 1
    if seen[parsed.row_hash] > 1:
        result.collapsed_duplicates += 1
        return
    result.rows.append(parsed)


def parse_register_csv(spec: RegisterSpec, data: bytes) -> ParseResult:
    """Parse a flat ERP export against one register's spec.

    Row rejections accumulate rather than stopping the file: a 500-row export
    with one bad row promotes 499 rows, not zero.
    """
    result = ParseResult()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        result.fatal = f"file is not valid UTF-8: {exc}"
        return result

    reader = csv.DictReader(io.StringIO(text))
    present = set(reader.fieldnames or ())
    if missing := sorted(set(spec.column_names) - present):
        result.fatal = f"missing required column(s): {', '.join(missing)}"
        return result

    seen: Counter[str] = Counter()
    for lineno, raw_row in enumerate(reader, start=2):
        # Normalise once. Everything downstream — the required check, the
        # coercion and the hash — reads these strings, so a trailing space can
        # never make two otherwise identical rows hash differently.
        cells = {c: ((raw_row.get(c) or "").strip() or None) for c in spec.column_names}
        _accumulate(spec, cells, lineno, result, seen)

    if not result.rows and not result.row_rejections:
        result.fatal = "file contains no data rows"
    return result


def parse_register_ndjson(spec: RegisterSpec, data: bytes) -> ParseResult:
    """Parse a newline-delimited JSON export against one register's spec.

    One JSON object per line, no wrapping array — chosen over an array so a
    malformed line is a row rejection, not a whole-file JSON parse failure the
    way one bad element in a `[...]` array would be. Line numbers are 1-based
    over the raw file (there is no header row to offset past, unlike CSV's
    start=2). Every cell is stringified before `_parse_row` sees it, so a JSON
    file with `"gst_rate": 18` and a CSV file with `gst_rate,18` hit the exact
    same coercion rule and cannot disagree about what a valid row is.
    """
    result = ParseResult()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        result.fatal = f"file is not valid UTF-8: {exc}"
        return result

    seen: Counter[str] = Counter()
    header_checked = False
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            result.row_rejections.append(
                RowRejection(source_line=lineno, message=f"not valid JSON: {exc}")
            )
            continue
        if not isinstance(record, dict):
            result.row_rejections.append(
                RowRejection(source_line=lineno, message="each line must be a JSON object")
            )
            continue

        # Checked against the first record only, the NDJSON analogue of a CSV
        # header — a genuinely column-less export fails fast rather than
        # producing one rejection per line.
        if not header_checked:
            header_checked = True
            if missing := sorted(set(spec.column_names) - set(record)):
                result.fatal = f"missing required column(s): {', '.join(missing)}"
                return result

        cells = {
            c: (str(record[c]).strip() or None if record.get(c) is not None else None)
            for c in spec.column_names
        }
        _accumulate(spec, cells, lineno, result, seen)

    if not result.rows and not result.row_rejections:
        result.fatal = "file contains no data rows"
    return result


#: Format name -> parser. The one place a third format gets added — a caller
#: never branches on format, only on this dict having the key.
_PARSERS = {
    "csv": parse_register_csv,
    "ndjson": parse_register_ndjson,
}


class RegisterLoader:
    """Loads one register type. Construct it with that type's spec."""

    def __init__(self, pool: TenantScopedPool, spec: RegisterSpec) -> None:
        self._pool = pool
        self._spec = spec

    async def load(
        self,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        gstin: str,
        ingest_id: uuid.UUID,
        data: bytes,
        period_start: dt.date,
        period_end: dt.date,
        doc_type_code: str | None = None,
        ingest_run_id: uuid.UUID | None = None,
        content_format: str = "csv",
    ) -> RegisterOutcome:
        """Promote one register artefact, upserting row by row.

        `gstin` is a parameter rather than a CSV column, for the reason
        `sales_register.load` gives: the register belongs to one registration,
        and taking it from the file would let a mislabelled export write rows
        under a GSTIN the batch was not scoped to.

        Row-level rejection (tenant migration 012): a file that parses but has
        some bad rows still promotes — the batch is created from what's valid,
        and every dropped row lands in `rejected_row`. Only a FATAL parse
        failure (bad encoding, a missing required column, zero data rows)
        quarantines the whole artefact, because then nothing can be identified
        as a row at all.

        Everything commits together, in the ONE transaction below. The manifest
        row is what makes the batch visible to Pipeline 2, so it must not
        become visible before the rows it describes; the rejected rows are in
        the same transaction for the same reason — a batch and the reasons some
        of its input didn't make it must appear atomically, or a report run
        between the two commits would show a batch with no explanation for its
        row_count being lower than the file it came from.
        """
        spec = self._spec
        silver = ctx.silver_schema
        ingest_run_id = ingest_run_id or uuid.uuid4()
        code = self._resolve_doc_type(doc_type_code)
        period_value = self._period_value(period_start, period_end)

        try:
            parser = _PARSERS[content_format]
        except KeyError:
            raise ValueError(
                f"unknown content_format {content_format!r},"
                f" expected one of {sorted(_PARSERS)}"
            ) from None

        parsed = parser(spec, data)
        if not parsed.ok:
            await self._quarantine(ctx, ingest_id, entity_id, code, parsed.fatal, ingest_run_id)
            raise ValidationRejected(
                f"{ctx} artefact {ingest_id} rejected as {code}: {parsed.fatal}"
            )

        batch_id = uuid.uuid4()
        content_hash = hashlib.sha256(data).digest()
        inserted = unchanged = superseded = 0

        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.ingest_batch (
                        batch_id, entity_id, document_type, source_stream,
                        period_start, period_end, row_count, content_hash,
                        bronze_manifest_ref, status, ingest_run_id
                    ) VALUES (%s, %s, %s, 'B', %s, %s, %s, %s, %s, 'READY', %s)
                    """
                ).format(sql.Identifier(silver)),
                (
                    batch_id, entity_id, code, period_start, period_end,
                    parsed.row_count, content_hash, str(ingest_id), ingest_run_id,
                ),
            )

            for row in parsed.rows:
                valid_from = self._valid_from(row, period_start)
                if spec.key_kind is KeyKind.NATURAL:
                    outcome = await self._upsert_natural(
                        conn, silver, entity_id=entity_id, gstin=gstin,
                        period_value=period_value, code=code, batch_id=batch_id,
                        ingest_id=ingest_id, row=row, valid_from=valid_from,
                    )
                else:
                    outcome = await self._upsert_content(
                        conn, silver, entity_id=entity_id, gstin=gstin,
                        period_value=period_value, code=code, batch_id=batch_id,
                        ingest_id=ingest_id, row=row, valid_from=valid_from,
                    )
                match outcome:
                    case "inserted":
                        inserted += 1
                    case "superseded":
                        superseded += 1
                    case _:
                        unchanged += 1

            if parsed.row_rejections:
                await self._record_rejections(
                    conn, silver, batch_id=batch_id, code=code,
                    ingest_id=ingest_id, rejections=parsed.row_rejections,
                )

        logger.info(
            "%s %s -> batch %s for %s: %d inserted, %d unchanged, %d superseded,"
            " %d rejected, %d duplicate row(s) collapsed",
            code, ingest_id, batch_id, ctx,
            inserted, unchanged, superseded,
            len(parsed.row_rejections), parsed.collapsed_duplicates,
        )
        return RegisterOutcome(
            doc_type_code=code, table=spec.table, batch_id=batch_id,
            inserted=inserted, unchanged=unchanged, superseded=superseded,
            rejected=len(parsed.row_rejections),
            row_count=parsed.row_count,
            collapsed_duplicates=parsed.collapsed_duplicates,
        )

    async def _record_rejections(
        self,
        conn: AsyncConnection[tuple[object, ...]],
        silver: str,
        *,
        batch_id: uuid.UUID,
        code: str,
        ingest_id: uuid.UUID,
        rejections: list[RowRejection],
    ) -> None:
        """One INSERT per rejection. A batch is at most tens of thousands of
        rows, so this is not the hot path executemany would exist to fix."""
        for rej in rejections:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.rejected_row (
                        batch_id, doc_type_code, bronze_ingest_id,
                        source_line, column_name, message
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """
                ).format(sql.Identifier(silver)),
                (batch_id, code, ingest_id, rej.source_line, rej.column, rej.message),
            )

    async def _quarantine(
        self,
        ctx: TenantContext,
        ingest_id: uuid.UUID,
        entity_id: uuid.UUID,
        document_type: str,
        reason: str | None,
        ingest_run_id: uuid.UUID,
    ) -> None:
        """FATAL parse failure only — mirrors src/silver/promote.py's
        `_quarantine`. The artefact stays in Bronze, untouched; the verdict is
        Silver's, because refusing a file is a conclusion drawn from parsing it.
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.quarantined_artefact (
                        ingest_id, entity_id, document_type, reason, ingest_run_id
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (ingest_id) DO UPDATE
                       SET reason        = EXCLUDED.reason,
                           rejected_at   = now(),
                           ingest_run_id = EXCLUDED.ingest_run_id
                    """
                ).format(sql.Identifier(ctx.silver_schema)),
                (ingest_id, entity_id, document_type, reason or "unknown", ingest_run_id),
            )

    # --- scoping -----------------------------------------------------------

    def _resolve_doc_type(self, doc_type_code: str | None) -> str:
        spec = self._spec
        if doc_type_code is None:
            if (default := spec.default_doc_type_code) is None:
                raise ValueError(
                    f"{spec.table} is shared by {len(spec.doc_type_codes)} registry"
                    " types; doc_type_code is required"
                )
            return default
        if doc_type_code not in spec.doc_type_codes:
            raise ValueError(
                f"{doc_type_code!r} does not load into {spec.table}"
                f" (expected one of {', '.join(spec.doc_type_codes)})"
            )
        return doc_type_code

    def _period_value(self, period_start: dt.date, period_end: dt.date) -> object:
        """The value for the table's period column — a date, or an FY string.

        A batch that straddles two financial years is refused rather than
        assigned to the year its first day falls in. An annual register covering
        April 2026 to March 2028 is not a fact about either year, and silently
        filing it under one would make a reconciliation wrong in a way no later
        query could detect.
        """
        if self._spec.period_column == "tax_period":
            return period_start
        fy = financial_year(period_start)
        if (end_fy := financial_year(period_end)) != fy:
            raise ValueError(
                f"{self._spec.table} is keyed on a financial year, but the batch"
                f" period {period_start}..{period_end} spans {fy} and {end_fy}"
            )
        return fy

    def _valid_from(self, row: RegisterRow, period_start: dt.date) -> dt.date:
        if (column := self._spec.valid_from_column) is None:
            return period_start
        # The spec forbids a nullable valid_from_column, and parse rejects a row
        # whose required cells are blank, so this is a date.
        return cast("dt.date", row.values[column])

    # --- the two key strategies -------------------------------------------

    async def _upsert_natural(
        self,
        conn: AsyncConnection[tuple[object, ...]],
        silver: str,
        *,
        entity_id: uuid.UUID,
        gstin: str,
        period_value: object,
        code: str,
        batch_id: uuid.UUID,
        ingest_id: uuid.UUID,
        row: RegisterRow,
        valid_from: dt.date,
    ) -> str:
        """Look up the live version by natural key; supersede it if it changed.

        Nullable key columns are matched with IS NOT DISTINCT FROM, not `=`.
        Three natural keys include `supplier_gstin`, which is nullable — an
        unregistered supplier is legitimate — and `supplier_gstin = NULL` matches
        nothing, so every resubmission of such a row would insert a duplicate
        instead of superseding.

        That fixes the LOOKUP. It does not fix the INDEX: a partial unique index
        treats two NULLs as distinct, so the database will not refuse the
        duplicate if two loads race. TODO.md carries it; the fix is
        NULLS NOT DISTINCT (PG15+) on those three indexes, which is a migration,
        not a loader change.
        """
        spec = self._spec
        envelope: dict[str, object] = {
            "entity_id": entity_id,
            "gstin": gstin,
            spec.period_column: period_value,
        }

        predicates: list[sql.Composable] = []
        params: list[object] = []
        for name in spec.key_columns:
            value = envelope[name] if name in envelope else row.values[name]
            column = spec.column(name)
            nullable = column is not None and not column.required
            operator = sql.SQL("IS NOT DISTINCT FROM") if nullable else sql.SQL("=")
            predicates.append(
                sql.SQL("{} {} %s").format(sql.Identifier(name), operator)
            )
            params.append(value)

        live = await (
            await conn.execute(
                sql.SQL(
                    "SELECT id, row_hash FROM {}.{} WHERE {}"
                    "   AND superseded_at IS NULL FOR UPDATE"
                ).format(
                    sql.Identifier(silver),
                    sql.Identifier(spec.table),
                    sql.SQL(" AND ").join(predicates),
                ),
                params,
            )
        ).fetchone()

        if live is not None:
            live_id, live_hash = cast("tuple[int, str]", live)
            if live_hash == row.row_hash:
                return "unchanged"
            await self._close(conn, silver, live_id, valid_from)
            await self._insert(
                conn, silver, entity_id=entity_id, gstin=gstin,
                period_value=period_value, code=code, batch_id=batch_id,
                ingest_id=ingest_id, row=row, valid_from=valid_from,
            )
            return "superseded"

        await self._insert(
            conn, silver, entity_id=entity_id, gstin=gstin,
            period_value=period_value, code=code, batch_id=batch_id,
            ingest_id=ingest_id, row=row, valid_from=valid_from,
        )
        return "inserted"

    async def _upsert_content(
        self,
        conn: AsyncConnection[tuple[object, ...]],
        silver: str,
        *,
        entity_id: uuid.UUID,
        gstin: str,
        period_value: object,
        code: str,
        batch_id: uuid.UUID,
        ingest_id: uuid.UUID,
        row: RegisterRow,
        valid_from: dt.date,
    ) -> str:
        """Insert unless this exact content is already live in this period.

        ON CONFLICT DO NOTHING against the table's partial unique index rather
        than SELECT-then-INSERT: one statement, and the database decides. The
        inference clause must repeat the index's WHERE, or Postgres cannot match
        the partial index and raises instead of deduping.
        """
        inserted_id = await self._insert(
            conn, silver, entity_id=entity_id, gstin=gstin,
            period_value=period_value, code=code, batch_id=batch_id,
            ingest_id=ingest_id, row=row, valid_from=valid_from,
            on_conflict_do_nothing=True,
        )
        return "inserted" if inserted_id is not None else "unchanged"

    # --- the two statements ------------------------------------------------

    async def _insert(
        self,
        conn: AsyncConnection[tuple[object, ...]],
        silver: str,
        *,
        entity_id: uuid.UUID,
        gstin: str,
        period_value: object,
        code: str,
        batch_id: uuid.UUID,
        ingest_id: uuid.UUID,
        row: RegisterRow,
        valid_from: dt.date,
        on_conflict_do_nothing: bool = False,
    ) -> int | None:
        spec = self._spec
        columns = (
            "entity_id", "gstin", spec.period_column, "doc_type_code",
            "batch_id", "bronze_ingest_id", "row_hash", "valid_from",
            *spec.column_names,
        )
        params: list[object] = [
            entity_id, gstin, period_value, code,
            batch_id, ingest_id, row.row_hash, valid_from,
            *(row.values[name] for name in spec.column_names),
        ]

        conflict: sql.Composable = sql.SQL("")
        if on_conflict_do_nothing:
            conflict = sql.SQL(
                " ON CONFLICT ({}) WHERE superseded_at IS NULL DO NOTHING"
            ).format(
                sql.SQL(", ").join(sql.Identifier(c) for c in spec.key_columns)
            )

        result = await conn.execute(
            sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}){} RETURNING id").format(
                sql.Identifier(silver),
                sql.Identifier(spec.table),
                sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                sql.SQL(", ").join(sql.Placeholder() * len(columns)),
                conflict,
            ),
            params,
        )
        returned = await result.fetchone()
        return cast("tuple[int]", returned)[0] if returned is not None else None

    async def _close(
        self,
        conn: AsyncConnection[tuple[object, ...]],
        silver: str,
        live_id: int,
        new_valid_from: dt.date,
    ) -> None:
        """Close the live version. The one UPDATE Silver performs.

        Invariant 6 holds: Bronze records fact and stays INSERT-only; this is
        Silver recording a judgement, which is what Silver is for.

        valid_to moves only when the replacement is valid from a LATER date, for
        the reason `sales_register._close` gives: a correction arriving on the
        same day did not make the old version true for a day, and forcing
        valid_to forward would either violate the valid_to > valid_from CHECK or
        invent a business-time interval that never existed.
        """
        await conn.execute(
            sql.SQL(
                """
                UPDATE {}.{}
                   SET superseded_at = now(),
                       valid_to      = CASE WHEN %s > valid_from THEN %s ELSE valid_to END,
                       modified_at   = now(),
                       modified_by   = current_user
                 WHERE id = %s
                """
            ).format(sql.Identifier(silver), sql.Identifier(self._spec.table)),
            (new_valid_from, new_valid_from, live_id),
        )
