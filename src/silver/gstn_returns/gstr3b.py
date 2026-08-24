"""B1.05 GSTR_3B — parse the real GSTN API JSON shape, upsert into
`gstr_3b` / `gstr_3b_itc_detail` / `gstr_3b_inward_supply` (tenant
migration 027).

REAL SHAPE, CONFIRMED FROM ALL 31 SPECIMENS, NOT ASSUMED. Unlike GSTR_2B
(a grouped invoice list), GSTR-3B is genuinely a summary of FIXED BOXES:

    {"gstin", "ret_period", "form", "arn", "filing_date", "filing_status",
     "sup_details": {"osup_det", "osup_zero", "osup_nil_exmp",
                      "isup_rev", "osup_nongst"},
     "inter_sup": {"unreg_details": [], "comp_details": [], "uin_details": []},
     "itc_elg": {"itc_avl": [...5 rows...], "itc_rev": [...2 rows...],
                 "itc_net": {...}, "itc_inelg": [...2 rows...]},
     "inward_sup": {"isup_details": [...2 rows...]},
     "intr_ltfee": {"intr_details": {...}}}

Every `itc_avl`/`itc_rev`/`itc_inelg`/`isup_details` row's `ty` vocabulary
is IDENTICAL across all 31 real specimens (IMPG/IMPS/ISRC/ISD/OTH for
`itc_avl`; RUL/OTH for `itc_rev`/`itc_inelg`; GST/NONGST for
`isup_details`) — confirmed by direct count, not assumed from one file.

`inter_sup`'s three lists are EMPTY in all 31 real specimens — the same
situation `gstr2b.py` has for `cdnr`, resolved the same way:
`_reject_unparsed_inter_sup` refuses rather than guesses a shape with zero
ground truth.

`notes_for_mock` is the sample generator's own debug field (README:
"mock_flag/mock_note columns mark the seeded defects... strip them"), not
part of the real GSTN schema — ignored, not stored.
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

DOC_TYPE = "GSTR_3B"

#: The real payload's own closed vocabulary, per box — confirmed against all
#: 31 real specimens (this module's docstring). A `ty` outside this set is a
#: genuinely new box GSTN has never shown this workspace, so it is rejected
#: rather than silently stored under an unverified value.
_ITC_AVL_TYPES = frozenset({"IMPG", "IMPS", "ISRC", "ISD", "OTH"})
_ITC_REV_INELG_TYPES = frozenset({"RUL", "OTH"})
_INWARD_SUPPLY_TYPES = frozenset({"GST", "NONGST"})


def _hash(values: list[str]) -> str:
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def _amount(value: object) -> decimal.Decimal:
    if value is None:
        return decimal.Decimal("0")
    return decimal.Decimal(str(value))


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    return dt.datetime.strptime(value, "%d-%m-%Y").date()


#: A (taxable_value, igst, cgst, sgst, cess) amount tuple — the shape every
#: five-field sup_details box shares.
_D = decimal.Decimal
_FiveAmounts = tuple[_D, _D, _D, _D, _D]
#: (taxable_value, igst, cess) — osup_zero's shape (no cgst/sgst, see migration header).
_ThreeAmounts = tuple[_D, _D, _D]
#: (igst, cgst, sgst, cess) — itc_net / intr_details' shape (no taxable value).
_FourAmounts = tuple[_D, _D, _D, _D]


@dataclass(frozen=True, slots=True)
class ItcDetail:
    box: str
    itc_type: str
    igst_amount: decimal.Decimal
    cgst_amount: decimal.Decimal
    sgst_amount: decimal.Decimal
    cess_amount: decimal.Decimal


@dataclass(frozen=True, slots=True)
class InwardSupply:
    supply_type: str
    inter_state_amount: decimal.Decimal
    intra_state_amount: decimal.Decimal


@dataclass(frozen=True, slots=True)
class ParsedGstr3b:
    gstin: str
    return_period: str
    arn: str | None
    filing_date: dt.date | None
    filing_status: str | None
    osup_det: _FiveAmounts
    osup_zero: _ThreeAmounts
    osup_nil_exempt_taxable_value: decimal.Decimal
    isup_rev: _FiveAmounts
    osup_nongst_taxable_value: decimal.Decimal
    itc_net: _FourAmounts
    interest: _FourAmounts
    itc_detail: tuple[ItcDetail, ...]
    inward_supply: tuple[InwardSupply, ...]
    document_hash: str = field(init=False)

    def __post_init__(self) -> None:
        parts = [
            self.gstin, self.return_period, self.arn or "",
            *(str(v) for v in (*self.osup_det, *self.osup_zero,
                                self.osup_nil_exempt_taxable_value, *self.isup_rev,
                                self.osup_nongst_taxable_value, *self.itc_net, *self.interest)),
        ]
        parts.extend(
            _hash([d.box, d.itc_type, str(d.igst_amount), str(d.cgst_amount),
                   str(d.sgst_amount), str(d.cess_amount)])
            for d in self.itc_detail
        )
        parts.extend(
            _hash([s.supply_type, str(s.inter_state_amount), str(s.intra_state_amount)])
            for s in self.inward_supply
        )
        object.__setattr__(self, "document_hash", _hash(parts))


def parse_gstr_3b(data: bytes, *, expected_period: dt.date) -> ParsedGstr3b:
    """Raises `ValidationRejected` for malformed JSON, a missing required
    top-level field, a period mismatch, or a non-empty `inter_sup` list —
    see `_reject_unparsed_inter_sup`."""
    try:
        top = json.loads(data)
        gstin = cast(str, top["gstin"])
        return_period = cast(str, top["ret_period"])
        sup_details = top["sup_details"]
        itc_elg = top["itc_elg"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValidationRejected(f"malformed GSTR_3B payload: {exc}") from exc

    _reject_period_mismatch(return_period, expected_period)
    _reject_unparsed_inter_sup(top.get("inter_sup", {}))

    osup_det = sup_details.get("osup_det", {})
    osup_zero = sup_details.get("osup_zero", {})
    isup_rev = sup_details.get("isup_rev", {})
    itc_net = itc_elg.get("itc_net", {})
    intr = top.get("intr_ltfee", {}).get("intr_details", {})

    return ParsedGstr3b(
        gstin=gstin, return_period=return_period,
        arn=top.get("arn"), filing_date=_date(top.get("filing_date")),
        filing_status=top.get("filing_status"),
        osup_det=(
            _amount(osup_det.get("txval")), _amount(osup_det.get("iamt")),
            _amount(osup_det.get("camt")), _amount(osup_det.get("samt")),
            _amount(osup_det.get("csamt")),
        ),
        osup_zero=(
            _amount(osup_zero.get("txval")), _amount(osup_zero.get("iamt")),
            _amount(osup_zero.get("csamt")),
        ),
        osup_nil_exempt_taxable_value=_amount(sup_details.get("osup_nil_exmp", {}).get("txval")),
        isup_rev=(
            _amount(isup_rev.get("txval")), _amount(isup_rev.get("iamt")),
            _amount(isup_rev.get("camt")), _amount(isup_rev.get("samt")),
            _amount(isup_rev.get("csamt")),
        ),
        osup_nongst_taxable_value=_amount(sup_details.get("osup_nongst", {}).get("txval")),
        itc_net=(
            _amount(itc_net.get("iamt")), _amount(itc_net.get("camt")),
            _amount(itc_net.get("samt")), _amount(itc_net.get("csamt")),
        ),
        interest=(
            _amount(intr.get("iamt")), _amount(intr.get("camt")),
            _amount(intr.get("samt")), _amount(intr.get("csamt")),
        ),
        itc_detail=(
            *_parse_itc_rows("AVAILED", itc_elg.get("itc_avl", []), _ITC_AVL_TYPES),
            *_parse_itc_rows("REVERSED", itc_elg.get("itc_rev", []), _ITC_REV_INELG_TYPES),
            *_parse_itc_rows("INELIGIBLE", itc_elg.get("itc_inelg", []), _ITC_REV_INELG_TYPES),
        ),
        inward_supply=_parse_inward_supply(top.get("inward_sup", {}).get("isup_details", [])),
    )


def _reject_period_mismatch(return_period: str, expected_period: dt.date) -> None:
    expected = f"{expected_period.month:02d}{expected_period.year:04d}"
    if return_period != expected:
        raise ValidationRejected(
            f"GSTR_3B payload ret_period={return_period!r} does not match the "
            f"trigger's period_start ({expected_period.isoformat()}, expected {expected!r})"
        )


def _reject_unparsed_inter_sup(inter_sup: dict[str, Any]) -> None:
    """See module docstring: `unreg_details`/`comp_details`/`uin_details` are
    empty in every real specimen this workspace holds, so their row shape
    has never been verified."""
    for key in ("unreg_details", "comp_details", "uin_details"):
        if inter_sup.get(key):
            raise ValidationRejected(
                f"GSTR_3B payload has a non-empty inter_sup.{key}, which this "
                f"parser does not support yet (no real specimen exists to "
                f"verify its row shape against — see "
                f"src/silver/gstn_returns/gstr3b.py)"
            )


def _parse_itc_rows(
    box: str, rows: list[dict[str, Any]], allowed_types: frozenset[str],
) -> list[ItcDetail]:
    out = []
    for row in rows:
        ty = cast(str, row["ty"])
        if ty not in allowed_types:
            raise ValidationRejected(
                f"GSTR_3B itc_elg box {box!r} has an unrecognised ty={ty!r} "
                f"(expected one of {sorted(allowed_types)}) — see "
                f"src/silver/gstn_returns/gstr3b.py"
            )
        out.append(ItcDetail(
            box=box, itc_type=ty,
            igst_amount=_amount(row.get("iamt")), cgst_amount=_amount(row.get("camt")),
            sgst_amount=_amount(row.get("samt")), cess_amount=_amount(row.get("csamt")),
        ))
    return out


def _parse_inward_supply(rows: list[dict[str, Any]]) -> tuple[InwardSupply, ...]:
    out = []
    for row in rows:
        ty = cast(str, row["ty"])
        if ty not in _INWARD_SUPPLY_TYPES:
            raise ValidationRejected(
                f"GSTR_3B inward_sup has an unrecognised ty={ty!r} "
                f"(expected one of {sorted(_INWARD_SUPPLY_TYPES)}) — see "
                f"src/silver/gstn_returns/gstr3b.py"
            )
        out.append(InwardSupply(
            supply_type=ty,
            inter_state_amount=_amount(row.get("inter")),
            intra_state_amount=_amount(row.get("intra")),
        ))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Gstr3bOutcome:
    batch_id: uuid.UUID | None
    """`None` on an unchanged statement — same "nothing to publish"
    reasoning `Gstr2bOutcome.batch_id` already documents."""
    inserted: bool
    itc_detail_count: int
    inward_supply_count: int


class Gstr3bLoader:
    """Upserts one parsed GSTR_3B statement. Whole-statement supersession,
    same shape `Gstr2bLoader` already establishes: natural key
    (entity_id, gstin, tax_period), one document, not many independent rows."""

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
    ) -> Gstr3bOutcome:
        silver = ctx.silver_schema
        ingest_run_id = ingest_run_id or uuid.uuid4()
        parsed = parse_gstr_3b(data, expected_period=period_start)

        batch_id = uuid.uuid4()
        content_hash = hashlib.sha256(data).digest()

        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            live = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT id, document_hash FROM {}.gstr_3b"
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
                        "gstr_3b %s unchanged for %s %s %s", ingest_id, ctx, gstin, period_start,
                    )
                    return Gstr3bOutcome(
                        batch_id=None, inserted=False,
                        itc_detail_count=len(parsed.itc_detail),
                        inward_supply_count=len(parsed.inward_supply),
                    )
                await conn.execute(
                    sql.SQL(
                        "UPDATE {}.gstr_3b SET superseded_at = now(),"
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
                    len(parsed.itc_detail) + len(parsed.inward_supply),
                    content_hash, str(ingest_id), ingest_run_id,
                ),
            )

            header_row = await (
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.gstr_3b (
                            entity_id, gstin, tax_period, batch_id, bronze_ingest_id,
                            row_hash, document_hash, arn, filing_date, filing_status,
                            osup_det_taxable_value, osup_det_igst, osup_det_cgst,
                            osup_det_sgst, osup_det_cess,
                            osup_zero_taxable_value, osup_zero_igst, osup_zero_cess,
                            osup_nil_exempt_taxable_value,
                            isup_rev_taxable_value, isup_rev_igst, isup_rev_cgst,
                            isup_rev_sgst, isup_rev_cess,
                            osup_nongst_taxable_value,
                            itc_net_igst, itc_net_cgst, itc_net_sgst, itc_net_cess,
                            interest_igst, interest_cgst, interest_sgst, interest_cess,
                            valid_from
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                  %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """
                    ).format(sql.Identifier(silver)),
                    (
                        entity_id, gstin, period_start, batch_id, ingest_id,
                        parsed.document_hash, parsed.document_hash,
                        parsed.arn, parsed.filing_date, parsed.filing_status,
                        *parsed.osup_det, *parsed.osup_zero,
                        parsed.osup_nil_exempt_taxable_value,
                        *parsed.isup_rev, parsed.osup_nongst_taxable_value,
                        *parsed.itc_net, *parsed.interest,
                        period_start,
                    ),
                )
            ).fetchone()
            header_id = cast("tuple[int]", header_row)[0]

            for d in parsed.itc_detail:
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.gstr_3b_itc_detail (
                            header_id, box, itc_type,
                            igst_amount, cgst_amount, sgst_amount, cess_amount
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(silver)),
                    (header_id, d.box, d.itc_type, d.igst_amount, d.cgst_amount,
                     d.sgst_amount, d.cess_amount),
                )

            for s in parsed.inward_supply:
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.gstr_3b_inward_supply (
                            header_id, supply_type, inter_state_amount, intra_state_amount
                        ) VALUES (%s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(silver)),
                    (header_id, s.supply_type, s.inter_state_amount, s.intra_state_amount),
                )

        logger.info(
            "gstr_3b %s -> batch %s for %s %s %s: %d itc detail rows, %d inward supply rows",
            ingest_id, batch_id, ctx, gstin, period_start,
            len(parsed.itc_detail), len(parsed.inward_supply),
        )
        return Gstr3bOutcome(
            batch_id=batch_id, inserted=True,
            itc_detail_count=len(parsed.itc_detail),
            inward_supply_count=len(parsed.inward_supply),
        )
