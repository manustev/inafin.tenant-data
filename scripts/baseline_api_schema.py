#!/usr/bin/env python3
"""Record API migrations that were deliberately applied outside the runner.

This one-time recovery tool verifies the essential resulting schema before
recording immutable migration checksums. It never runs application DDL.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import psycopg
from psycopg import sql

from src.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]
SHARED = (
    "020_app_login_api_grants.sql",
    "021_api_tenant_boundary.sql",
    "022_api_admin_tenancy.sql",
)
TENANT = (
    "021_connector_configuration.sql",
    "022_workspace_runtime.sql",
    "023_datahub_upload_request.sql",
    "024_datahub_upload_idempotency.sql",
)
TENANT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,31}$")


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_schema(conn: psycopg.Connection, tenant: str) -> None:
    if not TENANT_PATTERN.fullmatch(tenant):
        raise RuntimeError(f"invalid tenant slug: {tenant}")
    expected_tables = (
        f"t_{tenant}_gold.connector_configuration",
        f"t_{tenant}_gold.workspace_assessment",
        f"t_{tenant}_gold.datahub_upload_request",
    )
    missing = [
        table
        for table in expected_tables
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None
    ]
    if missing:
        raise RuntimeError(f"{tenant}: expected API tables are absent: {', '.join(missing)}")
    version_table = sql.Identifier(f"t_{tenant}_silver", "__migration_version")
    for filename in TENANT:
        path = ROOT / "migrations" / "tenant" / filename
        wanted = checksum(path)
        conn.execute(
            sql.SQL(
                "INSERT INTO {} (filename, checksum) VALUES (%s, %s) "
                "ON CONFLICT (filename) DO NOTHING"
            ).format(version_table),
            (filename, wanted),
        )
        actual = conn.execute(
            sql.SQL("SELECT checksum FROM {} WHERE filename=%s").format(version_table),
            (filename,),
        ).fetchone()[0]
        if actual != wanted:
            raise RuntimeError(f"{tenant}: checksum mismatch for {filename}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dsn",
        default="",
        help="tenant_migrate PostgreSQL DSN (defaults to PG_MIGRATE_DSN from .env)",
    )
    parser.add_argument("--tenant", action="append", required=True, help="tenant slug; repeatable")
    args = parser.parse_args()
    dsn = args.dsn or Settings().pg_migrate_dsn
    with psycopg.connect(dsn, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('platform_ref.tenant_customer')").fetchone()[0] is None:
            raise RuntimeError("platform_ref.tenant_customer is absent; apply migration 021 first")
        admin_tables = (
            "admin_role",
            "admin_user",
            "admin_contact",
            "notification_channel",
            "escalation_rule",
            "deboarding_case",
        )
        for table in admin_tables:
            # information_schema hides columns that tenant_migrate cannot read;
            # pg_catalog remains visible and is appropriate for this structural check.
            exists = conn.execute(
                "SELECT 1 FROM pg_attribute a "
                "JOIN pg_class c ON c.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='platform_ref' AND c.relname=%s "
                "AND a.attname='tenant_id' AND NOT a.attisdropped",
                (table,),
            ).fetchone()
            if exists is None:
                raise RuntimeError(
                    f"platform_ref.{table}.tenant_id is absent; apply migration 022 first"
                )
        for filename in SHARED:
            path = ROOT / "migrations" / "shared" / filename
            wanted = checksum(path)
            conn.execute(
                "INSERT INTO app.shared_migration_version (filename, checksum) "
                "VALUES (%s, %s) ON CONFLICT (filename) DO NOTHING",
                (filename, wanted),
            )
            actual = conn.execute(
                "SELECT checksum FROM app.shared_migration_version WHERE filename=%s",
                (filename,),
            ).fetchone()[0]
            if actual != wanted:
                raise RuntimeError(f"shared: checksum mismatch for {filename}")
        for tenant in args.tenant:
            require_schema(conn, tenant)
    print("API migration bookkeeping is baselined.")


if __name__ == "__main__":
    main()
