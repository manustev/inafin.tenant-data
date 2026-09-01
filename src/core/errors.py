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


class AuthenticationError(TenantDataError):
    """The API request could not be resolved to a tenant.

    Raised by `src.api.auth.AuthPort` adapters — a missing, malformed, or
    unrecognised credential. Deliberately distinct from
    `TenantBoundaryViolation`: that is a transaction that reached the
    database and was stopped there; this is a request that never got that
    far, because nothing has said which tenant it is yet.
    """


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


class UnknownArtefact(TenantDataError):
    """The `ingest_id` a trigger names has no `artefact_ledger` row here.

    Raised by `src.dispatch.trigger` when `load_trigger`'s foreign key to
    `artefact_ledger` rejects the insert. Its own type rather than a leaked
    `psycopg.errors.ForeignKeyViolation` for one specific reason: a FK
    violation raised LATER, from a Silver write, means something entirely
    different (a row referenced an unknown batch, doc type or master value)
    and must not be reported as "no such artefact". Catching the psycopg type
    at the route could not tell the two apart; catching it around the one
    statement that can raise it for that reason can.
    """


class SilverConstraintViolation(TenantDataError):
    """A Silver write was refused by a database constraint.

    Bronze accepted the bytes, dispatch found a loader, and the loader built
    a row the database then rejected. That is neither a caller mistake about
    the REQUEST (those are `ValueError`s — an unknown extension, a missing
    dispatch field) nor a judgement Silver reached about the document
    (`ValidationRejected`, which quarantines). It is Postgres enforcing a
    constraint the published schema either did not express or the file did
    not honour, and before this type existed it reached the caller as an
    opaque 500.

    `kind` splits the two cases a client must handle differently:

      CONFLICT  a unique index fired. On the `*_current_uq` indexes this
                means "you already sent this document and the prior version
                is still current" — a resubmission, not malformed data. The
                client should not retry the same bytes.
      INVALID   a check, not-null, foreign-key or data-type constraint
                fired. The file's contents are wrong for this column. The
                client should fix the value and resend.

    `constraint`, `column` and `table` come from `psycopg`'s `Diagnostic`
    and are whatever the server chose to populate — all three are optional
    because Postgres does not fill every field for every error class.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        constraint: str | None = None,
        column: str | None = None,
        table: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.constraint = constraint
        self.column = column
        self.table = table
