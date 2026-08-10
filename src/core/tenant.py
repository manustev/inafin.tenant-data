"""TenantContext — the only legitimate carrier of tenant identity.

ARCHITECTURE.md 5.6 and PHASE1-PLAN.md 4.4. Three rules this type exists to
make structural rather than advisory:

1. **Required, never ambient.** Every repository call takes a ``TenantContext``
   explicitly. There is no contextvar fallback, no module global, no default.
   A caller that forgets one gets a TypeError at the call site — not a silent
   unscoped read.

2. **Validated at construction.** The slug is checked once, here. Downstream
   code composes schema and role names from a context that is already known
   good, so no other module needs to re-validate or remember to.

3. **Derived, never concatenated.** Schema and role names come from
   ``src.core.identifiers``. Nothing else in the codebase may build them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from src.core import identifiers


class Role(StrEnum):
    """Which pipeline is acting. Determines the role assumed for the transaction."""

    INGEST = "ingest"
    RECON = "recon"
    SUPPORT = "support"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable, hashable, validated tenant identity.

    ``frozen`` matters: a mutable context could be edited mid-transaction by a
    helper and change which tenant subsequent statements addressed.
    """

    slug: str
    tenant_id: UUID | None = None

    def __post_init__(self) -> None:
        identifiers.validate_slug(self.slug)

    # --- schemas ---
    @property
    def bronze_schema(self) -> str:
        return identifiers.bronze_schema(self.slug)

    @property
    def silver_schema(self) -> str:
        return identifiers.silver_schema(self.slug)

    @property
    def gold_schema(self) -> str:
        return identifiers.gold_schema(self.slug)

    # --- roles ---
    def role_name(self, role: Role) -> str:
        match role:
            case Role.INGEST:
                return identifiers.ingest_role(self.slug)
            case Role.RECON:
                return identifiers.recon_role(self.slug)
            case Role.SUPPORT:
                return identifiers.support_role(self.slug)

    def guard_schema(self, role: Role) -> str:
        """The schema asserted in the transaction preamble.

        Silver for every role: it is the only schema all three hold USAGE on,
        so it is the one schema whose identity row every role can read. Bronze
        and Gold are protected by the USAGE boundary itself — recon has no
        USAGE on bronze, ingest none on gold.
        """
        _ = role
        return self.silver_schema

    def bucket(self, prefix: str) -> str:
        return identifiers.bucket_name(self.slug, prefix)

    def __str__(self) -> str:
        return f"tenant:{self.slug}"
