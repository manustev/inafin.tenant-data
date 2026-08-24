"""`GstnApiConnector` — GST Network's return-filing and taxpayer-status API.

Covers every `platform_ref.document_type` row whose `source_system` is
`GSTN_API`: `GSTR_1`, `GSTR_1_ARN`, `GSTR_2B`, `GSTR_2B_AMENDMENT_HISTORY`,
`GSTR_3B`, `GSTR_9`, `GSTR_9C`, `GSTR_2A`, `GSTR_6`, `GSTR_7`, `GSTR_8`,
`GST_REGISTRATION_CERTIFICATE_CUSTOMER`/`_SUPPLIER`, `GSTIN_STATUS_AT_DATE`.

GSTN does not issue direct API credentials to ordinary taxpayers or third
parties — access is via a licensed GSP (GST Suvidha Provider) or an ASP
sitting on top of one, authenticated per-session against the taxpayer's own
GSTN login (OTP-based) or, for a GSP's own application credentials, a
client-id/client-secret + session-token exchange the GSP issues. Real
endpoints are GSP-specific (each GSP wraps the same underlying GSTN API set
slightly differently), so `base_url`/`credential` here point at whichever
GSP this deployment eventually contracts — never GSTN's internal API
directly.

Per-type call shape, for when a GSP contract exists:

- `GSTR_1`/`GSTR_3B`/`GSTR_9`/`GSTR_9C`/`GSTR_6`/`GSTR_7`/`GSTR_8`: one call
  per (GSTIN, return period), GET-shaped, returning the decrypted JSON body
  this session's sample data mirrors (`gstin`, `ret_period`/`rtnprd`, the
  return's own table sections). `GSTR_9`/`GSTR_9C` are annual — period is a
  financial year, not a month.
- `GSTR_2B`: same per-(GSTIN, period) shape, but the live response arrives
  wrapped in GSTN's transport envelope (`status_cd`, `rek`, base64+AES `data`
  payload) — decrypting that envelope is this adapter's job before
  `FetchedDocument.content` is produced, never Bronze's.
- `GSTR_1_ARN`/filing-date lookups and `GSTIN_STATUS_AT_DATE`: point lookups,
  not periodic documents — `period` may be `None` for the latter (status "as
  of" a caller-supplied date is a query parameter, not a folder axis).
- `GST_REGISTRATION_CERTIFICATE_CUSTOMER`/`_SUPPLIER`: the counterparty
  registration-status API keyed on the counterparty's own GSTIN, not the
  filing taxpayer's — `gstin` here means "whose registration," which the
  caller must supply explicitly per counterparty, not resolve from `ctx`.
"""

from __future__ import annotations

from typing import ClassVar

from src.connectors.adapters.base_http import HttpSourceConnector
from src.connectors.base import FetchedDocument


class GstnApiConnector(HttpSourceConnector):
    source_system: ClassVar[str] = "GSTN_API"

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
            "GSTN_API live fetch not yet wired — needs a GSP/ASP contract "
            "and session credentials; see module docstring for the call "
            "shape per doc_type_code once one exists."
        )
