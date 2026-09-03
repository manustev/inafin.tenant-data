"""Archetype 1 extractors — A4.08 GTA consignment note, A6.01 Bill of Entry.

Both synthesize a single-row CSV matching the type's registry `field_contract`
(`src/silver/contract.py`) and hand it, unchanged, to
`SilverPromotionService.promote_transaction_documents`. Confirmed against the
real specimens (streamed-marinating-gray.md's verification method):

  * **A4.08 fully extracts.** Every fact `validate_transaction_csv` needs
    (freight amount, the RCM-computed GST, the consignment note number/date,
    the GTA's own GSTIN) is a clean `Label: Value` line.
  * **A6.01 now fully extracts too.** `taxable_value`/`igst_amount` sit in a
    two-column "Particular / Amount" table with NO colon between label and
    figure — pypdf's flattened text renders it as
    `"Assessable (Customs) Value 1,24,50,000"`. This was an accepted tier-1
    limitation until a follow-up ERP upload E2E report (2026-09-02) asked
    for it directly: `labelvalue.py`'s `_find_value` gained a second,
    colon-less matching mode for `money` fields specifically — label at line
    start, a money-shaped token at line end, arbitrary prose between (so
    `"IGST @ 18% (on value + BCD) 24,05,595"` binds against the label
    `"IGST"` without the rate needing to be part of the label). See that
    module for the grammar; `test_extraction_transaction.py` now asserts
    `Extracted`, not `Partial`.

Corrective refactor (2026-08-11): `doc_type_code` and `label_spec` used to be
`ClassVar`s here. They are now constructor arguments — set by `platform_ref.
document_type.extraction_spec` at registry-build time
(`src/extraction/registry.py`) — same as every other archetype base. Only
`_csv_row` stays as real per-type Python: it is GST-rate arithmetic and cast
handling, not a label:value binding, so there is no grammar to move to data
(`TYPED-TABLES-PLAN.md`-style "what varies is data, what computes stays
code").
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from src.extraction.base import TransactionDocumentExtractor

#: A GTA's outward transport service always carries this SAC in this batch's
#: specimen set — not stated in the document (no GTA invoice prints its own
#: SAC), so it is a fixed business constant here, the same idiom
#: `EntitlementExtractor.issuing_authority` uses for a fact that never varies
#: by instance. 996791 = "Goods transport services", SAC ch. 9967.
_GTA_SAC = "996791"


class GtaConsignmentNoteExtractor(TransactionDocumentExtractor):
    """`doc_type_code`/`label_spec` come from the registry
    (`GTA_INVOICE_CONSIGNMENT_NOTE`'s `extraction_spec` cell,
    `registry/document_types.csv`) — only `_csv_row` is per-type code."""

    def _csv_row(self, fields: dict[str, object]) -> tuple[dict[str, str], dt.date]:
        doc_date = cast("dt.date", fields["doc_date"])
        taxable = Decimal(cast("str", fields["taxable_value"]))
        gst_amount = Decimal(cast("str", fields["gst_amount"]))
        # Deterministic, not guessed: rate = amount / base, the arithmetic
        # relationship every GST head satisfies — computed rather than
        # parsed from the value's trailing "(5%)" text, which is prose the
        # Label: Value grammar does not attempt to read.
        gst_rate = (
            (gst_amount / taxable * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if taxable
            else Decimal(0)
        )
        doc_number = cast("str", fields["doc_number"])
        row = {
            "doc_number": doc_number,
            "doc_date": doc_date.isoformat(),
            "counterparty_gstin": cast("str", fields["counterparty_gstin"]),
            "counterparty_name": cast("str", fields["counterparty_name"]),
            "line_number": "1",
            "hsn_sac": _GTA_SAC,
            "quantity": "1",
            "unit_price": str(taxable),
            "taxable_value": str(taxable),
            "gst_rate": str(gst_rate),
            # A Gujarat-to-Chhattisgarh consignment is inter-state — IGST,
            # not CGST/SGST. See test for the same reasoning cross-checked.
            "igst_amount": str(gst_amount),
            "cgst_amount": "0",
            "sgst_amount": "0",
            "consignment_note_number": doc_number,
            "consignment_note_date": doc_date.isoformat(),
            "rcm_applicable": "YES",
        }
        return row, doc_date


class BillOfEntryExtractor(TransactionDocumentExtractor):
    """`doc_type_code`/`label_spec` come from the registry (`BILL_OF_ENTRY`'s
    `extraction_spec` cell, `registry/document_types.csv`). `taxable_value`/
    `igst_amount` are declared required there and now bind cleanly against
    the colon-less "Particular / Amount" table every specimen uses —
    `labelvalue.py`'s table-row matching for `money` fields (see module
    docstring)."""

    def _csv_row(self, fields: dict[str, object]) -> tuple[dict[str, str], dt.date]:
        doc_date = cast("dt.date", fields["doc_date"])
        taxable = Decimal(cast("str", fields["taxable_value"]))
        igst = Decimal(cast("str", fields["igst_amount"]))
        gst_rate = (
            (igst / taxable * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if taxable
            else Decimal(0)
        )
        doc_number = cast("str", fields["doc_number"])
        row = {
            "doc_number": doc_number,
            "doc_date": doc_date.isoformat(),
            "counterparty_name": cast("str", fields["counterparty_name"]),
            "line_number": "1",
            "hsn_sac": cast("str", fields["hsn_sac"]),
            "quantity": "1",
            "unit_price": str(taxable),
            "taxable_value": str(taxable),
            "gst_rate": str(gst_rate),
            "igst_amount": str(igst),
            "cgst_amount": "0",
            "sgst_amount": "0",
            "be_number": doc_number,
            "be_date": doc_date.isoformat(),
            "port_code": cast("str", fields["port_code"]),
        }
        return row, doc_date


#: Archetype 1's PDF extractors are genuinely bespoke per-type code
#: (`_csv_row`'s GST-rate/cast logic) — this small, fixed dict is the
#: dispatch `src/extraction/registry.py` uses for archetype 1, the same way
#: `register_types.py.REGISTER_DOCUMENT_EXTRACTORS` is fixed code for
#: archetype 2. It is NOT the thing this refactor moved to data: adding a
#: THIRD archetype-1 PDF type still needs a `_csv_row` implementation, which
#: is real business logic, not a label:value mapping.
TRANSACTION_DOCUMENT_EXTRACTOR_CLASS_BY_DOC_TYPE: dict[str, type[TransactionDocumentExtractor]] = {
    "GTA_INVOICE_CONSIGNMENT_NOTE": GtaConsignmentNoteExtractor,
    "BILL_OF_ENTRY": BillOfEntryExtractor,
}
