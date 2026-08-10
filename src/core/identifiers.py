"""Single source of truth for every tenant-derived identifier.

ARCHITECTURE.md 5.4: schema and role names are derived from the tenant slug
"through one identifier-safe formatter, in one place". This is that place.
Nothing anywhere else in the codebase may build a schema or role name by
string concatenation — if it does, the static gate in scripts/check_isolation.py
fails the build.

Two invariants this module enforces:

1. A slug is validated before it is ever interpolated. ``validate_slug`` is the
   only gate, and every public function here calls it. A slug that reached a
   schema name unvalidated would be an injection site.

2. Composition into SQL goes through ``psycopg.sql.Identifier``, never f-strings.
   Identifier() quotes and escapes per libpq rules; f-strings do not.
"""

from __future__ import annotations

import re
from typing import Final

from psycopg import sql

# Lowercase, starts with a letter, 3..32 chars. Deliberately narrower than
# Postgres allows: the slug also becomes an S3 bucket component and a Kafka
# message key, and S3 bucket naming is the strictest of the three.
_SLUG_RE: Final = re.compile(r"^[a-z][a-z0-9_]{2,31}$")

# PHASE1-PLAN.md D2 — a slug is immutable once provisioned. It is embedded in
# schema names, role names, bucket names and Kafka keys; renaming is a migration,
# not an UPDATE.
MAX_SLUG_LEN: Final = 32


class InvalidSlugError(ValueError):
    """Raised when a tenant slug fails validation. Never caught silently."""


def validate_slug(slug: str) -> str:
    """Return ``slug`` if it is a legal tenant slug, else raise.

    Raises rather than returning a bool: a caller that forgets to check a bool
    still gets a safe identifier, whereas a caller that forgets to check an
    ``is_valid()`` result does not.
    """
    if not isinstance(slug, str):  # pragma: no cover - defensive, mypy blocks it
        raise InvalidSlugError(f"tenant slug must be str, got {type(slug).__name__}")
    if not _SLUG_RE.match(slug):
        raise InvalidSlugError(
            f"invalid tenant slug {slug!r}: must match {_SLUG_RE.pattern} "
            f"(lowercase, starts with a letter, 3-{MAX_SLUG_LEN} chars)"
        )
    return slug


# --- Schema names -----------------------------------------------------------


def bronze_schema(slug: str) -> str:
    return f"t_{validate_slug(slug)}_bronze"


def silver_schema(slug: str) -> str:
    return f"t_{validate_slug(slug)}_silver"


def gold_schema(slug: str) -> str:
    return f"t_{validate_slug(slug)}_gold"


def all_schemas(slug: str) -> tuple[str, str, str]:
    """Bronze, Silver, Gold — the three schemas that constitute one tenant."""
    return (bronze_schema(slug), silver_schema(slug), gold_schema(slug))


# --- Role names -------------------------------------------------------------


def ingest_role(slug: str) -> str:
    """Pipeline 1. DML on bronze + silver. No access to gold."""
    return f"t_{validate_slug(slug)}_ingest"


def recon_role(slug: str) -> str:
    """Pipeline 2. SELECT on silver v1_ views only. DML on gold. Cannot write silver."""
    return f"t_{validate_slug(slug)}_recon"


def support_role(slug: str) -> str:
    """Read-only. Assumed only by a deliberate, logged support action."""
    return f"t_{validate_slug(slug)}_support"


def all_roles(slug: str) -> tuple[str, str, str]:
    return (ingest_role(slug), recon_role(slug), support_role(slug))


# --- Object store -----------------------------------------------------------


def bucket_name(slug: str, prefix: str) -> str:
    """Top-level bucket per tenant (ARCHITECTURE.md 5.7).

    S3 bucket names are DNS labels: lowercase, no underscores. The slug allows
    underscores (Postgres identifiers) so they are mapped to hyphens here. The
    mapping is one-way but injective over the validated slug alphabet, because
    ``_`` is the only character that changes and ``-`` is not legal in a slug.
    """
    return f"{prefix}-{validate_slug(slug).replace('_', '-')}"


# --- SQL composition --------------------------------------------------------


def schema_ident(schema: str) -> sql.Identifier:
    """Wrap an already-derived schema name for safe SQL composition."""
    return sql.Identifier(schema)


def qualified(schema: str, table: str) -> sql.Identifier:
    """Fully-qualified ``schema.table`` for SQL composition.

    ARCHITECTURE.md 5.4 — every generated statement must be fully qualified.
    ``search_path`` is set to '' cluster-wide so an unqualified reference raises
    rather than silently resolving to another tenant's table via a cached plan.
    """
    return sql.Identifier(schema, table)


# The Kafka message key. Partitions by tenant so per-tenant ordering holds
# without topic-per-tenant (ARCHITECTURE.md 5.10).
def kafka_key(slug: str) -> bytes:
    return validate_slug(slug).encode("utf-8")
