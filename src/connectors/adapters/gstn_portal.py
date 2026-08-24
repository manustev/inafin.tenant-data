"""`GstnPortalConnector` — GST portal downloads with no published API.

Covers `source_system == "GSTN_PORTAL"`: `GST_REGISTRATION_CERTIFICATE_SELF`
(REG-06 self-certificate), `GST_REGISTRATION_AMENDMENT_HISTORY`,
`COMPOSITION_SCHEME_RECORDS`, `ISD_REGISTRATION_CERTIFICATE`,
`OPEN_SCN_REGISTER`, `DRC_01B_NOTICE`, `DRC_03_VOLUNTARY_PAYMENT`,
`AMNESTY_SETTLEMENT_ORDER`, `ADJUDICATION_HEARING_ORDER`,
`REFUND_APPLICATION_RFD_01`.

Unlike `GSTN_API`'s return-filing endpoints, none of these have a published
GSP-accessible API — a taxpayer or their authorised representative views and
downloads them from the logged-in GST portal UI (services > user services,
or services > refunds, or the notices/orders tab under "View Additional
Notices/Orders"). Two realistic ways to automate this once real access
exists, neither built here:

1. An RPA/headless-browser session against the portal, authenticated the
   same way a human would be (credentials + OTP), scraping the rendered page
   or its underlying XHR JSON.
2. A manual staff download queued through the ingestion surface that already
   exists (`POST /artefacts` upload) — at which point this connector is
   never invoked at all for that tenant/period, because the bytes arrived as
   a Category-A-shaped upload instead of a Category-B pull. This is a real,
   legitimate operating mode for `GSTN_PORTAL` types specifically (their
   registry `mode` column, unchanged by this session, already allows for it)
   and may turn out to be cheaper than building the scrape.

`OPEN_SCN_REGISTER`, `DRC_01B_NOTICE`, `AMNESTY_SETTLEMENT_ORDER` and
`ADJUDICATION_HEARING_ORDER` in particular are officer/system-generated
notices and orders under "View Additional Notices/Orders" — layout has
changed across GSTN portal versions before and will again, which is exactly
why this session's sample-data README flags the 3 specimen PDFs as cosmetic,
not a real extraction target yet.
"""

from __future__ import annotations

from typing import ClassVar

from src.connectors.adapters.base_http import HttpSourceConnector
from src.connectors.base import FetchedDocument


class GstnPortalConnector(HttpSourceConnector):
    source_system: ClassVar[str] = "GSTN_PORTAL"

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
            "GSTN_PORTAL has no published API — needs an RPA/portal-session "
            "adapter or a manual-upload operating mode; see module "
            "docstring for both options."
        )
