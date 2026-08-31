"""tenantctl — operational entry points.

Deliberately small. Everything here is an operation someone will run against
production under time pressure, so each one either succeeds completely or
reports precisely what it left behind.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys
import uuid

import psycopg

from src.bronze.scan import build_scanner
from src.bronze.service import BronzeIngestionService
from src.core.config import Settings, get_settings
from src.core.errors import SchemaDriftError
from src.core.pool import TenantScopedPool
from src.core.tenant import TenantContext
from src.dispatch.trigger import TriggerOutcome, record_and_dispatch_trigger
from src.events.publisher import BatchPublisher
from src.extraction.reader import FallbackPdfTextReader, PypdfReader
from src.migrate.runner import MigrationRunner
from src.provisioning.objectstore import S3ObjectStore
from src.provisioning.service import ProvisioningService


def _store(settings: Settings) -> S3ObjectStore | None:
    if not settings.s3_endpoint_url and not settings.s3_access_key:
        return None
    return S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket_prefix=settings.s3_bucket_prefix,
        retention_days=settings.bronze_retention_days,
    )


def cmd_migrate(_: argparse.Namespace) -> int:
    settings = get_settings()
    runner = MigrationRunner(settings.pg_migrate_dsn)

    shared = runner.apply_shared()
    print(f"shared: {len(shared)} applied {shared or ''}".strip())

    report = runner.apply_all_tenants(concurrency=4)
    for r in report.results:
        status = "ok" if r.ok else f"FAILED — {r.error}"
        print(f"  {r.slug}: {len(r.applied)} applied — {status}")

    # Drift is checked AFTER migrating, not before: the point is to catch a
    # tenant the fan-out silently skipped, not to refuse to start.
    try:
        runner.assert_no_drift()
        print("drift: none")
    except SchemaDriftError as exc:
        print(f"DRIFT: {exc}", file=sys.stderr)
        return 1

    return 0 if report.ok else 1


def cmd_provision(args: argparse.Namespace) -> int:
    settings = get_settings()
    svc = ProvisioningService(
        migrate_dsn=settings.pg_migrate_dsn,
        object_store=_store(settings),
        bucket_prefix=settings.s3_bucket_prefix,
    )
    t = svc.provision(args.slug, uuid.UUID(args.tenant_id) if args.tenant_id else None)
    print(f"provisioned {t.slug}  tenant_id={t.tenant_id}")
    print(f"  schemas: {', '.join(t.schemas)}")
    print(f"  roles:   {', '.join(t.roles)}")
    print(f"  bucket:  {t.bucket}")
    return 0


async def _run_trigger(
    settings: Settings,
    slug: str,
    ingest_id: uuid.UUID,
    doc_type_code: str,
    period_start: dt.date | None,
    period_end: dt.date | None,
    gstin: str | None,
) -> TriggerOutcome:
    """Assembles the same pieces `src/api/app.py`'s lifespan builds for a
    long-running server — pool, store, a scanner, the OCR-fallback reader,
    the Kafka publisher — but opens and closes them for this ONE call rather
    than holding them for a process lifetime. Kept as its own function (not
    inlined into `cmd_trigger`) so `asyncio.run` wraps exactly this and
    nothing else — argument parsing and error-to-exit-code mapping stay
    synchronous, in `cmd_trigger`.

    Deliberately NOT sharing a constructor with `app.py`'s lifespan: that one
    is scoped to FastAPI's startup/shutdown hooks and this one is scoped to a
    single CLI invocation with different pool sizing needs (a handful of
    connections, not the app's `app_pool_max_size`). If the two drift apart in
    which pieces they build, that is a real thing to notice — keeping them
    textually separate is what makes a missing piece here visible on read
    rather than hidden behind a shared helper neither call site fully needs.
    """
    pool = TenantScopedPool(settings.pg_app_dsn, min_size=1, max_size=4)
    await pool.open()

    batch_publisher = BatchPublisher(
        settings.kafka_bootstrap, settings.kafka_batch_topic, enabled=settings.kafka_enabled,
    )
    await batch_publisher.start()

    try:
        store = _store(settings)
        if store is None:
            raise RuntimeError(
                "no S3/MinIO configuration (s3_endpoint_url / s3_access_key) — "
                "a trigger needs object storage to fetch the artefact's bytes"
            )
        bronze = BronzeIngestionService(
            pool, store, bucket_prefix=settings.s3_bucket_prefix,
            scanner=build_scanner(settings),
        )

        secondary_reader = None
        if settings.ocr_enabled:
            from src.extraction.ocr import PaddleOcrReader

            secondary_reader = PaddleOcrReader()
        reader = FallbackPdfTextReader(PypdfReader(), secondary_reader)

        ctx = TenantContext(slug)
        return await record_and_dispatch_trigger(
            pool, ctx, bronze,
            ingest_id=ingest_id, doc_type_code=doc_type_code,
            period_start=period_start, period_end=period_end, gstin=gstin,
            store=store, reader=reader, publisher=batch_publisher,
        )
    finally:
        await batch_publisher.stop()
        await pool.close()


def cmd_trigger(args: argparse.Namespace) -> int:
    """The manual local trigger — `tenantctl trigger` calls exactly the same
    `record_and_dispatch_trigger` the HTTP route calls
    (`POST /artefacts/{id}/trigger`), so a dispatch run from a terminal and
    one run from the API cannot silently diverge.

    EXIT CODE. 0 for ACCEPTED/PARTIAL — a real write happened. 1 for
    UNROUTED (no dispatch_mechanism for this type yet) and QUARANTINED (the
    document's own content failed Silver's validation) — both are honest,
    durably-recorded outcomes, not crashes, but neither wrote anything to
    Silver, and a script chaining this command should see that as
    "did not succeed" rather than silently continuing.
    """
    settings = get_settings()
    ingest_id = uuid.UUID(args.ingest_id)
    period_start = dt.date.fromisoformat(args.period_start) if args.period_start else None
    period_end = dt.date.fromisoformat(args.period_end) if args.period_end else None

    try:
        result = asyncio.run(
            _run_trigger(
                settings, args.slug, ingest_id, args.doc_type_code,
                period_start, period_end, args.gstin,
            )
        )
    except psycopg.errors.ForeignKeyViolation:
        print(f"ERROR: no artefact {ingest_id} for tenant {args.slug!r}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        # MissingDispatchFieldError, UnknownExtractorError, and
        # infer_content_format's plain ValueError are all ValueError
        # subclasses or instances — same fold record_and_dispatch_trigger's
        # own caller (the HTTP route) uses, see its docstring.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    line = f"trigger {result.trigger_id}: {result.doc_type_code} -> {result.status}"
    if result.mechanism:
        line += f" ({result.mechanism})"
    if result.batch_id:
        line += f" batch={result.batch_id}"
    print(line)
    return 0 if result.status in ("ACCEPTED", "PARTIAL") else 1


def cmd_drift(_: argparse.Namespace) -> int:
    settings = get_settings()
    drift = MigrationRunner(settings.pg_migrate_dsn).drift_report()
    if not drift:
        print("no drift")
        return 0
    for slug, pending in sorted(drift.items()):
        print(f"{slug}: {len(pending)} pending — {', '.join(pending)}")
    return 1


def main() -> int:
    logging.basicConfig(
        level=get_settings().log_level, format="%(levelname)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(prog="tenantctl")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply shared + all tenant chains, then check drift")

    p = sub.add_parser("provision", help="provision a new tenant")
    p.add_argument("slug")
    p.add_argument("--tenant-id", default=None)

    sub.add_parser("drift", help="report tenants not at head")

    p = sub.add_parser(
        "trigger",
        help="manually dispatch one already-uploaded artefact (local dev / backfill)",
    )
    p.add_argument("slug", help="tenant slug")
    p.add_argument("ingest_id", help="the artefact's ingest_id (from POST /artefacts)")
    p.add_argument("doc_type_code")
    p.add_argument("--period-start", default=None, help="YYYY-MM-DD")
    p.add_argument("--period-end", default=None, help="YYYY-MM-DD")
    p.add_argument("--gstin", default=None)

    args = parser.parse_args()
    return {
        "migrate": cmd_migrate,
        "provision": cmd_provision,
        "drift": cmd_drift,
        "trigger": cmd_trigger,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
