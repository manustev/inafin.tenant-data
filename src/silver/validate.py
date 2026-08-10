"""Bronze -> Silver validation and cleansing, driven by the registry contract.

The pipeline stage the user described: "checks some basic field types, mandatory
fields and cleansing". Everything here is *structural* — types, mandatory
fields, internal arithmetic consistency, tax-head shape.

What this stage deliberately does NOT do is judge tax treatment. Whether the
rate is correct, whether place of supply was derived properly, whether ITC is
blocked under s.17(5) — those are reconciliation questions, answered by v2
against a bitemporal corpus. Mixing them in here would put law in the ingestion
path, where it cannot be re-evaluated as-of a date when the law changes.

A file is accepted whole or quarantined whole. Partial acceptance would leave a
batch whose row_count does not describe its contents, and the completeness gate
downstream (anomaly T9) depends on that count meaning something.

**One validator, eleven document types.** What is mandatory for a shipping bill
and not for a purchase register comes from
`platform_ref.document_type.field_contract` (shared 009), parsed by
`src.silver.contract`. Nothing in this module names a document type — if it ever
has to, the archetype abstraction has failed (ARCHITECTURE.md 6).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

from src.silver.contract import Attribute, Counterparty, FieldContract

#: Present on every archetype-1 document, whatever its type. Everything else a
#: type needs is an attribute in its contract.
REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset({
    "doc_number", "doc_date", "line_number",
    "hsn_sac", "quantity", "unit_price", "taxable_value", "gst_rate",
})

_GSTIN_RE: Final = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")
_HSN_RE: Final = re.compile(r"^[0-9]{4,8}$")
_HASH64_RE: Final = re.compile(r"^[A-Za-z0-9]{64}$")
_DATE_FORMATS: Final = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


@dataclass(frozen=True, slots=True)
class ValidatedLine:
    line_number: int
    hsn_sac: str
    description: str | None
    quantity: Decimal
    unit_of_measure: str | None
    unit_price: Decimal
    taxable_value: Decimal
    gst_rate: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal
    itc_amount: Decimal
    attributes: dict[str, object] = field(default_factory=dict)

    @property
    def tax_amount(self) -> Decimal:
        return self.cgst_amount + self.sgst_amount + self.igst_amount + self.cess_amount


@dataclass(frozen=True, slots=True)
class ValidatedDocument:
    doc_number: str
    doc_date: dt.date
    counterparty_gstin: str | None
    counterparty_name: str | None
    currency: str
    lines: tuple[ValidatedLine, ...]
    attributes: dict[str, object] = field(default_factory=dict)

    @property
    def total_taxable_value(self) -> Decimal:
        return sum((ln.taxable_value for ln in self.lines), Decimal(0))

    @property
    def total_tax_value(self) -> Decimal:
        return sum((ln.tax_amount for ln in self.lines), Decimal(0))

    @property
    def total_value(self) -> Decimal:
        return self.total_taxable_value + self.total_tax_value


@dataclass
class ValidationResult:
    documents: list[ValidatedDocument] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejections

    @property
    def row_count(self) -> int:
        return sum(len(doc.lines) for doc in self.documents)


# --- cleansing primitives ---------------------------------------------------
# Shared with the archetype-2 register validator when that lands; the contract
# type names in src/silver/contract.ATTR_TYPES are exactly these functions.


def _dec(raw: str, field_name: str, row_no: int, errs: list[str]) -> Decimal:
    try:
        return Decimal((raw or "0").strip() or "0")
    except InvalidOperation:
        errs.append(f"row {row_no}: {field_name} is not a number: {raw!r}")
        return Decimal(0)


def _date(raw: str, field_name: str, row_no: int, errs: list[str]) -> dt.date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    errs.append(f"row {row_no}: {field_name} is not a recognised date: {raw!r}")
    return None


def _clean_gstin(raw: str) -> str:
    return (raw or "").upper().replace(" ", "").strip()


def coerce_attribute(
    attr: Attribute, raw: str, row_no: int, errs: list[str]
) -> object | None:
    """Cleanse one contract-declared attribute. None means 'not supplied'.

    A value that fails its declared type is an ERROR, not a silent drop. An
    unparseable be_date that vanished would leave a Bill of Entry that looks
    complete and reconciles against nothing.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    match attr.type:
        case "text":
            return raw
        case "date":
            d = _date(raw, attr.name, row_no, errs)
            return d.isoformat() if d else None
        case "money" | "qty" | "rate":
            return str(_dec(raw, attr.name, row_no, errs))
        case "int":
            try:
                return int(raw)
            except ValueError:
                errs.append(f"row {row_no}: {attr.name} is not an integer: {raw!r}")
                return None
        case "gstin":
            gstin = _clean_gstin(raw)
            if not _GSTIN_RE.match(gstin):
                errs.append(f"row {row_no}: {attr.name} is not a valid GSTIN: {gstin!r}")
                return None
            return gstin
        case "hsn":
            if not _HSN_RE.match(raw):
                errs.append(f"row {row_no}: {attr.name} must be 4-8 digits, got {raw!r}")
                return None
            return raw
        case "hash64":
            if not _HASH64_RE.match(raw):
                errs.append(
                    f"row {row_no}: {attr.name} must be 64 alphanumeric characters, "
                    f"got {len(raw)}"
                )
                return None
            return raw
        case _:  # pragma: no cover — parse_contract rejects unknown types first
            errs.append(f"row {row_no}: {attr.name} has unsupported type {attr.type!r}")
            return None


