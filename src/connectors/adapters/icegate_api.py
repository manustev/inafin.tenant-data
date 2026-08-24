"""`IcegateApiConnector` — ICEGATE (Indian Customs EDI) export/import data.

Covers `source_system == "ICEGATE_API"`: `ICEGATE_SHIPPING_BILL`,
`ICEGATE_EGM_STATUS`, `ICEGATE_BILL_OF_ENTRY`.

ICEGATE exposes a broker/CHA (Customs House Agent)-facing EDI interface for
shipping bill and bill-of-entry filing, and a public shipping-bill /
BoE-status tracking service (ICEGATE e-Sanchit / "Track Your Document").
Real third-party access is broker-mediated in practice — most importers/
exporters get this data via their CHA's software rather than a direct ICEGATE
API contract, so `base_url`/`credential` here most likely end up pointing at
a CHA integration or ICEGATE's own registered-user API, not a public
anonymous endpoint.

Per-type shape:

- `ICEGATE_SHIPPING_BILL`: one row per shipping bill — shipping bill number,
  date, port code, FOB value, invoice reference, exporter GSTIN/IEC. Queried
  by IEC + date range, not by GSTIN alone (IEC is the customs identity;
  GSTIN reconciliation happens downstream).
- `ICEGATE_EGM_STATUS`: per shipping bill, whether the Export General
  Manifest has been filed by the shipping line — this is what actually
  confirms goods left India and gates refund processing (README's MOCK-04
  anomaly is exactly a shipping bill with no EGM filed).
- `ICEGATE_BILL_OF_ENTRY`: import-side — assessable value, BCD, SWS, IGST
  paid at customs, out-of-charge date. This is the IGST-on-import figure
  that GSTR-2B's `impg` section is supposed to reflect from GSTN's own
  ICEGATE-GSTN data exchange — confirmed independently here because that
  exchange sometimes lags or drops rows (README's MOCK-17).
"""

from __future__ import annotations

from typing import ClassVar

from src.connectors.adapters.base_http import HttpSourceConnector
from src.connectors.base import FetchedDocument


class IcegateApiConnector(HttpSourceConnector):
    source_system: ClassVar[str] = "ICEGATE_API"

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
            "ICEGATE_API live fetch not yet wired — needs either a CHA "
            "integration or a registered-user ICEGATE API contract; see "
            "module docstring."
        )
