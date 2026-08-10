"""tenantctl — operational entry points.

Deliberately small. Everything here is an operation someone will run against
production under time pressure, so each one either succeeds completely or
reports precisely what it left behind.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid

from src.core.config import Settings, get_settings
from src.core.errors import SchemaDriftError
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

    args = parser.parse_args()
    return {
        "migrate": cmd_migrate,
        "provision": cmd_provision,
        "drift": cmd_drift,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
