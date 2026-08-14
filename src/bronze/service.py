"""Bronze intake — receive an artefact, store it immutably, index it.

ARCHITECTURE.md 3.1. Bronze holds no business content: the bytes go to the
tenant's own bucket under compliance-mode Object Lock, and this service writes
the ledger row that indexes them.

Ordering is deliberate and not interchangeable:

    hash -> dedup check -> PUT object -> INSERT ledger row

The object is written BEFORE the ledger row. Reversed, a failure between the two
would leave a ledger row pointing at bytes that do not exist — the platform
would believe it holds evidence it cannot produce. In the chosen order a failure
leaves an orphaned object: invisible, harmless, and eventually reconciled. When
the artefact may be tendered in a GST proceeding, "we have it but haven't
indexed it" is a recoverable state and "we indexed it but don't have it" is not.

**Bronze is INSERT-only.** A ledger row is written once, when the bytes land,
and is never revised — `ingest` holds SELECT and INSERT here and nothing else
(shared migration 006), so this is a privilege, not a convention. Disposition
(promoted, quarantined, superseded) is Silver's: deciding any of it requires
parsing, and Bronze holds no business content to parse. Do not add a column here
that records a conclusion we reached later.

**Intake gate, updated ordering:**

    file-check -> hash -> dedup check -> virus scan -> PUT object -> INSERT ledger row

Two stages were added ahead of the original `hash -> dedup -> PUT -> INSERT`
(see `src/bronze/filecheck.py` and `src/bronze/scan.py` for why each exists as
its own module). Both run BEFORE anything is written to the object store,
because Object Lock makes a stored object unmodifiable — a file that fails
either check must never become an evidentiary artefact at all. The virus scan
specifically runs AFTER the dedup check, not before: a duplicate upload reuses
the verdict already implied by the first upload having succeeded, so identical
bytes are never scanned twice.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import cast

import psycopg
from psycopg import sql

from src.bronze.filecheck import ALLOWED_EXTENSIONS, DEFAULT_MAX_UPLOAD_BYTES, check_file
from src.bronze.scan import NullScanner, VirusScanPort
from src.core.errors import IntakeRejected
from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext
from src.provisioning.objectstore import ObjectStorePort

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ArtefactLedgerEntry:
    """The metadata a dispatcher needs to route a trigger — not the bytes
    (`fetch`) and not a disposition (Bronze holds none)."""

    entity_id: uuid.UUID
    declared_document_type: str
    original_filename: str | None


@dataclass(frozen=True, slots=True)
class ArtefactReceipt:
    ingest_id: uuid.UUID
    content_hash: bytes
    bucket: str
    object_key: str
    size_bytes: int
    deduplicated: bool
    """True when this content was already held — the caller is being handed the
    prior ingest_id rather than a new one."""


class BronzeIngestionService:
    """Receives one artefact, runs the intake gate over it, and indexes it.

    The gate (file-check, dedup, virus scan) is described in this module's
    docstring; this class is what runs it in order and turns the result into
    either an `ArtefactReceipt` or an `IntakeRejected`.
    """

    def __init__(
        self,
        pool: TenantScopedPool,
        store: ObjectStorePort,
        *,
        bucket_prefix: str = "inafin-tenant",
        scanner: VirusScanPort | None = None,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
    ) -> None:
        """`scanner` defaults to `NullScanner()` — scanning off — rather than
        constructing a real adapter here. Which scanner runs is a deployment
        decision (`src.bronze.scan.build_scanner` reads it from `Settings`),
        and this constructor must not hardcode one; a caller that wants
        ClamAV, or a future commercial/cloud adapter, passes it in.
        """
        self._pool = pool
        self._store = store
        self._bucket_prefix = bucket_prefix
        self._scanner = scanner or NullScanner()
        self._max_upload_bytes = max_upload_bytes
        self._allowed_extensions = allowed_extensions

    async def receive(
        self,
        ctx: TenantContext,
        *,
        entity_id: uuid.UUID,
        data: bytes,
        document_type: str = "PURCHASE_INVOICE",
        source_stream: str = "B",
        filename: str | None = None,
        received_from: str = "upload",
        received_at: dt.datetime | None = None,
    ) -> ArtefactReceipt:
        """Run the intake gate over `data` and, if it passes, store and index it.

        Raises `IntakeRejected` if the file fails shape validation or the
        virus scan — in both cases nothing is written to the object store or
        the ledger, so a refused file leaves no trace of ever having arrived.
        That is deliberate: unlike a Silver quarantine (which keeps the bytes
        as evidence of what a client sent, because the file DID become an
        artefact), a file refused here never became an artefact in the first
        place, so there is nothing to retain a record of.
        """
        received_at = received_at or dt.datetime.now(dt.UTC)

        # --- File-check, before anything else touches `data` -----------------
        check = check_file(
            filename=filename, data=data,
            max_bytes=self._max_upload_bytes, allowed_extensions=self._allowed_extensions,
        )
        if not check.ok:
            logger.warning("bronze intake refused %s for %s: %s", filename, ctx, check.reason)
            raise IntakeRejected(check.reason)

        content_hash = hashlib.sha256(data).digest()
        bronze = ctx.bronze_schema

        # --- Dedup, scoped to this tenant by construction -------------------
        # The uniqueness domain is one tenant's schema, so a tenant can never
        # learn that another holds an identical file, and can never have its own
        # upload rejected as a duplicate of a document it cannot see.
        # ARCHITECTURE.md 9 finding 1 — do not centralise this.
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            existing = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT ingest_id, object_bucket, object_key, size_bytes"
                        " FROM {}.artefact_ledger WHERE content_hash = %s"
                    ).format(sql.Identifier(bronze)),
                    (content_hash,),
                )
            ).fetchone()
        if existing is not None:
            logger.info("bronze dedup hit for %s: %s", ctx, existing[0])
            prior = cast("tuple[uuid.UUID, str, str, int]", existing)
            return ArtefactReceipt(
                ingest_id=prior[0], content_hash=content_hash,
                bucket=prior[1], object_key=prior[2],
                size_bytes=prior[3], deduplicated=True,
            )

        # --- Virus scan, only for content not already held -------------------
        # ScannerUnavailableError (the daemon/API is unreachable) is deliberately
        # NOT caught here — it must fail the upload loudly rather than let an
        # infrastructure fault silently pass as "clean".
        scan_result = self._scanner.scan(data)
        if not scan_result.clean:
            logger.warning(
                "bronze intake refused %s for %s: %s flagged %s",
                filename, ctx, scan_result.scanner, scan_result.signature,
            )
            raise IntakeRejected(
                f"virus scan ({scan_result.scanner}) flagged this file"
                + (f": {scan_result.signature}" if scan_result.signature else "")
            )

        ingest_id = uuid.uuid4()

        # --- Object first ----------------------------------------------------
        stored = self._store.put_bronze(
            slug=ctx.slug, ingest_id=str(ingest_id), data=data,
            suffix=(filename or "").rsplit(".", 1)[-1] if filename and "." in filename else "bin",
            received_at=received_at,
        )

        # --- Then the index --------------------------------------------------
        try:
            async with self._pool.transaction(ctx, Role.INGEST) as conn:
                await conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.artefact_ledger (
                            ingest_id, entity_id, declared_document_type,
                            source_stream, received_at, content_hash, object_key,
                            object_bucket, size_bytes, original_filename,
                            received_from
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(bronze)),
                    (ingest_id, entity_id, document_type, source_stream, received_at,
                     content_hash, stored.key, stored.bucket, stored.size_bytes,
                     filename, received_from),
                )
        except psycopg.errors.UniqueViolation:
            # Concurrent identical upload won the race. Both objects are in the
            # store (harmless under WORM); the ledger keeps one. Return theirs.
            async with self._pool.transaction(ctx, Role.INGEST) as conn:
                row = await (
                    await conn.execute(
                        sql.SQL(
                            "SELECT ingest_id, object_bucket, object_key, size_bytes"
                            " FROM {}.artefact_ledger WHERE content_hash = %s"
                        ).format(sql.Identifier(bronze)),
                        (content_hash,),
                    )
                ).fetchone()
            assert row is not None
            winner = cast("tuple[uuid.UUID, str, str, int]", row)
            return ArtefactReceipt(
                ingest_id=winner[0], content_hash=content_hash, bucket=winner[1],
                object_key=winner[2], size_bytes=winner[3], deduplicated=True,
            )

        logger.info("bronze received %s for %s (%d bytes)", ingest_id, ctx, len(data))
        return ArtefactReceipt(
            ingest_id=ingest_id, content_hash=content_hash, bucket=stored.bucket,
            object_key=stored.key, size_bytes=stored.size_bytes, deduplicated=False,
        )

    async def fetch(self, ctx: TenantContext, ingest_id: uuid.UUID) -> bytes:
        """Re-read the byte-exact artefact. The replay path, and the evidence path."""
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            row = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT object_bucket, object_key FROM {}.artefact_ledger"
                        " WHERE ingest_id = %s"
                    ).format(sql.Identifier(ctx.bronze_schema)),
                    (ingest_id,),
                )
            ).fetchone()
        if row is None:
            raise KeyError(f"no bronze artefact {ingest_id} for {ctx}")

        bucket, key = cast("tuple[str, str]", row)
        # The expected bucket is derived from the tenant context, not trusted
        # from the row. ARCHITECTURE.md 5.7 — the slug is embedded redundantly
        # so a bucket-policy regression is still caught by a path assertion.
        expected = ctx.bucket(self._bucket_prefix)
        if bucket != expected:
            raise PermissionError(
                f"bronze object for {ctx} names bucket {bucket}, expected {expected}"
            )
        return self._store.get(bucket=bucket, key=key)

    async def ledger_entry(self, ctx: TenantContext, ingest_id: uuid.UUID) -> ArtefactLedgerEntry:
        """Read-only metadata lookup, alongside `fetch`'s bytes lookup.

        The dispatcher (`src/dispatch/router.py`) needs `entity_id` and the
        original filename to route a trigger; neither is on `TriggerRequest`,
        since the client already declared them at upload time. Bronze is
        read-only here — no invariant bent, just a second SELECT on the same
        row `fetch` already reads.
        """
        async with self._pool.transaction(ctx, Role.INGEST) as conn:
            row = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT entity_id, declared_document_type, original_filename"
                        " FROM {}.artefact_ledger WHERE ingest_id = %s"
                    ).format(sql.Identifier(ctx.bronze_schema)),
                    (ingest_id,),
                )
            ).fetchone()
        if row is None:
            raise KeyError(f"no bronze artefact {ingest_id} for {ctx}")

        entity_id, declared_document_type, original_filename = cast(
            "tuple[uuid.UUID, str, str | None]", row
        )
        return ArtefactLedgerEntry(
            entity_id=entity_id,
            declared_document_type=declared_document_type,
            original_filename=original_filename,
        )
