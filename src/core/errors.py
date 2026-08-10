"""Error types that carry isolation meaning.

A tenant boundary violation must never be indistinguishable from an ordinary
failure. These types exist so that a caller — and an alert rule — can tell the
difference between "the query found nothing" and "the query was stopped at the
tenant boundary".
"""

from __future__ import annotations


class TenantDataError(Exception):
    """Base for every error raised by this package."""


class TenantBoundaryViolation(TenantDataError):
    """A transaction touched, or tried to touch, the wrong tenant.

    Raised when ``app.assert_schema_owner`` rejects a schema, or when a role
    assumption does not match the intended tenant. This is a security event:
    it must be logged at ERROR with the tenant context, alerted on, and never
    retried automatically.
    """


class TenantNotProvisioned(TenantDataError):
    """The tenant has no registry row, or is not ACTIVE."""


class ProvisioningError(TenantDataError):
    """Provisioning failed partway. The tenant is in an indeterminate state.

    PHASE1-PLAN.md 6 — a half-provisioned tenant is a security state, not
    merely a broken one: schemas may exist without their grants. Provisioning
    is transactional in Postgres, so this signals the bucket/registry steps
    outside that transaction need reconciliation.
    """


class MigrationError(TenantDataError):
    """A migration failed for one or more schemas."""


class SchemaDriftError(TenantDataError):
    """One or more tenant schemas are not at head.

    Silent drift is how one tenant ends up two migrations behind and nobody
    notices until a query fails in production. This is raised by the drift
    check so it can alert rather than log.
    """


class ValidationRejected(TenantDataError):
    """A Bronze artefact failed Silver validation and was quarantined."""


class IntakeRejected(TenantDataError):
    """A file was refused at Bronze intake, before it became an artefact.

    Distinct from `ValidationRejected`: that is Silver's judgement about a
    document's CONTENT (missing GSTIN, a bad tax head) on bytes that are
    already stored under Object Lock. This is earlier and coarser — the file
    fails basic shape checks (empty, oversized, wrong extension) or a virus
    scan before it is ever written to the object store, so nothing about it
    becomes evidence at all. Raised by `src.bronze.filecheck` and
    `src.bronze.scan`.
    """
