"""`EwbPortalApiConnector` — E-Way Bill portal API.

Covers `source_system == "EWB_PORTAL_API"`: `EWAY_BILL_OUTWARD_REGISTER`.

The National Informatics Centre's e-way bill system (ewaybillgst.gov.in)
exposes a GSP-mediated API — same GSP relationship `GstnApiConnector`
depends on, often the same GSP contract in practice, but a distinct API
surface and distinct session/auth flow from the return-filing API. A
`GetEwayBillsByGSTIN`/date-range-shaped call is the realistic per-tenant
pull: EWB number, invoice reference, value, HSN, from/to state, transporter,
vehicle, validity window, and cancellation/extension events.

Already-live in this repo: `EWAY_BILL_OUTWARD_REGISTER` has
`dispatch_mechanism = ARCHETYPE1_PROMOTE` (sixth session) — same relationship
to the existing promotion path as `IrpApiConnector` has for
`IRN_IRP_REGISTER`; this is the missing intake side, not a new Silver
target.

`EWB_THRESHOLD_HISTORY` (`B4.03`) is a *different* type despite the shared
"e-way bill" subject — its `source_system` is `INAFIN_CORPUS`, not
`EWB_PORTAL_API` (it is bitemporal reference data, not a per-tenant pull),
and per `registry/README.md` §2 it belongs to the `inafin-gst-corpus` repo,
not to any connector built here.
"""

from __future__ import annotations

from typing import ClassVar

from src.connectors.adapters.base_http import HttpSourceConnector
from src.connectors.base import FetchedDocument


class EwbPortalApiConnector(HttpSourceConnector):
    source_system: ClassVar[str] = "EWB_PORTAL_API"

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
            "EWB_PORTAL_API live fetch not yet wired — needs the same "
            "GSP-mediated session GstnApiConnector needs; see module "
            "docstring."
        )
