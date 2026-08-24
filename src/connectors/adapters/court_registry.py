"""`CourtRegistryConnector` — court/tribunal stay orders. Structurally a
connector; realistically never automatable.

Covers `source_system == "COURT_REGISTRY"`: `COURT_STAY_ORDER`.

`registry/document_types.csv`'s own `source_system` for this type is
`COURT_REGISTRY`, distinct from every GST-specific system above — a High
Court, Supreme Court or CESTAT stay order is issued by a judicial registry
with no relationship to GSTN, ICEGATE or DGFT at all, and (per the sample
README's §5 limits: "there is no realistic structured feed to mock") no
court registry publishes a machine-queryable API a taxpayer or platform
could pull from. This class exists only so `factory.py`'s dispatch table has
an entry for every `source_system` the registry defines — its real "adapter"
will always be a human filing the order through the ordinary upload path
(Category-A-shaped intake, same as `GSTN_PORTAL`'s manual-upload fallback),
never this class's `_do_fetch`.
"""

from __future__ import annotations

from typing import ClassVar

from src.connectors.adapters.base_http import HttpSourceConnector
from src.connectors.base import FetchedDocument


class CourtRegistryConnector(HttpSourceConnector):
    source_system: ClassVar[str] = "COURT_REGISTRY"

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
            "COURT_STAY_ORDER has no automatable source — file it through "
            "the ordinary upload path instead of a connector; see module "
            "docstring."
        )
