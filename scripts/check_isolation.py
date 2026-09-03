#!/usr/bin/env python
"""Structural isolation gate — ARCHITECTURE.md 10.7.

Runs in CI **and** post-migrate in every environment. The second is the one that
matters: CI proves the migrations are correct in the abstract, this proves the
cluster in front of you actually is.

Expressed as RULES, not as a list of expected table names. A hardcoded list goes
stale the moment a migration adds a table, and a stale allowlist fails open —
the new table simply isn't checked. Every rule below holds for any number of
tables, so a table added in Phase 4 is covered without anyone remembering to
update this file.

    python scripts/check_isolation.py            # exits 1 on any finding
    python scripts/check_isolation.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

import psycopg

from src.core.config import get_settings
from src.core.identifiers import (
    all_roles,
    all_schemas,
    recon_engine_role,
    reconciliation_schema,
)

WRITE_PRIVILEGES = frozenset({"INSERT", "UPDATE", "DELETE", "TRUNCATE"})


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # CRITICAL | HIGH
    detail: str


def _tenants(conn: psycopg.Connection[tuple[object, ...]]) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT slug FROM app.tenant_registry"
            " WHERE status IN ('ACTIVE','PROVISIONING') ORDER BY slug"
        ).fetchall()
    ]


def check(conn: psycopg.Connection[tuple[object, ...]]) -> list[Finding]:
    findings: list[Finding] = []
    slugs = _tenants(conn)

    # --- Cluster-wide invariants -------------------------------------------

    for (rolname,) in conn.execute(
        "SELECT rolname FROM pg_roles WHERE rolbypassrls AND NOT rolsuper"
    ).fetchall():
        findings.append(
            Finding("no-bypassrls", "CRITICAL", f"role {rolname} has BYPASSRLS")
        )

    row = conn.execute(
        "SELECT rolinherit, rolcanlogin FROM pg_roles WHERE rolname = 'app_login'"
    ).fetchone()
    if row is None:
        findings.append(Finding("app-login-exists", "CRITICAL", "app_login is missing"))
    elif row[0]:
        findings.append(
            Finding(
                "app-login-noinherit",
                "CRITICAL",
                "app_login has INHERIT — it would hold every tenant's privileges "
                "without SET ROLE, and the fail-closed property is gone",
            )
        )

    for role in ("app_login", "tenant_migrate"):
        got = conn.execute(
            "SELECT setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole"
            " WHERE r.rolname = %s",
            (role,),
        ).fetchone()
        cfg = got[0] if got else None
        if not cfg or not any(c.startswith("search_path=") for c in cfg):
            findings.append(
                Finding(
                    "empty-search-path",
                    "CRITICAL",
                    f"{role} has no search_path override — unqualified references "
                    f"could resolve, re-opening the prepared-statement hazard",
                )
            )

    if conn.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname = 'public'"
    ).fetchone():
        findings.append(Finding("no-public-schema", "HIGH", "schema public still exists"))

    # --- Per-tenant invariants ----------------------------------------------

    for slug in slugs:
        bronze, silver, gold = all_schemas(slug)
        ingest, recon, support = all_roles(slug)
        reconciliation = reconciliation_schema(slug)
        recon_engine = recon_engine_role(slug)

        for schema in (bronze, silver, gold, reconciliation):
            if not conn.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)
            ).fetchone():
                findings.append(
                    Finding("schema-exists", "CRITICAL", f"{slug}: missing schema {schema}")
                )
                continue

            # Identity row present, singular, and naming THIS tenant. A schema
            # restored under the wrong name is otherwise undetectable from the
            # data alone — this row travels with the dump and is what makes
            # assert_schema_owner catch it on first use.
            try:
                rows = conn.execute(
                    psycopg.sql.SQL("SELECT tenant_slug FROM {}.__schema_identity").format(
                        psycopg.sql.Identifier(schema)
                    )
                ).fetchall()
            except psycopg.Error as exc:
                conn.rollback()
                findings.append(
                    Finding("identity-readable", "CRITICAL", f"{schema}: {exc}")
                )
                continue
            if len(rows) != 1:
                findings.append(
                    Finding(
                        "identity-singleton", "CRITICAL",
                        f"{schema}: expected exactly 1 identity row, found {len(rows)}",
                    )
                )
            elif rows[0][0] != slug:
                findings.append(
                    Finding(
                        "identity-matches", "CRITICAL",
                        f"{schema}: identity says {rows[0][0]!r}, schema name says {slug!r}",
                    )
                )

        for role in (ingest, recon, support, recon_engine):
            if not conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone():
                findings.append(
                    Finding("role-exists", "CRITICAL", f"{slug}: missing role {role}")
                )

        # recon_engine holds CREATE on its own schema, and only its own
        # (docs/adr/0001, reversed shared migration 061) — the engine team
        # owns table DDL there. This regresses the moment CREATE leaks to
        # any OTHER role on the reconciliation schema, or to recon_engine on
        # anything but its own schema.
        for other in slugs:
            other_reconciliation = reconciliation_schema(other)
            for role, expect_create in (
                (recon_engine, other == slug),
                (ingest, False),
                (recon, False),
                (support, False),
            ):
                got = conn.execute(
                    "SELECT has_schema_privilege(%s, %s, 'CREATE')",
                    (role, other_reconciliation),
                ).fetchone()
                if bool(got and got[0]) != expect_create:
                    findings.append(
                        Finding(
                            "recon-engine-create-scoped-to-own-schema", "CRITICAL",
                            f"{role} CREATE on {other_reconciliation} is "
                            f"{bool(got and got[0])}, expected {expect_create}",
                        )
                    )

        # Cross-tenant USAGE: the core property. Any tenant role holding USAGE
        # on another tenant's schema is a breach, not a warning.
        for other in slugs:
            if other == slug:
                continue
            for other_schema in (*all_schemas(other), reconciliation_schema(other)):
                for role in (ingest, recon, support, recon_engine):
                    got = conn.execute(
                        "SELECT has_schema_privilege(%s, %s, 'USAGE')", (role, other_schema)
                    ).fetchone()
                    if got and got[0]:
                        findings.append(
                            Finding(
                                "no-cross-tenant-usage", "CRITICAL",
                                f"{role} holds USAGE on {other_schema}",
                            )
                        )

        grants = conn.execute(
            """
            SELECT grantee, table_schema, table_name, privilege_type, c.relkind
            FROM information_schema.role_table_grants g
            JOIN pg_namespace n ON n.nspname = g.table_schema
            JOIN pg_class c ON c.relname = g.table_name AND c.relnamespace = n.oid
            WHERE g.grantee = ANY(%s)
            """,
            ([ingest, recon, support, recon_engine],),
        ).fetchall()

        for grantee, schema, table, priv, relkind in grants:
            # recon must never touch a Silver BASE table. Its entire read
            # contract is the v1_ views (ARCHITECTURE.md 2.4).
            if (
                grantee == recon
                and schema == silver
                and relkind == "r"
                and table != "__schema_identity"
            ):
                findings.append(
                    Finding(
                        "recon-no-silver-base-tables", "CRITICAL",
                        f"{recon} has {priv} on base table {schema}.{table} — the v1_ "
                        f"contract is bypassed",
                    )
                )
            # Bronze is INSERT-only for every runtime role. It indexes objects
            # held under Object Lock COMPLIANCE for 2190 days, so a mutable
            # ledger row could only ever drift from the store it describes —
            # and disposition belongs in Silver, where the parsing happens.
            if (
                schema == bronze
                and not table.startswith("__")
                and priv in ("UPDATE", "DELETE", "TRUNCATE")
            ):
                findings.append(
                    Finding(
                        "bronze-insert-only", "CRITICAL",
                        f"{grantee} has {priv} on {schema}.{table} — Bronze is the "
                        f"evidentiary record and is written once",
                    )
                )
            # ingest must never see Gold; recon must never see Bronze.
            if grantee == ingest and schema == gold:
                findings.append(
                    Finding("ingest-no-gold", "CRITICAL",
                            f"{ingest} has {priv} on {schema}.{table}")
                )
            if grantee == recon and schema == bronze:
                findings.append(
                    Finding("recon-no-bronze", "CRITICAL",
                            f"{recon} has {priv} on {schema}.{table}")
                )
            # recon_engine (inafin-reconciliation-engine, docs/adr/0001) is
            # scoped to an allowlisted subset of silver v1_ views plus its own
            # reconciliation schema — nothing on bronze, ever. Gold gets ONE
            # deliberate, named exception (shared migration 063):
            # v1_reconciliation_tenant_setting, plus the __schema_identity
            # read that USAGE on the gold schema requires (step 7 below) —
            # anything else in Gold is still a violation, inafinplatform/v2's
            # tables included.
            if grantee == recon_engine and schema == bronze:
                findings.append(
                    Finding(
                        "recon-engine-scoped-to-own-schema", "CRITICAL",
                        f"{recon_engine} has {priv} on {schema}.{table}",
                    )
                )
            if (
                grantee == recon_engine
                and schema == gold
                and table != "__schema_identity"
                and not table.startswith("v1_reconciliation_")
            ):
                findings.append(
                    Finding(
                        "recon-engine-scoped-to-own-schema", "CRITICAL",
                        f"{recon_engine} has {priv} on {schema}.{table} — only "
                        f"v1_reconciliation_% views are an approved Gold exception",
                    )
                )
            if (
                grantee == recon_engine
                and schema == gold
                and table.startswith("v1_reconciliation_")
                and relkind != "v"
            ):
                findings.append(
                    Finding(
                        "recon-engine-gold-exception-must-be-a-view", "CRITICAL",
                        f"{recon_engine} has {priv} on {schema}.{table}, relkind={relkind} "
                        f"— the Gold exception is for views only, never a base table",
                    )
                )
            if (
                grantee == recon_engine
                and schema == silver
                and relkind == "r"
                and table != "__schema_identity"
            ):
                findings.append(
                    Finding(
                        "recon-engine-no-silver-base-tables", "CRITICAL",
                        f"{recon_engine} has {priv} on base table {schema}.{table}",
                    )
                )
            # The reconciliation schema belongs to recon_engine alone.
            if schema == reconciliation and grantee in (ingest, recon, support):
                findings.append(
                    Finding(
                        "reconciliation-schema-recon-engine-only", "CRITICAL",
                        f"{grantee} has {priv} on {schema}.{table}",
                    )
                )
            # The guard's anchor must be immutable to every runtime role.
            if table == "__schema_identity" and priv in WRITE_PRIVILEGES:
                findings.append(
                    Finding(
                        "identity-immutable", "CRITICAL",
                        f"{grantee} has {priv} on {schema}.__schema_identity — it could "
                        f"rewrite tenant_slug and defeat assert_schema_owner",
                    )
                )
            # Migration bookkeeping belongs to tenant_migrate alone.
            if table == "__migration_version":
                findings.append(
                    Finding("migration-table-private", "HIGH",
                            f"{grantee} has {priv} on {schema}.__migration_version")
                )
            # support is read-only, everywhere, always.
            if grantee == support and priv in WRITE_PRIVILEGES:
                findings.append(
                    Finding("support-read-only", "CRITICAL",
                            f"{support} has {priv} on {schema}.{table}")
                )

        # v1_ views must be DEFINER-rights. security_invoker would force recon
        # to hold SELECT on the base tables, collapsing the read contract into
        # "recon can read all of Silver".
        for (viewname, reloptions) in conn.execute(
            "SELECT c.relname, c.reloptions FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relkind = 'v' AND c.relname LIKE 'v1\\_%%'",
            (silver,),
        ).fetchall():
            for opt in reloptions or []:
                if opt.replace(" ", "").lower() == "security_invoker=true":
                    findings.append(
                        Finding(
                            "v1-views-definer-rights", "CRITICAL",
                            f"{silver}.{viewname} has security_invoker=true — recon "
                            f"would need SELECT on the base tables",
                        )
                    )

        # Tenant reference data must be read-only to tenants.
        for role in (ingest, recon, support, recon_engine):
            for priv in ("INSERT", "UPDATE", "DELETE"):
                got = conn.execute(
                    "SELECT has_table_privilege(%s, 'platform_ref.universal_master', %s)",
                    (role, priv),
                ).fetchone()
                if got and got[0]:
                    findings.append(
                        Finding("platform-ref-read-only", "CRITICAL",
                                f"{role} has {priv} on platform_ref.universal_master")
                    )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    with psycopg.connect(settings.pg_migrate_dsn, autocommit=True) as conn:
        findings = check(conn)

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    elif not findings:
        print("isolation OK — no findings")
    else:
        for f in findings:
            print(f"[{f.severity}] {f.rule}: {f.detail}")
        print(f"\n{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
