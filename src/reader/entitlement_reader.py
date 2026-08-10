"""What v2 imports to answer the entitlement question.

    Was an active instrument of type T, covering HSN H, held by entity E
    on date D?

Thirty-three document types, one call. Reads through `v1_entitlement_instrument`
using the `recon` role, which holds no privilege on any Silver base table — the
view is the entire contract (ARCHITECTURE.md 2.4).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import cast

from psycopg import sql

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext

logger = logging.getLogger(__name__)


def hsn_prefixes(hsn: str) -> list[str]:
    """Expand an HSN into every level it belongs to.

        '84713010' -> ['84', '8471', '847130', '84713010']

    HSN is hierarchical, so an instrument scoped to '8471' covers a supply of
    '84713010'. Testing that with `scope_hsn @> LIKE '8471%'` cannot use an
    index, so the containment is INVERTED: the caller expands the queried code
    into its own prefixes and the stored array is tested for overlap (`&&`),
    which the GIN index answers directly.

    Levels are the GST chapter/heading/subheading/tariff-item structure, hence
    even lengths. A longer code (10-digit customs tariff items exist) keeps its
    full form as the final prefix so an exactly-scoped instrument still matches.
    """
    digits = "".join(c for c in hsn if c.isdigit())
    if not digits:
        return []
    out = [digits[:n] for n in range(2, len(digits) + 1, 2)]
    if digits not in out:
        out.append(digits)
    return out


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: uuid.UUID
    instrument_type: str
    issuing_authority: str
    instrument_number: str
    valid_from: dt.date
    valid_to: dt.date
    status: str
    scope_hsn: list[str] | None
    scope: dict[str, object]
    obligation: dict[str, object]
    bronze_ingest_id: uuid.UUID


class EntitlementReader:
    def __init__(self, pool: TenantScopedPool) -> None:
        self._pool = pool

    async def find(
        self,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        instrument_type: str,
        as_of: dt.date,
        hsn: str | None = None,
    ) -> list[Instrument]:
        """Instruments conferring the entitlement on `as_of`.

        Four predicates, each of which is load-bearing:

          status = 'ACTIVE'    an instrument inside its validity window can still
                               be SUSPENDED or CANCELLED, and both retrospectively
                               remove the entitlement. Expiry and standing are
                               independent axes.
          as_of BETWEEN ...    the date of the SUPPLY, not today. The LUT that
                               matters for a June invoice is the one valid in
                               June.
          superseded_at NULL   the current version of our belief.
          scope_hsn overlap    NULL scope_hsn means unrestricted and matches
                               everything — which is the common case, and getting
                               this backwards would deny every LUT-backed export.
        """
        prefixes = hsn_prefixes(hsn) if hsn else None
        async with self._pool.transaction(ctx, Role.RECON) as conn:
            rows = await (
                await conn.execute(
                    sql.SQL(
                        """
                        SELECT instrument_id, instrument_type, issuing_authority,
                               instrument_number, valid_from, valid_to, status,
                               scope_hsn, scope, obligation, bronze_ingest_id
                          FROM {}.v1_entitlement_instrument
                         WHERE entity_id = %s
                           AND instrument_type = %s
                           AND status = 'ACTIVE'
                           AND superseded_at IS NULL
                           AND %s BETWEEN valid_from AND valid_to
                           AND (%s::text[] IS NULL
                                OR scope_hsn IS NULL
                                OR scope_hsn && %s::text[])
                         ORDER BY valid_from DESC
                        """
                    ).format(sql.Identifier(ctx.silver_schema)),
                    (entity_id, instrument_type, as_of, prefixes, prefixes),
                )
            ).fetchall()

        typed = cast(
            "list[tuple[uuid.UUID, str, str, str, dt.date, dt.date, str, "
            "list[str] | None, dict[str, object], dict[str, object], uuid.UUID]]",
            rows,
        )
        return [Instrument(*r) for r in typed]

    async def is_entitled(
        self,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        instrument_type: str,
        as_of: dt.date,
        hsn: str | None = None,
    ) -> bool:
        """The A8/A10 predicate, reduced to a boolean.

        Absence is a finding, not an error: a claimed zero-rated export with no
        LUT in force on the invoice date is precisely what the reconciliation is
        looking for. Callers must treat False as a result.
        """
        found = await self.find(
            ctx, entity_id=entity_id, instrument_type=instrument_type,
            as_of=as_of, hsn=hsn,
        )
        return bool(found)
