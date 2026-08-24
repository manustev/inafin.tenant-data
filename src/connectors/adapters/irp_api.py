"""`IrpApiConnector` — e-invoice Invoice Registration Portal API.

Covers `source_system == "IRP_API"`: `IRN_IRP_REGISTER`.

The IRP (one of several NIC-empanelled portals — NIC-IRP, Cygnet, Cleartax,
etc. — a taxpayer registers with exactly one) exposes a GetIrnDetails-style
lookup keyed on IRN, plus (for a taxpayer's own generated invoices) a
listing API scoped to their own GSTIN. Real access is per-IRP, authenticated
with client-id/client-secret plus a GSTIN-linked username/password —
credentials are IRP-specific and this adapter's `base_url`/`credential`
point at whichever IRP the tenant is actually registered with, not a
generic NIC endpoint.

Response shape mirrors this session's sample payload
(`B4_EInvoice_and_EWayBill/B4.01_IRN_Register/irn_sample_payload.json`): IRN
(a SHA-256-shaped hash), acknowledgement number and timestamp, the signed
QR-code payload, and the invoice content it was generated from — the IRP
timestamp is authoritative for invoice date/time under Rule 48(5), taking
precedence over whatever date the seller's own ERP recorded.

Already-live in this repo: `IRN_IRP_REGISTER` has `dispatch_mechanism =
ARCHETYPE1_PROMOTE` (Bronze->Silver side, sixth session) — this connector is
the missing Bronze-intake side for it. Wiring this adapter turns "someone
uploads the IRN register CSV" into "the platform pulls it," without any
change to the promotion path already built.
"""

from __future__ import annotations

from typing import ClassVar

from src.connectors.adapters.base_http import HttpSourceConnector
from src.connectors.base import FetchedDocument


class IrpApiConnector(HttpSourceConnector):
    source_system: ClassVar[str] = "IRP_API"

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
            "IRP_API live fetch not yet wired — needs a per-IRP client "
            "registration and GSTIN-linked credentials; see module "
            "docstring."
        )