def _collect_attributes(
    attrs: tuple[Attribute, ...],
    row: dict[str, str],
    row_no: int,
    errs: list[str],
) -> dict[str, object]:
    out: dict[str, object] = {}
    for attr in attrs:
        value = coerce_attribute(attr, row.get(attr.name, ""), row_no, errs)
        if value is None:
            if attr.required:
                errs.append(
                    f"row {row_no}: {attr.name} is mandatory for this document type"
                )
            continue
        out[attr.name] = value
    return out


# --- the validator ----------------------------------------------------------


def validate_transaction_csv(data: bytes, contract: FieldContract) -> ValidationResult:
    """Parse, cleanse and structurally validate an archetype-1 CSV export.

    One row per line item; rows sharing a document number and counterparty form
    one document.
    """
    result = ValidationResult()
    errs = result.rejections

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        errs.append(f"file is not valid UTF-8: {exc}")
        return result

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        errs.append("file has no header row")
        return result

    header = {(h or "").strip().lower() for h in reader.fieldnames}

    required = set(REQUIRED_COLUMNS)
    if contract.counterparty is Counterparty.REQUIRED:
        required.add("counterparty_gstin")
    elif contract.counterparty is Counterparty.FOREIGN:
        # No GSTIN exists to demand, but an unnamed overseas counterparty makes
        # the document unattributable — which is the whole point of holding it.
        required.add("counterparty_name")
    required.update(contract.required_doc_attributes)
    required.update(contract.required_line_attributes)

    missing = required - header
    if missing:
        errs.append(f"missing mandatory columns: {', '.join(sorted(missing))}")
        return result

    grouped: dict[tuple[str, str], list[tuple[int, dict[str, str]]]] = {}
    headers: dict[tuple[str, str], tuple[dt.date, str | None, str | None, str]] = {}
    doc_attrs: dict[tuple[str, str], dict[str, object]] = {}

    for row_no, raw_row in enumerate(reader, start=2):  # row 1 is the header
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}

        doc_number = row["doc_number"].strip()
        if not doc_number:
            errs.append(f"row {row_no}: doc_number is mandatory")
            continue

        gstin = _clean_gstin(row.get("counterparty_gstin", ""))
        if gstin and not _GSTIN_RE.match(gstin):
            errs.append(f"row {row_no}: counterparty_gstin is not valid: {gstin!r}")
            continue
        if not gstin and contract.counterparty is Counterparty.REQUIRED:
            errs.append(
                f"row {row_no}: counterparty_gstin is mandatory for this document type"
            )
            continue
        if gstin and contract.counterparty is Counterparty.FOREIGN:
            # Not a formatting quibble. A GSTIN on an import Bill of Entry means
            # the file is not what it claims to be, and treating an overseas
            # supplier as registered would fabricate an ITC claim.
            errs.append(
                f"row {row_no}: this document type has an overseas counterparty, "
                f"but a GSTIN was supplied: {gstin!r}"
            )
            continue

        name = row.get("counterparty_name", "") or None
        if name is None and contract.counterparty is Counterparty.FOREIGN:
            errs.append(f"row {row_no}: counterparty_name is mandatory for this document type")
            continue

        doc_date = _date(row["doc_date"], "doc_date", row_no, errs)
        if doc_date is None:
            if not any(e.startswith(f"row {row_no}: doc_date") for e in errs):
                errs.append(f"row {row_no}: doc_date is mandatory")
            continue

        key = (doc_number, gstin)
        grouped.setdefault(key, []).append((row_no, row))
        if key not in headers:
            headers[key] = (doc_date, gstin or None, name, row.get("currency") or "INR")
            doc_attrs[key] = _collect_attributes(
                contract.doc_attributes, row, row_no, errs
            )

    if not grouped and not errs:
        errs.append("file contains a header but no data rows")

    for key, rows in grouped.items():
        doc_date, cp_gstin, cp_name, currency = headers[key]
        lines = _validate_lines(rows, contract, errs)
        if not lines:
            continue

        seen = [ln.line_number for ln in lines]
        if len(seen) != len(set(seen)):
            errs.append(f"document {key[0]}: duplicate line_number values")

        result.documents.append(
            ValidatedDocument(
                doc_number=key[0],
                doc_date=doc_date,
                counterparty_gstin=cp_gstin,
                counterparty_name=cp_name,
                currency=currency,
                lines=tuple(sorted(lines, key=lambda x: x.line_number)),
                attributes=doc_attrs[key],
            )
        )

    return result


