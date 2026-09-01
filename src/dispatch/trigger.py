"""Record a load trigger and dispatch it — the one implementation shared by
`POST /artefacts/{id}/trigger` and `tenantctl trigger` (the CLI's manual
local trigger, twelfth-session item #2).

WHY THIS EXISTS AS ITS OWN MODULE. Before this, "insert `load_trigger`, read
the ledger entry, infer the content format, fetch the bytes, call
`dispatch_load`, map `NoDispatchMechanismError`/`ValidationRejected` to a
recorded-but-not-dispatched outcome" lived entirely inside the HTTP route
handler (`src/api/routes_ingest.py`). Duplicating it for a CLI command would
have meant two copies of the exact ordering
`HANDOFF-2026-08-07.md`/`src/api/app.py`'s docstring argue for — durable
record first, then dispatch, in-process, no second remote hop — free to drift
apart. This module IS that ordering, once; both callers wrap it in whatever
error-reporting shape fits their surface (HTTP status codes for the route,
exit codes and stderr for the CLI).

WHAT THIS RAISES VERSUS WHAT IT RETURNS. Two different things can go wrong
with a trigger, and they are deliberately not the same shape:

  * `UnknownArtefact` (no such `ingest_id` for this tenant), `ValueError`
    from `infer_content_format` (an unrecognised file extension),
    `MissingDispatchFieldError` (a mechanism needs
    `period_start`/`period_end`/`gstin` and the caller didn't supply one),
    `UnknownExtractorError` (an archetype-3 type with no registry
    `extraction_spec`) — these are CALLER MISTAKES about the REQUEST. It was
    malformed or incomplete, so they propagate as exceptions for each caller
    to map onto its own error surface (HTTP 404/422, or a CLI exit code).

  * `SilverConstraintViolation` is a caller mistake about the FILE. Bronze
    took the bytes, a loader was found, and the database refused the row it
    built. Raised rather than folded because no Silver write survived: there
    is no batch to report and nothing was quarantined, so `TriggerOutcome`
    has no honest status to carry. Its `kind` splits CONFLICT (a unique
    index — a resubmission) from INVALID (a check, not-null, foreign-key or
    data-type constraint — a wrong value), because a client must respond to
    those differently; see `src/core/errors.py`. Every one of these reached
    the caller as an opaque HTTP 500 before 2026-09-01.

  * `NoDispatchMechanismError` and `ValidationRejected` are NOT caller
    mistakes — they are legitimate OUTCOMES of a well-formed trigger
    (UNROUTED: this type has no loader yet; QUARANTINED: the document's own
    content failed Silver's validation). Both are folded into
    `TriggerOutcome.status` rather than raised, because the trigger record
    itself succeeded — `load_trigger`'s row is real and durable either way.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import cast

import psycopg
from psycopg import sql

from src.bronze.service import BronzeIngestionService
from src.core.errors import (
    SilverConstraintViolation,
    UnknownArtefact,
    ValidationRejected,
)
from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.dispatch.content_format import infer_content_format
from src.dispatch.router import NoDispatchMechanismError, dispatch_load
from src.events.publisher import BatchPublisherPort
from src.extraction.reader import PdfTextPort
from src.provisioning.objectstore import ObjectStorePort


@dataclass(frozen=True, slots=True)
class TriggerOutcome:
    trigger_id: int
    ingest_id: uuid.UUID
    doc_type_code: str
    requested_at: dt.datetime
    status: str
    """One of `SilverReader.artefact_outcome`'s vocabulary
    (ACCEPTED/PARTIAL/QUARANTINED) once `dispatch_load` has actually run, or
    UNROUTED if `doc_type_code` has no `dispatch_mechanism` yet."""
    mechanism: str | None = None
    batch_id: uuid.UUID | None = None


async def record_and_dispatch_trigger(
    pool: TenantScopedPool,
    ctx: TenantContext,
    bronze: BronzeIngestionService,
    *,
    ingest_id: uuid.UUID,
    doc_type_code: str,
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    gstin: str | None = None,
    store: ObjectStorePort | None = None,
    reader: PdfTextPort | None = None,
    publisher: BatchPublisherPort | None = None,
) -> TriggerOutcome:
    """Insert `load_trigger`, then dispatch. See the module docstring for
    which failures raise and which become `TriggerOutcome.status`.

    `load_trigger` is written first and unconditionally — the durable record
    that a trigger was requested, independent of whether dispatch succeeds.
    Its FK to `artefact_ledger` means an unknown `ingest_id` for this tenant
    raises `UnknownArtefact` here, not a 404 — mapping that to a status code
    is the HTTP route's job, not this function's.
    """
    try:
        async with pool.transaction(ctx, Role.INGEST) as conn:
            row = await (
                await conn.execute(
                    sql.SQL(
                        "INSERT INTO {}.load_trigger (ingest_id, doc_type_code)"
                        " VALUES (%s, %s) RETURNING id, ingest_id, doc_type_code, requested_at"
                    ).format(sql.Identifier(ctx.bronze_schema)),
                    (ingest_id, doc_type_code),
                )
            ).fetchone()
    except psycopg.errors.ForeignKeyViolation as exc:
        # SCOPED TO THIS STATEMENT ON PURPOSE. `load_trigger`'s only foreign
        # key is to `artefact_ledger`, so a violation here can mean exactly
        # one thing. A ForeignKeyViolation from the dispatch below means
        # something else entirely — a Silver row referencing an unknown
        # batch, doc type or master value — and the route used to report
        # that as "no artefact for this tenant", which was wrong.
        raise UnknownArtefact(
            f"no artefact {ingest_id} for this tenant"
        ) from exc

    assert row is not None
    raw_trigger_id, raw_ingest_id, raw_doc_type_code, raw_requested_at = row
    trigger_id = cast(int, raw_trigger_id)
    resolved_ingest_id = cast(uuid.UUID, raw_ingest_id)
    resolved_doc_type_code = cast(str, raw_doc_type_code)
    requested_at = cast(dt.datetime, raw_requested_at)

    def _recorded_only(status: str) -> TriggerOutcome:
        return TriggerOutcome(
            trigger_id=trigger_id, ingest_id=resolved_ingest_id,
            doc_type_code=resolved_doc_type_code, requested_at=requested_at,
            status=status,
        )

    entry = await bronze.ledger_entry(ctx, ingest_id)
    content_format = infer_content_format(entry.original_filename)
    data = await bronze.fetch(ctx, ingest_id)

    try:
        outcome = await dispatch_load(
            ctx, pool,
            ingest_id=ingest_id, entity_id=entry.entity_id,
            doc_type_code=resolved_doc_type_code,
            data=data, content_format=content_format,
            period_start=period_start, period_end=period_end, gstin=gstin,
            store=store, reader=reader, publisher=publisher,
        )
    except NoDispatchMechanismError:
        return _recorded_only("UNROUTED")
    except ValidationRejected:
        return _recorded_only("QUARANTINED")
    except psycopg.errors.UniqueViolation as exc:
        raise _constraint_violation(exc, kind="CONFLICT") from exc
    except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as exc:
        # IntegrityError covers check (including a DOMAIN's check —
        # `platform_ref.gstin`, `tax_rate` — which Postgres reports as
        # SQLSTATE 23514 like any other), not-null, foreign-key and
        # exclusion violations. DataError covers the class that never
        # reaches a constraint at all because the value cannot be coerced:
        # numeric_value_out_of_range on `numeric(6,3)`, an invalid date
        # literal. Both are the same thing to a client — the file's contents
        # are wrong for that column — so both map to INVALID.
        raise _constraint_violation(exc, kind="INVALID") from exc

    return TriggerOutcome(
        trigger_id=trigger_id, ingest_id=resolved_ingest_id,
        doc_type_code=resolved_doc_type_code, requested_at=requested_at,
        status=outcome.status, mechanism=outcome.mechanism, batch_id=outcome.batch_id,
    )


def _constraint_violation(
    exc: psycopg.Error, *, kind: str
) -> SilverConstraintViolation:
    """Carry `psycopg`'s `Diagnostic` fields onto our own error type.

    `primary` alone ("duplicate key value violates unique constraint
    ...") is what a client saw as an opaque 500 body before this; the
    diagnostic fields are what make it actionable. Postgres does not
    populate every field for every error class, hence the Nones — a
    DataError typically names neither a constraint nor a column, and
    that is reported honestly rather than filled in with a guess.
    """
    diag = exc.diag
    return SilverConstraintViolation(
        (diag.message_primary or str(exc)).strip(),
        kind=kind,
        constraint=diag.constraint_name,
        column=diag.column_name,
        table=diag.table_name,
    )
