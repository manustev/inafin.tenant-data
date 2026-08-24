"""Archetype-5 GSTN return parsers — one module per return type.

D3 (`HANDOFF-2026-08-19-categoryB.md`, decided 2026-08-19): unlike
`extraction_spec` (PDF label:value, uniform grammar) or `RegisterSpec` (flat
CSV, uniform grammar), a filed GSTN return's nested JSON has no shape
grammar can usefully describe across types — GSTR-2B is a 3-level grouped
list, GSTR-3B is a fixed-box dict, GSTR-9C is named reconciliation tables.
Each return type is bespoke Python here, the same "spec grammar buys
nothing" call already made for archetype 2's register types
(`src/extraction/register_types.py`, kept per-type on purpose).

`GSTR_2B` (`gstr2b.py`) and `GSTR_3B` (`gstr3b.py`) are built so far.
`GSTR_1`/`GSTR_9`/`GSTR_9C` are not.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Protocol

from src.core.tenant import TenantContext


class GstnReturnOutcomePort(Protocol):
    """The one shape every `Gstr*Outcome` already has independently
    (`Gstr2bOutcome`, `Gstr3bOutcome`) — `batch_id` is `None` on an
    unchanged resubmission, which `src/dispatch/router.py`'s
    `_publish_if_ready` already treats as nothing-to-publish.

    Declared as read-only `@property` rather than plain attributes: both
    concrete outcomes are frozen dataclasses, and mypy's Protocol matching
    treats a plain attribute as requiring a SETTABLE field — a frozen
    dataclass's read-only attribute fails that check even though it
    satisfies everything this dict ever actually does (read, never write).
    """

    @property
    def batch_id(self) -> uuid.UUID | None: ...
    @property
    def inserted(self) -> bool: ...


class GstnReturnLoaderPort(Protocol):
    """What `src/dispatch/router.py`'s `_GSTN_RETURN_LOADERS` dict holds —
    both `Gstr2bLoader` and `Gstr3bLoader` already satisfy this
    structurally, no inheritance needed (same idiom `VirusScanPort`'s
    adapters use)."""

    async def load(
        self,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        gstin: str,
        ingest_id: uuid.UUID,
        data: bytes,
        period_start: dt.date,
        period_end: dt.date,
        ingest_run_id: uuid.UUID | None = None,
    ) -> GstnReturnOutcomePort: ...
