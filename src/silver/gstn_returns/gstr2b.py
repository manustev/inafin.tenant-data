"""B1.03 GSTR_2B — parse the real GSTN API JSON shape, upsert into
`gstr_2b` / `gstr_2b_line` / `gstr_2b_itc_summary` (tenant migration 026).

REAL SHAPE, NOT ASSUMED. Confirmed by directly diffing the JSON against its
sibling flat CSV during this session (`HANDOFF-2026-08-19-categoryB.md`
§2b): the CSV covers only the `b2b` section and drops `isd`/`impg`
entirely, so JSON is parsed here directly — no CSV fallback exists for this
type. The payload shape:

    {"data": {"gstin", "rtnprd", "version", "gendt",
              "docdata": {"b2b": [...], "cdnr": [...], "impg": [...], "isd": [...]},
              "itcsumm": {"itcavl": {...}, "itcnotavl": {...}}}}

`b2b` is grouped by supplier -> invoice -> item (3 levels); `impg`/`isd` are
flat one-row-per-document. `cdnr` is present in the schema but every real
specimen in `reference/B-Documents/` carries an empty array — see
`_reject_unparsed_cdnr` below for what happens if a real one ever arrives.

CONTENT IDENTITY, not a batch-level hash: `row_hash` per line,
`document_hash` over the whole statement (header facts + every line's hash
in order + every summary row) — same idiom `sales_register.py`'s `_hash`
already establishes, reused here rather than re-derived.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

from psycopg import sql

from src.core.errors import ValidationRejected
from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext

logger = logging.getLogger(__name__)

DOC_TYPE = "GSTR_2B"


@dataclass(frozen=True, slots=True)
class ParsedLine:
    section: str
    counterparty_gstin: str | None
    counterparty_name: str | None
    document_kind: str
    document_number: str
    document_date: dt.date
    item_number: int | None
    rate: decimal.Decimal | None
    taxable_value: decimal.Decimal | None
    igst_amount: decimal.Decimal
    cgst_amount: decimal.Decimal
    sgst_amount: decimal.Decimal
    cess_amount: decimal.Decimal
    itc_available: bool | None
    itc_unavailable_reason: str | None
    place_of_supply: str | None
    reverse_charge: bool | None
    attributes: dict[str, Any]
    row_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "row_hash", _hash([
            self.section, self.counterparty_gstin or "", self.document_kind,
            self.document_number, self.document_date.isoformat(),
            "" if self.item_number is None else str(self.item_number),
            str(self.rate or ""), str(self.taxable_value or ""),
            str(self.igst_amount), str(self.cgst_amount),
            str(self.sgst_amount), str(self.cess_amount),
        ]))


@dataclass(frozen=True, slots=True)
class ParsedItcSummary:
    category: str
    availability: str
    igst_amount: decimal.Decimal
    cgst_amount: decimal.Decimal
    sgst_amount: decimal.Decimal
    cess_amount: decimal.Decimal


@dataclass(frozen=True, slots=True)
class ParsedGstr2b:
    gstin: str
    return_period: str
    generated_at: dt.datetime | None
    source_version: str | None
    lines: tuple[ParsedLine, ...]
    itc_summary: tuple[ParsedItcSummary, ...]
    document_hash: str = field(init=False)

    def __post_init__(self) -> None:
        parts = [self.gstin, self.return_period, *(ln.row_hash for ln in self.lines)]
        parts.extend(
            _hash([s.category, s.availability, str(s.igst_amount), str(s.cgst_amount),
                   str(s.sgst_amount), str(s.cess_amount)])
            for s in self.itc_summary
        )
        object.__setattr__(self, "document_hash", _hash(parts))


def _hash(values: list[str]) -> str:
    """Unit-separated, same reasoning `sales_register.py`'s `_hash` states:
    'AB'+'C' and 'A'+'BC' must not collide."""
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def _amount(value: object) -> decimal.Decimal:
    if value is None:
        return decimal.Decimal("0")
    return decimal.Decimal(str(value))


def _date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%d-%m-%Y").date()


def parse_gstr_2b(data: bytes, *, expected_period: dt.date) -> ParsedGstr2b:
    """Raises `ValidationRejected` for malformed JSON, a missing required
    top-level field, a period that does not match what the caller (the
    trigger's own `period_start`) expected, or a non-empty `cdnr` section —
    see `_reject_unparsed_cdnr`."""
    try:
        payload = json.loads(data)
        top = payload["data"]
        gstin = cast(str, top["gstin"])
        return_period = cast(str, top["rtnprd"])
        docdata = top["docdata"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValidationRejected(f"malformed GSTR_2B payload: {exc}") from exc

    _reject_period_mismatch(return_period, expected_period)
    _reject_unparsed_cdnr(docdata.get("cdnr", []))

    gendt = top.get("gendt")
    generated_at = (
        dt.datetime.combine(_date(gendt), dt.time.min, tzinfo=dt.UTC) if gendt else None
    )

    lines: list[ParsedLine] = []
    lines.extend(_parse_b2b(docdata.get("b2b", [])))
    lines.extend(_parse_impg(docdata.get("impg", [])))
    lines.extend(_parse_isd(docdata.get("isd", [])))

    return ParsedGstr2b(
        gstin=gstin, return_period=return_period, generated_at=generated_at,
        source_version=top.get("version"),
        lines=tuple(lines), itc_summary=tuple(_parse_itc_summary(top.get("itcsumm", {}))),
    )


def _reject_period_mismatch(return_period: str, expected_period: dt.date) -> None:
    expected = f"{expected_period.month:02d}{expected_period.year:04d}"
    if return_period != expected:
        raise ValidationRejected(
            f"GSTR_2B payload rtnprd={return_period!r} does not match the "
            f"trigger's period_start ({expected_period.isoformat()}, expected {expected!r})"
        )


def _reject_unparsed_cdnr(cdnr: list[object]) -> None:
    """See this module's docstring: no real specimen with a populated `cdnr`
    exists in this workspace, so its `nt[]` shape has not been verified
    against real data. Quarantining rather than guessing keeps the same
    "no invented vocabulary" discipline `CLAUDE.md` states for the onboarding
    tables — a wrong guess here would silently drop real credit/debit notes
    from every ITC reconciliation that reads this table."""
    if cdnr:
        raise ValidationRejected(
            "GSTR_2B payload has a non-empty cdnr section, which this parser "
            "does not support yet (no real specimen exists to verify the "
            "nt[] shape against — see src/silver/gstn_returns/gstr2b.py)"
        )


def _parse_b2b(suppliers: list[dict[str, Any]]) -> list[ParsedLine]:
    lines: list[ParsedLine] = []
    for supplier in suppliers:
        ctin = supplier.get("ctin")
        trdnm = supplier.get("trdnm")
        supplier_attrs = {
            "supplier_filed_period": supplier.get("supprd"),
            "supplier_filed_date": supplier.get("supfileddt"),
            "supplier_filing_mode": supplier.get("supfilingmode"),
        }
        for inv in supplier.get("inv", []):
            itcavl = inv.get("itcavl")
            rev = inv.get("rev")
            attrs = {
                **supplier_attrs,
                "irn": inv.get("irn") or None,
                "irngendate": inv.get("irngendate") or None,
                "diffprcnt": inv.get("diffprcnt"),
            }
            for item in inv.get("items", []):
                lines.append(ParsedLine(
                    section="B2B", counterparty_gstin=ctin, counterparty_name=trdnm,
                    document_kind="INVOICE",
                    document_number=inv["inum"], document_date=_date(inv["idt"]),
                    item_number=item.get("num"), rate=_amount(item.get("rt")),
                    taxable_value=_amount(item.get("txval")),
                    igst_amount=_amount(item.get("igst")), cgst_amount=_amount(item.get("cgst")),
                    sgst_amount=_amount(item.get("sgst")), cess_amount=_amount(item.get("cess")),
                    itc_available=None if itcavl is None else itcavl == "Y",
                    itc_unavailable_reason=inv.get("rsn") or None,
                    place_of_supply=inv.get("pos"),
                    reverse_charge=None if rev is None else rev == "Y",
                    attributes=attrs,
                ))
    return lines


def _parse_impg(rows: list[dict[str, Any]]) -> list[ParsedLine]:
    return [
        ParsedLine(
            section="IMPG", counterparty_gstin=None, counterparty_name=None,
            document_kind="BILL_OF_ENTRY",
            document_number=row["boenum"], document_date=_date(row["boedt"]),
            item_number=None, rate=None, taxable_value=_amount(row.get("txval")),
            igst_amount=_amount(row.get("igst")), cgst_amount=decimal.Decimal("0"),
            sgst_amount=decimal.Decimal("0"), cess_amount=_amount(row.get("cess")),
            itc_available=None, itc_unavailable_reason=None,
            place_of_supply=None, reverse_charge=None,
            attributes={
                "reference_date": row.get("refdt"), "port_code": row.get("portcode"),
                "is_amended": row.get("isamd"),
            },
        )
        for row in rows
    ]


def _parse_isd(rows: list[dict[str, Any]]) -> list[ParsedLine]:
    return [
        ParsedLine(
            section="ISD", counterparty_gstin=row.get("ctin"), counterparty_name=row.get("trdnm"),
            document_kind="ISD_CREDIT",
            document_number=row["docnum"], document_date=_date(row["docdt"]),
            item_number=None, rate=None, taxable_value=None,
            igst_amount=_amount(row.get("igst")), cgst_amount=_amount(row.get("cgst")),
            sgst_amount=_amount(row.get("sgst")), cess_amount=_amount(row.get("cess")),
            itc_available=None, itc_unavailable_reason=None,
            place_of_supply=None, reverse_charge=None,
            attributes={"document_type": row.get("doctype")},
        )
        for row in rows
    ]


def _parse_itc_summary(itcsumm: dict[str, Any]) -> list[ParsedItcSummary]:
    out: list[ParsedItcSummary] = []
    for availability, key in (("AVAILABLE", "itcavl"), ("NOT_AVAILABLE", "itcnotavl")):
        for category, amounts in (itcsumm.get(key) or {}).items():
            out.append(ParsedItcSummary(
                category=category.upper(), availability=availability,
                igst_amount=_amount(amounts.get("igst")), cgst_amount=_amount(amounts.get("cgst")),
                sgst_amount=_amount(amounts.get("sgst")), cess_amount=_amount(amounts.get("cess")),
            ))
    return out


@dataclass(frozen=True, slots=True)
class Gstr2bOutcome:
    batch_id: uuid.UUID | None
    """`None` on an unchanged statement — no `ingest_batch` row is written
    at all in that case (nothing changed, so there is nothing for Pipeline 2
    to be told about), which `src/dispatch/router.py::_publish_if_ready`
    already treats as "nothing to publish", the same as a QUARANTINED
    outcome."""
    inserted: bool
    """False means an unchanged statement was found and nothing was
    written — the same "second run of an unchanged file no-ops" idempotency
    `sales_register.py`'s `UpsertOutcome` documents, at the granularity of
    one whole statement rather than per-invoice, since a 2B statement is one
    document, not many."""
    line_count: int
    itc_summary_count: int


class Gstr2bLoader:
    """Upserts one parsed GSTR_2B statement. Whole-statement supersession,
    not per-line — unlike `sales_register.py` (many independent invoices
    per file), a 2B statement is one document with a natural key of
    (entity_id, gstin, tax_period), so a changed statement replaces itself
    entirely rather than being diffed line by line."""

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
    ) -> Gstr2bOutcome:
        silver = ctx.silver_schema
        ingest_run_id = ingest_run_id or uuid.uuid4()
        parsed = parse_gstr_2b(data, expected_period=period_start)

        batch_id = uuid.uuid4()
        content_hash = hashlib.sha256(data).digest()

        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            live = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT id, document_hash FROM {}.gstr_2b"
                        " WHERE entity_id = %s AND gstin = %s AND tax_period = %s"
                        "   AND superseded_at IS NULL"
                        " FOR UPDATE"
                    ).format(sql.Identifier(silver)),
                    (entity_id, gstin, period_start),
                )
            ).fetchone()

            if live is not None:
                live_id, live_hash = cast("tuple[int, str]", live)
                if live_hash == parsed.document_hash:
                    logger.info(
                        "gstr_2b %s unchanged for %s %s %s", ingest_id, ctx, gstin, period_start,
                    )
                    return Gstr2bOutcome(
                        batch_id=None, inserted=False,
                        line_count=len(parsed.lines), itc_summary_count=len(parsed.itc_summary),
                    )
                await conn.execute(
                    sql.SQL(
                        "UPDATE {}.gstr_2b SET superseded_at = now(),"
                        " modified_at = now(), modified_by = current_user"
                        " WHERE id = %s"
                    ).format(sql.Identifier(silver)),
                    (live_id,),
                )

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
                    len(parsed.lines), content_hash, str(ingest_id), ingest_run_id,
                ),
            )

            header_row = await (
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.gstr_2b (
                            entity_id, gstin, tax_period, batch_id, bronze_ingest_id,
                            row_hash, document_hash, generated_at, source_version,
                            valid_from
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """
                    ).format(sql.Identifier(silver)),
                    (
                        entity_id, gstin, period_start, batch_id, ingest_id,
                        parsed.document_hash, parsed.document_hash,
                        parsed.generated_at, parsed.source_version, period_start,
                    ),
                )
            ).fetchone()
            header_id = cast("tuple[int]", header_row)[0]

            for line_number, ln in enumerate(parsed.lines, start=1):
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.gstr_2b_line (
                            header_id, line_number, row_hash, section,
                            counterparty_gstin, counterparty_name, document_kind,
                            document_number, document_date, item_number, rate,
                            taxable_value, igst_amount, cgst_amount, sgst_amount,
                            cess_amount, itc_available, itc_unavailable_reason,
                            place_of_supply, reverse_charge, attributes
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                  %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(silver)),
                    (
                        header_id, line_number, ln.row_hash, ln.section,
                        ln.counterparty_gstin, ln.counterparty_name, ln.document_kind,
                        ln.document_number, ln.document_date, ln.item_number, ln.rate,
                        ln.taxable_value, ln.igst_amount, ln.cgst_amount, ln.sgst_amount,
                        ln.cess_amount, ln.itc_available, ln.itc_unavailable_reason,
                        ln.place_of_supply, ln.reverse_charge, json.dumps(ln.attributes),
                    ),
                )

            for summary in parsed.itc_summary:
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.gstr_2b_itc_summary (
                            header_id, category, availability,
                            igst_amount, cgst_amount, sgst_amount, cess_amount
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(silver)),
                    (
                        header_id, summary.category, summary.availability,
                        summary.igst_amount, summary.cgst_amount,
                        summary.sgst_amount, summary.cess_amount,
                    ),
                )

        logger.info(
            "gstr_2b %s -> batch %s for %s %s %s: %d lines, %d itc summary rows",
            ingest_id, batch_id, ctx, gstin, period_start,
            len(parsed.lines), len(parsed.itc_summary),
        )
        return Gstr2bOutcome(
            batch_id=batch_id, inserted=True,
            line_count=len(parsed.lines), itc_summary_count=len(parsed.itc_summary),
        )
