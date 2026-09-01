"""A1.01 SALES_REGISTER — parse, hash, and upsert into the typed table.

The reference pattern for TYPED-TABLES-PLAN.md. Two things distinguish it from
`promote.py`, which it does NOT replace (that happens at step 4, with
PURCHASE_REGISTER):

  * **Typed columns, not a contract-driven jsonb bag.** Nothing here reads
    `document_type.field_contract`. `place_of_supply` and `invoice_type` are
    columns with a CHECK and a foreign key, so the grammar in
    `src/silver/contract.py` has nothing left to describe for this type.

  * **Idempotency is per row, not per batch.** TYPED-TABLES-PLAN.md 5: a weekly
    full-file re-export of a growing register no-ops on every invoice it has
    already seen and inserts only what is new. No batch supersession, no
    PENDING_REVIEW, no human in the loop. That is the whole reason the
    resubmission gap in TODO.md is closed rather than worked around.

`ingest_batch` is untouched as the queue of record (invariant 1). It stops being
the unit of *truth* about which rows are current; that moves to the rows.

Column names follow `reference/inafin_a1_schema.sql`.
"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import hashlib
import io
import logging
import uuid
from dataclasses import dataclass, field
from typing import cast

from psycopg import AsyncConnection, sql

from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext

logger = logging.getLogger(__name__)

DOC_TYPE = "SALES_REGISTER"

# The ERP export contract. Flat — header fields repeat on every line row, which
# is how the reference schema models it and how ERPs actually export a register.
# Splitting header from line is OUR decision (TYPED-TABLES-PLAN.md 6) and happens
# here, on the way in.
#
# THE `invoice_` PREFIX IS LOAD-BEARING. The reference is flat, so `taxable_value`,
# `cgst` and `total_value` are LINE values there and it carries no invoice-level
# totals at all. A split needs both grains in one flat row, and reusing one column
# name for both would make the file ambiguous — so the invoice-level claim is
# prefixed in the CSV. On the tables the reference names are used unprefixed at
# both grains, because there the table already says which grain you have.
HEADER_FIELDS = (
    "invoice_no",
    "invoice_date",
    "customer_gstin",
    "invoice_type",
    "supply_type",
    "place_of_supply",
    "reverse_charge",
    "currency",
    "trade_discount",
    "freight",
    "packing",
    "insurance",
    "invoice_taxable_value",
    "invoice_cgst",
    "invoice_sgst",
    "invoice_igst",
    "invoice_cess",
    "invoice_total_value",
    "irn",
    "ewb_no",
)
LINE_FIELDS = (
    "line_number",
    "hsn_sac",
    "description",
    "qty",
    "uom",
    "unit_price",
    "taxable_value",
    "gst_rate",
    "cgst",
    "sgst",
    "igst",
    "cess",
    "total_value",
)
REQUIRED_COLUMNS = frozenset(HEADER_FIELDS) | frozenset(LINE_FIELDS)

# Only these must carry a value. Everything else may be blank.
#
# The invoice-level totals are deliberately NOT required, and that is
# TYPED-TABLES-PLAN.md 6 expressed in code: absent means the ERP made no
# invoice-level claim, which is different from a claim of zero and different
# again from a claim that disagrees with the lines. Computing them from the lines
# would manufacture a claim that was never made, after which every invoice agrees
# with itself by construction and the check is worthless.
#
# customer_gstin is not required either: the registry records this type as
# counterparty=OPTIONAL, so a B2CS sale legitimately has none and its ABSENCE IS
# A FACT.
REQUIRED_VALUES = frozenset(
    {"invoice_no", "invoice_date", "invoice_type", "line_number", "taxable_value"}
)


@dataclass(frozen=True, slots=True)
class SalesLine:
    line_number: int
    hsn_sac: str | None
    description: str | None
    qty: decimal.Decimal | None
    uom: str | None
    unit_price: decimal.Decimal | None
    taxable_value: decimal.Decimal
    gst_rate: decimal.Decimal | None
    cgst: decimal.Decimal | None
    sgst: decimal.Decimal | None
    igst: decimal.Decimal | None
    cess: decimal.Decimal | None
    total_value: decimal.Decimal | None
    row_hash: str


@dataclass(frozen=True, slots=True)
class SalesInvoice:
    invoice_no: str
    invoice_date: dt.date
    customer_gstin: str | None
    invoice_type: str
    supply_type: str | None
    place_of_supply: str | None
    reverse_charge: bool
    currency: str
    trade_discount: decimal.Decimal | None
    freight: decimal.Decimal | None
    packing: decimal.Decimal | None
    insurance: decimal.Decimal | None
    # Invoice-level claims. None means the ERP made none.
    taxable_value: decimal.Decimal | None
    cgst: decimal.Decimal | None
    sgst: decimal.Decimal | None
    igst: decimal.Decimal | None
    cess: decimal.Decimal | None
    total_value: decimal.Decimal | None
    irn: str | None
    ewb_no: str | None
    row_hash: str
    document_hash: str
    lines: tuple[SalesLine, ...]


@dataclass
class ParseResult:
    invoices: list[SalesInvoice] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejections

    @property
    def line_count(self) -> int:
        return sum(len(i.lines) for i in self.invoices)


@dataclass(frozen=True, slots=True)
class UpsertOutcome:
    """What the resubmission table in TYPED-TABLES-PLAN.md 5 produced, counted.

    These numbers are the observable proof that a weekly full-file export is
    idempotent: the second run of an unchanged file must report
    inserted=0, superseded=0, unchanged=N.
    """

    batch_id: uuid.UUID
    inserted: int
    unchanged: int
    superseded: int
    invoice_count: int
    line_count: int


def _hash(values: list[str]) -> str:
    """Content identity over already-normalised field values.

    Unit-separated rather than concatenated: 'AB' + 'C' and 'A' + 'BC' must not
    collide, and the separator cannot occur in a parsed CSV field.
    """
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


class _RowError(Exception):
    pass


def _decimal(raw: str | None, column: str) -> decimal.Decimal | None:
    if not raw:
        return None
    try:
        return decimal.Decimal(raw)
    except decimal.InvalidOperation:
        raise _RowError(f"{column}: {raw!r} is not a number") from None


def _required_decimal(raw: str, column: str) -> decimal.Decimal:
    value = _decimal(raw.strip(), column)
    if value is None:
        raise _RowError(f"{column} is required")
    return value


def _date(raw: str, column: str) -> dt.date:
    try:
        return dt.date.fromisoformat(raw.strip())
    except ValueError:
        raise _RowError(f"{column}: {raw!r} is not an ISO date") from None


def _bool(raw: str, column: str) -> bool:
    lowered = (raw or "").strip().lower()
    if lowered in {"true", "t", "yes", "y", "1"}:
        return True
    if lowered in {"false", "f", "no", "n", "0", ""}:
        return False
    raise _RowError(f"{column}: {raw!r} is not a boolean")


def _clean(row: dict[str, str], column: str) -> str | None:
    value = (row.get(column) or "").strip()
    return value or None


def parse_sales_register_csv(data: bytes) -> ParseResult:
    """Parse a flat ERP export into header/line pairs, hashing as it goes.

    Rejections accumulate rather than raising on the first one: a file with
    fourteen bad rows should be reported once, not fourteen times over fourteen
    re-ingests.
    """
    result = ParseResult()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        result.rejections.append(f"file is not valid UTF-8: {exc}")
        return result

    reader = csv.DictReader(io.StringIO(text))
    present = set(reader.fieldnames or ())
    missing = sorted(REQUIRED_COLUMNS - present)
    if missing:
        result.rejections.append(f"missing required column(s): {', '.join(missing)}")
        return result

    # Insertion-ordered, so invoices land in file order and a diff of two runs is
    # readable.
    grouped: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for lineno, row in enumerate(reader, start=2):
        blank = sorted(c for c in REQUIRED_VALUES if not (row.get(c) or "").strip())
        if blank:
            result.rejections.append(
                f"row {lineno}: empty required field(s): {', '.join(blank)}"
            )
            continue
        grouped.setdefault(row["invoice_no"].strip(), []).append((lineno, row))

    for invoice_no, rows in grouped.items():
        try:
            result.invoices.append(_build_invoice(invoice_no, rows))
        except _RowError as exc:
            result.rejections.append(f"invoice {invoice_no}: {exc}")

    if not result.invoices and not result.rejections:
        result.rejections.append("file contains no data rows")
    return result


def _build_invoice(
    invoice_no: str, rows: list[tuple[int, dict[str, str]]]
) -> SalesInvoice:
    lineno, first = rows[0]

    # Header fields repeat on every line row. If two rows disagree, the export is
    # internally inconsistent and there is no defensible way to pick a winner — so
    # this rejects rather than taking the first, which would silently discard one
    # of two contradictory claims about the same invoice.
    for other_lineno, other in rows[1:]:
        differing = sorted(
            c
            for c in HEADER_FIELDS
            if (other.get(c) or "").strip() != (first.get(c) or "").strip()
        )
        if differing:
            raise _RowError(
                f"rows {lineno} and {other_lineno} disagree on header field(s): "
                f"{', '.join(differing)}"
            )

    lines: list[SalesLine] = []
    seen_numbers: set[int] = set()
    for row_lineno, row in rows:
        try:
            line_number = int(row["line_number"])
        except ValueError:
            raise _RowError(
                f"row {row_lineno}: line_number {row['line_number']!r} is not an integer"
            ) from None
        if line_number in seen_numbers:
            raise _RowError(f"row {row_lineno}: duplicate line_number {line_number}")
        seen_numbers.add(line_number)

        # Hashed from the RAW normalised strings, not the parsed values, so the
        # hash does not change if Decimal's repr ever does.
        line_hash = _hash([(row.get(c) or "").strip() for c in LINE_FIELDS])
        lines.append(
            SalesLine(
                line_number=line_number,
                hsn_sac=_clean(row, "hsn_sac"),
                description=_clean(row, "description"),
                qty=_decimal(_clean(row, "qty"), "qty"),
                uom=_clean(row, "uom"),
                unit_price=_decimal(_clean(row, "unit_price"), "unit_price"),
                taxable_value=_required_decimal(row["taxable_value"], "taxable_value"),
                gst_rate=_decimal(_clean(row, "gst_rate"), "gst_rate"),
                cgst=_decimal(_clean(row, "cgst"), "cgst"),
                sgst=_decimal(_clean(row, "sgst"), "sgst"),
                igst=_decimal(_clean(row, "igst"), "igst"),
                cess=_decimal(_clean(row, "cess"), "cess"),
                total_value=_decimal(_clean(row, "total_value"), "total_value"),
                row_hash=line_hash,
            )
        )

    lines.sort(key=lambda line: line.line_number)
    header_hash = _hash([(first.get(c) or "").strip() for c in HEADER_FIELDS])

    # document_hash covers the header AND its lines, in line order. A header-only
    # hash would no-op a resubmission that corrected a line, and the stale line
    # would survive undetected — see the column comment in tenant migration 009.
    document_hash = _hash([header_hash, *(line.row_hash for line in lines)])

    return SalesInvoice(
        invoice_no=invoice_no,
        invoice_date=_date(first["invoice_date"], "invoice_date"),
        customer_gstin=_clean(first, "customer_gstin"),
        invoice_type=first["invoice_type"].strip(),
        supply_type=_clean(first, "supply_type"),
        place_of_supply=_clean(first, "place_of_supply"),
        reverse_charge=_bool(first.get("reverse_charge", ""), "reverse_charge"),
        currency=(_clean(first, "currency") or "INR"),
        trade_discount=_decimal(_clean(first, "trade_discount"), "trade_discount"),
        freight=_decimal(_clean(first, "freight"), "freight"),
        packing=_decimal(_clean(first, "packing"), "packing"),
        insurance=_decimal(_clean(first, "insurance"), "insurance"),
        taxable_value=_decimal(
            _clean(first, "invoice_taxable_value"), "invoice_taxable_value"
        ),
        cgst=_decimal(_clean(first, "invoice_cgst"), "invoice_cgst"),
        sgst=_decimal(_clean(first, "invoice_sgst"), "invoice_sgst"),
        igst=_decimal(_clean(first, "invoice_igst"), "invoice_igst"),
        cess=_decimal(_clean(first, "invoice_cess"), "invoice_cess"),
        total_value=_decimal(
            _clean(first, "invoice_total_value"), "invoice_total_value"
        ),
        irn=_clean(first, "irn"),
        ewb_no=_clean(first, "ewb_no"),
        row_hash=header_hash,
        document_hash=document_hash,
        lines=tuple(lines),
    )


class SalesRegisterLoader:
    def __init__(self, pool: TenantScopedPool) -> None:
        self._pool = pool

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
        ingest_run_id: uuid.UUID | None = None,
    ) -> UpsertOutcome:
        """Promote one sales register artefact, upserting row by row.

        `gstin` is a parameter rather than a CSV column: the register belongs to
        one registration, and taking it from the file would let a mislabelled
        export write rows under a GSTIN the batch was not scoped to.

        Everything commits together. The manifest row is what makes the batch
        visible to Pipeline 2, so it must not become visible before the rows it
        describes — and a supersession must not be visible without the row that
        replaced it.
        """
        silver = ctx.silver_schema
        ingest_run_id = ingest_run_id or uuid.uuid4()

        parsed = parse_sales_register_csv(data)
        if not parsed.ok:
            reason = "; ".join(parsed.rejections[:5]) + (
                f" (+{len(parsed.rejections) - 5} more)"
                if len(parsed.rejections) > 5
                else ""
            )
            # BUG FOUND LIVE by the ERP upload E2E suite (2026-09-01): this
            # used to raise straight away with no durable record, anywhere.
            # `record_and_dispatch_trigger` still correctly folded the
            # exception into `TriggerOutcome.status = "QUARANTINED"` — that
            # part of the response was never wrong — but `GET /status`
            # (`SilverReader.artefact_outcome`) looks up
            # `v1_quarantined_artefact WHERE ingest_id = %s` FIRST, found
            # nothing, fell through to the `v1_ingest_batch` lookup (also
            # nothing — no batch is ever created on this path), and reported
            # PENDING forever. A client polling status after a synchronously
            # quarantined trigger saw the artefact hang indefinitely.
            # `RegisterLoader.load` (the flat REGISTER_LOADER mechanism) has
            # carried the fix for this exact failure shape since it was
            # written — `self._quarantine(...)` below is that same method,
            # copied rather than shared because the two loaders' `load()`
            # signatures differ enough that a shared helper would need to
            # accept `document_type` as a parameter either way, and this
            # loader has exactly one caller of it.
            await self._quarantine(ctx, ingest_id, entity_id, reason, ingest_run_id)
            raise ValidationRejected(
                f"{ctx} artefact {ingest_id} rejected: {reason}"
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
                    batch_id, entity_id, DOC_TYPE, period_start, period_end,
                    # LINES, not invoices — the meaning row_count already has
                    # elsewhere (src/silver/validate.py). Preserved deliberately
                    # rather than quietly redefined for the header/line split;
                    # TODO.md carries the open item to name it properly, and
                    # UpsertOutcome returns both numbers so a caller is not stuck
                    # with the ambiguous one.
                    parsed.line_count, content_hash, str(ingest_id), ingest_run_id,
                ),
            )

            for invoice in parsed.invoices:
                live = await (
                    await conn.execute(
                        sql.SQL(
                            "SELECT id, document_hash FROM {}.sales_register"
                            " WHERE entity_id = %s AND gstin = %s AND invoice_no = %s"
                            "   AND superseded_at IS NULL"
                            " FOR UPDATE"
                        ).format(sql.Identifier(silver)),
                        (entity_id, gstin, invoice.invoice_no),
                    )
                ).fetchone()

                if live is not None:
                    live_id, live_hash = cast("tuple[int, str]", live)
                    if live_hash == invoice.document_hash:
                        unchanged += 1
                        continue
                    await self._close(conn, silver, live_id, invoice.invoice_date)
                    superseded += 1
                else:
                    inserted += 1

                await self._insert(
                    conn, silver,
                    entity_id=entity_id, gstin=gstin, batch_id=batch_id,
                    ingest_id=ingest_id, tax_period=period_start, invoice=invoice,
                )

        logger.info(
            "sales register %s -> batch %s for %s: %d inserted, %d unchanged, %d superseded",
            ingest_id, batch_id, ctx, inserted, unchanged, superseded,
        )
        return UpsertOutcome(
            batch_id=batch_id, inserted=inserted, unchanged=unchanged,
            superseded=superseded, invoice_count=len(parsed.invoices),
            line_count=parsed.line_count,
        )

    @staticmethod
    async def _close(
        conn: AsyncConnection[tuple[object, ...]],
        silver: str,
        live_id: int,
        new_valid_from: dt.date,
    ) -> None:
        """Close the live version. The one UPDATE Silver performs.

        Invariant 6 holds: Bronze records fact and stays INSERT-only; this is
        Silver recording a judgement, which is what Silver is for.

        valid_to moves only when the replacement is valid from a LATER date. A
        correction to the same invoice arriving on the same day did not make the
        old version true for a day — its replacement happened in transaction
        time, which is what superseded_at records. Forcing valid_to forward
        anyway would either violate the valid_to > valid_from CHECK or invent a
        business-time interval that never existed.
        """
        await conn.execute(
            sql.SQL(
                """
                UPDATE {}.sales_register
                   SET superseded_at = now(),
                       valid_to      = CASE WHEN %s > valid_from THEN %s ELSE valid_to END,
                       modified_at   = now(),
                       modified_by   = current_user
                 WHERE id = %s
                """
            ).format(sql.Identifier(silver)),
            (new_valid_from, new_valid_from, live_id),
        )

    @staticmethod
    async def _insert(
        conn: AsyncConnection[tuple[object, ...]], silver: str, *,
        entity_id: uuid.UUID, gstin: str, batch_id: uuid.UUID,
        ingest_id: uuid.UUID, tax_period: dt.date, invoice: SalesInvoice,
    ) -> None:
        row = await (
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.sales_register (
                        entity_id, gstin, tax_period, batch_id, bronze_ingest_id,
                        row_hash, document_hash,
                        invoice_no, invoice_date, customer_gstin, invoice_type,
                        supply_type, place_of_supply, reverse_charge, currency,
                        trade_discount, freight, packing, insurance,
                        taxable_value, cgst, sgst, igst, cess, total_value,
                        irn, ewb_no, valid_from
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s)
                    RETURNING id
                    """
                ).format(sql.Identifier(silver)),
                (
                    entity_id, gstin, tax_period, batch_id, ingest_id,
                    invoice.row_hash, invoice.document_hash,
                    invoice.invoice_no, invoice.invoice_date, invoice.customer_gstin,
                    invoice.invoice_type, invoice.supply_type, invoice.place_of_supply,
                    invoice.reverse_charge, invoice.currency,
                    invoice.trade_discount, invoice.freight, invoice.packing,
                    invoice.insurance,
                    invoice.taxable_value, invoice.cgst, invoice.sgst, invoice.igst,
                    invoice.cess, invoice.total_value,
                    invoice.irn, invoice.ewb_no, invoice.invoice_date,
                ),
            )
        ).fetchone()
        header_id = cast("tuple[int]", row)[0]

        for line in invoice.lines:
            await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.sales_register_line (
                        header_id, line_number, row_hash, hsn_sac, description,
                        qty, uom, unit_price, taxable_value, gst_rate,
                        cgst, sgst, igst, cess, total_value
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s)
                    """
                ).format(sql.Identifier(silver)),
                (
                    header_id, line.line_number, line.row_hash, line.hsn_sac,
                    line.description, line.qty, line.uom, line.unit_price,
                    line.taxable_value, line.gst_rate,
                    line.cgst, line.sgst, line.igst, line.cess, line.total_value,
                ),
            )

    async def _quarantine(
        self,
        ctx: TenantContext,
        ingest_id: uuid.UUID,
        entity_id: uuid.UUID,
        reason: str,
        ingest_run_id: uuid.UUID,
    ) -> None:
        """FATAL parse failure only — the durable half of quarantining.

        Mirrors `RegisterLoader._quarantine`
        (`src/silver/registers/loader.py`) exactly: same table, same
        `ON CONFLICT` upsert (a re-attempt after a parser fix replaces the
        verdict rather than accumulating rows — the artefact itself is
        immutable, so a history of retries belongs in logs, not this table).
        The artefact stays in Bronze, untouched; the verdict is Silver's,
        because refusing a file is a conclusion drawn from parsing it.
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
                (ingest_id, entity_id, DOC_TYPE, reason, ingest_run_id),
            )