def _validate_lines(
    rows: list[tuple[int, dict[str, str]]],
    contract: FieldContract,
    errs: list[str],
) -> list[ValidatedLine]:
    lines: list[ValidatedLine] = []

    for row_no, row in rows:
        try:
            line_number = int(row["line_number"])
        except ValueError:
            errs.append(f"row {row_no}: line_number is not an integer")
            continue

        hsn = row["hsn_sac"].strip()
        if not _HSN_RE.match(hsn):
            errs.append(f"row {row_no}: hsn_sac must be 4-8 digits, got {hsn!r}")
            continue

        gst_rate = _dec(row["gst_rate"], "gst_rate", row_no, errs)
        if not (Decimal(0) <= gst_rate <= Decimal(100)):
            errs.append(f"row {row_no}: gst_rate {gst_rate} out of range 0-100")

        cgst = _dec(row.get("cgst_amount", "0"), "cgst_amount", row_no, errs)
        sgst = _dec(row.get("sgst_amount", "0"), "sgst_amount", row_no, errs)
        igst = _dec(row.get("igst_amount", "0"), "igst_amount", row_no, errs)

        # Structural only: a line is intra-state or inter-state, never both.
        # WHETHER the head is correct is a T3 anomaly for v2 to decide against
        # re-derived place of supply — not a question for ingestion.
        if igst > 0 and (cgst > 0 or sgst > 0):
            errs.append(
                f"row {row_no}: both IGST and CGST/SGST are non-zero — "
                f"a supply cannot be intra-state and inter-state at once"
            )

        lines.append(
            ValidatedLine(
                line_number=line_number,
                hsn_sac=hsn,
                description=row.get("description") or None,
                quantity=_dec(row["quantity"], "quantity", row_no, errs),
                unit_of_measure=row.get("uom") or row.get("unit_of_measure") or None,
                unit_price=_dec(row["unit_price"], "unit_price", row_no, errs),
                taxable_value=_dec(row["taxable_value"], "taxable_value", row_no, errs),
                gst_rate=gst_rate,
                cgst_amount=cgst,
                sgst_amount=sgst,
                igst_amount=igst,
                cess_amount=_dec(row.get("cess_amount", "0"), "cess_amount", row_no, errs),
                itc_amount=_dec(row.get("itc_amount", "0"), "itc_amount", row_no, errs),
                attributes=_collect_attributes(
                    contract.line_attributes, row, row_no, errs
                ),
            )
        )

    return lines
