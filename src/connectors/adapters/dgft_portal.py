"""`DgftPortalConnector` — Directorate General of Foreign Trade portal data.

Covers `source_system == "DGFT_PORTAL"`: `DGFT_EBRC`, `DGFT_IEC_REGISTRY`,
`DGFT_EXPORT_OBLIGATION_STATUS`.

DGFT's portal (dgft.gov.in) has a registered-IEC-holder login area but no
broadly available public API for these three; access today is via portal
download by the IEC holder or their authorised representative, similar in
practice to `GSTN_PORTAL`'s situation.

Per-type shape:

- `DGFT_EBRC`: Electronic Bank Realisation Certificate — one row per
  shipping bill, realisation amount and date, days from invoice to
  realisation, status. This is the government-authenticated counterpart to
  a bank-issued FIRC/BRC and is what Rule 96A's 12-month realisation
  deadline is actually measured against (README's MOCK-06: an export
  invoice with no eBRC breaches this).
- `DGFT_IEC_REGISTRY`: IEC number, registered branches, RCMC (Registration-
  cum-Membership Certificate), and — the field this reconciliation actually
  needs — the GSTIN linked to each IEC branch. A branch with no GSTIN
  mapped breaks the ICEGATE-GSTN data flow for that branch entirely
  (README's MOCK-18).
- `DGFT_EXPORT_OBLIGATION_STATUS`: per EPCG/Advance Authorisation licence,
  export obligation imposed vs fulfilled and the obligation period — an
  expired period with a shortfall retroactively affects deemed-export
  zero-rating claims already taken (README's MOCK-19).
"""

from __future__ import annotations

from typing import ClassVar

from src.connectors.adapters.base_http import HttpSourceConnector
from src.connectors.base import FetchedDocument


class DgftPortalConnector(HttpSourceConnector):
    source_system: ClassVar[str] = "DGFT_PORTAL"

    async def _do_fetch(
        self,
        *,
        tenant_slug: str,
        doc_type_code: str,
        ref: str,
        gstin: str | None,
        period: str | None,
    ) -> FetchedDocument:
        raise NotImplementedError(
            "DGFT_PORTAL has no published API for these three types — "
            "needs an RPA/portal-session adapter; see module docstring."
        )
