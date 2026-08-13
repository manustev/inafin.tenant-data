"""Archetype 2 extractors — PAYROLL_TDS_REGISTER (A3.05), FIRC_BRC_REGISTER
(A5.14), FORM_15CA_15CB (A7.04).

Each `extract()` is genuinely per-type — a payroll table and an FX
realisation table share no columns, so unlike the label:value archetypes
there is no single shared parsing rule to reuse (`RegisterDocumentExtractor`
in `src/extraction/base.py` is what IS shared: rows -> CSV ->
`RegisterLoader`, unchanged). `FORM_15CA_15CB`'s specimen is, unusually for
this archetype, a single `Label: Value` certificate rather than a table —
reuses `parse_label_value` for the one row it has, rather than a bespoke
regex parser.

pypdf's text extraction wraps table cells across lines with no reliable
column delimiter (`"INV/EXP/24-\\n25/0142"`), so row boundaries below are
found by the one token each row's own layout keeps together (an employee
ref, an invoice number prefix) and cell boundaries by content-shaped regexes
— documented per-parser, since this is exactly the kind of layout-specific
work `CLAUDE.md` says not to guess at without a real sample; these ARE the
real samples.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import ClassVar

from src.extraction.base import RegisterDocumentExtractor
from src.extraction.labelvalue import (
    Extracted,
    ExtractionOutcome,
    LabelField,
    NoTextLayer,
    Partial,
    parse_label_value,
)
from src.silver.registers.catalog import SPEC_BY_DOC_TYPE

_TRAIL_RE = re.compile(r"(\d{3}[A-Z]?)\s+([\d,]+)\s+([\d,]+)\s*$")
_CLASS_RE = re.compile(r"\b(Employee|Contractor|Professional)\b")


class PayrollTdsRegisterExtractor(RegisterDocumentExtractor):
    doc_type_code: ClassVar[str] = "PAYROLL_TDS_REGISTER"  # type: ignore[misc]
    spec = SPEC_BY_DOC_TYPE["PAYROLL_TDS_REGISTER"]

    def extract(self, pages: list[str]) -> ExtractionOutcome:
        text = "\n".join(pages)
        if len(text.strip()) < 20:
            return NoTextLayer()

        # Cut the trailing note before splitting — it can itself contain a
        # ref-no-shaped token and would otherwise be mistaken for a row.
        body = text.split("Note:")[0]
        chunks = re.split(r"(?=\b(?:EMP|CONS)\d{4}\b)", body)
        rows: list[dict[str, str]] = []
        for chunk in chunks:
            m = re.match(r"^(EMP|CONS)\d{4}", chunk)
            if not m:
                continue
            ref_no = m.group(0)
            rest = re.sub(r"\s+", " ", chunk[len(ref_no) :]).strip()
            trail = _TRAIL_RE.search(rest)
            if not trail:
                continue
            tds_section, gross_amount, tds_deducted = trail.groups()
            middle = rest[: trail.start()].strip()
            classes = _CLASS_RE.findall(middle)
            if not classes:
                continue
            classification = classes[-1]
            # The last classification token often has trailing role
            # boilerplate ("(Director)") after it that this parser does not
            # attempt to separate from the classification word — same
            # accepted per-cell-boundary limitation the module docstring
            # names. person_name intentionally carries the row's leading
            # name+role text combined: pypdf's reflow removes the column
            # boundary between them, and both are `text` columns downstream.
            name_end = middle.rfind(classification)
            person_name = middle[:name_end].strip()
            rows.append(
                {
                    "ref_no": ref_no,
                    "person_name": person_name,
                    "role_title": "",
                    "classification": classification,
                    "tds_section": tds_section,
                    "gross_amount": gross_amount.replace(",", ""),
                    "tds_deducted": tds_deducted.replace(",", ""),
                }
            )

        if not rows:
            return Partial(fields={}, missing=("rows",))
        return Extracted(fields={"rows": rows})


_AD_BANKS: tuple[str, ...] = ("HDFC Bank, Fort Branch", "ICICI Bank, Nariman Point")
_FIRC_NO_RE = re.compile(r"FIRC/\d{4}/[A-Z]{2}/\s*\d+")
_FIRC_ROW_START_RE = re.compile(r"INV/EXP/\d{2}-\s*\d{2}/\d{4}")
_FIRC_HEADER_RE = re.compile(
    r"^(INV/EXP/\d{2}-\s*\d{2}/\d{4})\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+"
    r"([A-Z]{3})\s*([\d,]+)\s+(.*)$"
)


class FircBrcRegisterExtractor(RegisterDocumentExtractor):
    doc_type_code: ClassVar[str] = "FIRC_BRC_REGISTER"  # type: ignore[misc]
    spec = SPEC_BY_DOC_TYPE["FIRC_BRC_REGISTER"]

    def extract(self, pages: list[str]) -> ExtractionOutcome:
        text = "\n".join(pages)
        if len(text.strip()) < 20:
            return NoTextLayer()

        body = text.split("Both bank-issued")[0]
        chunks = re.split(rf"(?={_FIRC_ROW_START_RE.pattern})", body)
        rows: list[dict[str, str]] = []
        for chunk in chunks:
            flat = re.sub(r"\s+", " ", chunk).strip()
            m = _FIRC_HEADER_RE.match(flat)
            if not m:
                continue
            invoice_no, invoice_date_raw, currency, amount, trailing = m.groups()
            invoice_date = dt.datetime.strptime(invoice_date_raw, "%d-%b-%Y").date()

            bank = next((b for b in _AD_BANKS if trailing.startswith(b)), None)
            rest = trailing[len(bank) :].strip() if bank else trailing
            firc_match = _FIRC_NO_RE.search(rest)
            if firc_match:
                firc_no = re.sub(r"\s+", "", firc_match.group(0))
                status = rest[firc_match.end() :].strip()
            else:
                firc_no = ""
                status = re.sub(r"-\s+", "-", rest)

            rows.append(
                {
                    "export_invoice_no": re.sub(r"-\s+", "-", invoice_no).replace(" ", ""),
                    "invoice_date": invoice_date.isoformat(),
                    "amount_foreign": f"{currency} {amount}",
                    "ad_bank": bank or "",
                    "firc_no": firc_no,
                    "realisation_status": status,
                }
            )

        if not rows:
            return Partial(fields={}, missing=("rows",))
        return Extracted(fields={"rows": rows})


class Form15ca15cbExtractor(RegisterDocumentExtractor):
    """A degenerate one-row register: the specimen is a single certificate,
    `Label: Value` shaped like archetypes 3/6/7 rather than a table — see
    module docstring."""

    doc_type_code: ClassVar[str] = "FORM_15CA_15CB"  # type: ignore[misc]
    spec = SPEC_BY_DOC_TYPE["FORM_15CA_15CB"]

    def extract(self, pages: list[str]) -> ExtractionOutcome:
        outcome = parse_label_value(
            pages,
            {
                "form_15ca_ack_no": LabelField("Form 15CA Acknowledgement No.", "text"),
                "remittee_name": LabelField("Remittee", "text"),
                "remittance_amount_raw": LabelField("Remittance Amount", "text"),
                "nature_of_remittance": LabelField(
                    "Nature of Remittance", "text", required=False
                ),
                "form_15cb_cert_no": LabelField(
                    "Form 15CB CA Certificate No.", "text", required=False
                ),
                "classification": LabelField(
                    "Classification Under Income-tax Act", "text", required=False
                ),
            },
        )
        if not isinstance(outcome, Extracted):
            return outcome

        f = outcome.fields
        # The INR-equivalent figure sits in a parenthetical inside the same
        # value as the foreign-currency amount ("USD 24,000 ( 19,92,000
        # equivalent)₹") — pulled out specifically rather than via the
        # generic `money` type, which would bind the FIRST number (the USD
        # figure) instead.
        inr_match = re.search(r"\(\s*([\d,]+)\s*equivalent\)", str(f["remittance_amount_raw"]))
        row = {
            "form_15ca_ack_no": str(f["form_15ca_ack_no"]),
            "remittee_name": str(f["remittee_name"]),
            "remittance_amount_inr": inr_match.group(1).replace(",", "") if inr_match else "",
            "nature_of_remittance": str(f.get("nature_of_remittance") or ""),
            "form_15cb_cert_no": str(f.get("form_15cb_cert_no") or ""),
            "classification": str(f.get("classification") or ""),
            "tax_withheld_inr": "",
        }
        return Extracted(fields={"rows": [row]})


REGISTER_DOCUMENT_EXTRACTORS: dict[str, type[RegisterDocumentExtractor]] = {
    cls.doc_type_code: cls
    for cls in (PayrollTdsRegisterExtractor, FircBrcRegisterExtractor, Form15ca15cbExtractor)
}
