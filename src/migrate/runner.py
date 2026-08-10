"""Migration fan-out runner.

ARCHITECTURE.md 5.8 names this the real operational cost of schema-per-tenant,
and PHASE1-PLAN.md 6 flags it as the item most likely to be underestimated. It
is not "Alembic in a loop":

  * per-schema version tracking, so tenants can be at different versions
  * resumability — one tenant failing must not block the other N-1
  * checksum pinning — an already-applied migration that has been edited is a
    hard error, not a silent no-op
  * drift detection that alerts rather than logs
  * new tenants provisioned mid-release land at head

DEVIATION FROM THE DESIGN DOC, flagged for ratification: the Full Data
Architecture says "all tables created and managed via Alembic migrations". That
holds well for Category B (shared, single schema, branching history). It fits
the tenant chain badly: those migrations are one DDL body applied N times with a
schema substitution — a templating problem, not a version-graph problem — and
every property listed above would have to be built on top of Alembic anyway.
This runner is ~200 lines of auditable Python against plain .sql files. The
shared chain could still be moved to Alembic without touching the tenant path.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import psycopg
from psycopg import sql

from src.core.errors import MigrationError, SchemaDriftError
from src.core.identifiers import bronze_schema, gold_schema, silver_schema, validate_slug

logger = logging.getLogger(__name__)

_MIGRATIONS_ROOT = Path(__file__).resolve().parents[2] / "migrations"
SHARED_DIR = _MIGRATIONS_ROOT / "shared"
TENANT_DIR = _MIGRATIONS_ROOT / "tenant"
# Per-tenant column extensions, one subdirectory per slug (TYPED-TABLES-PLAN.md
# 7). Module-level so tests can point it somewhere disposable.
TENANT_EXT_DIR = _MIGRATIONS_ROOT / "tenant_ext"

# Extension filenames are namespaced, and the loader enforces it.
#
# Both chains are recorded in ONE __migration_version table, keyed by filename.
# An extension called 003_gold.sql would therefore collide with the common
# chain's key: the runner would read it as already applied, skip the real 003
# for that tenant, and the checksum check would then report a mismatch on a file
# nobody edited. Requiring a distinct prefix makes the collision unrepresentable
# and lets the version table say which chain each row came from.
_EXT_FILENAME_RE = re.compile(r"^ext_[0-9]{3}_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    filename: str
    body: str
    checksum: str


@dataclass
class TenantResult:
    slug: str
    applied: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class RunReport:
    results: list[TenantResult]

    @property
    def failed(self) -> list[TenantResult]:
        return [r for r in self.results if not r.ok]

    @property
    def ok(self) -> bool:
        return not self.failed


def _load(directory: Path) -> list[Migration]:
    """Load .sql migrations in lexical order. Filenames are the version key."""
    out: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        out.append(
            Migration(
                filename=path.name,
                body=body,
                checksum=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            )
        )
    return out


def _load_ext(slug: str) -> list[Migration]:
    """Load one tenant's column-extension chain. Empty for most tenants.

    TYPED-TABLES-PLAN.md 7: table-per-type exists so a tenant can carry extra
    columns on a register, and this is where those columns live. Base tables stay
    templated and byte-identical across the estate (migrations/tenant/); anything
    only one tenant has arrives here, applied to that slug alone and pinned by
    the same checksum in the same bookkeeping table.

    The slug is validated before it becomes a path component, so this cannot
    traverse: validate_slug constrains the alphabet to [a-z0-9_].
    """
    validate_slug(slug)
    directory = TENANT_EXT_DIR / slug
    if not directory.is_dir():
        return []

    stray = sorted(
        p.name for p in directory.glob("*.sql") if not _EXT_FILENAME_RE.match(p.name)
    )
    if stray:
        raise MigrationError(
            f"tenant_ext/{slug}: {', '.join(stray)} — extension migrations must be "
            f"named ext_NNN_description.sql. They share __migration_version with the "
            f"common chain, so an unprefixed name can collide with it."
        )
    return _load(directory)


def _render(body: str, slug: str) -> str:
    """Substitute schema placeholders.

    Plain string replacement rather than str.format: migration bodies contain
    JSONB literals and dollar-quoted PL/pgSQL, both of which use braces that
    .format would try to interpret.

    The substituted values are quoted identifiers built from an already
    validated slug, so this cannot inject: validate_slug constrains the alphabet
    to [a-z0-9_] before the name is ever constructed.
    """
    validate_slug(slug)
    return (
        body.replace("{{bronze}}", f'"{bronze_schema(slug)}"')
        .replace("{{silver}}", f'"{silver_schema(slug)}"')
        .replace("{{gold}}", f'"{gold_schema(slug)}"')
    )


class MigrationRunner:
    def __init__(self, migrate_dsn: str) -> None:
        self._dsn = migrate_dsn

    # --- shared chain -------------------------------------------------------

    def apply_shared(self) -> list[str]:
        migrations = _load(SHARED_DIR)
        applied: list[str] = []

        with psycopg.connect(self._dsn, autocommit=True) as conn:
            # Bootstrap the bookkeeping table itself — it cannot live in a
            # migration it is used to track.
            conn.execute("CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION tenant_migrate")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app.shared_migration_version (
                    filename   text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now(),
                    checksum   text NOT NULL
                )
                """
            )
            done = self._applied_map(conn, "app.shared_migration_version")

            for m in migrations:
                if m.filename in done:
                    self._verify_checksum(m, done[m.filename], scope="shared")
                    continue
                logger.info("applying shared migration %s", m.filename)
                with conn.transaction():
                    conn.execute(m.body)
                    conn.execute(
                        "INSERT INTO app.shared_migration_version (filename, checksum)"
                        " VALUES (%s, %s)",
                        (m.filename, m.checksum),
                    )
                applied.append(m.filename)
        return applied

    # --- tenant chain -------------------------------------------------------

    def apply_tenant(self, slug: str) -> list[str]:
        """Apply pending tenant migrations to one tenant, then re-assert grants.

        Grants are re-applied unconditionally, even when no migration ran: it is
        idempotent, and it makes every run self-healing against drift introduced
        by hand. app.apply_tenant_grants REVOKEs before it GRANTs, so a
        privilege added outside the matrix is removed here.

        The common chain is applied before the tenant's extension chain, always.
        An extension adds columns to a base table, so the base table has to exist
        — and a tenant three common migrations behind must catch up before its
        own extensions are re-evaluated against a schema that has since moved.
        """
        validate_slug(slug)
        migrations = [*_load(TENANT_DIR), *_load_ext(slug)]
        version_table = sql.Identifier(silver_schema(slug), "__migration_version")
        applied: list[str] = []

        with psycopg.connect(self._dsn, autocommit=True) as conn:
            done = self._applied_map(conn, version_table)

            for m in migrations:
                if m.filename in done:
                    self._verify_checksum(m, done[m.filename], scope=slug)
                    continue
                logger.info("applying tenant migration %s to %s", m.filename, slug)
                with conn.transaction():
                    conn.execute(_render(m.body, slug))
                    conn.execute(
                        sql.SQL(
                            "INSERT INTO {} (filename, checksum) VALUES (%s, %s)"
                        ).format(version_table),
                        (m.filename, m.checksum),
                    )
                applied.append(m.filename)

            conn.execute("SELECT app.apply_tenant_grants(%s)", (slug,))
        return applied

    def apply_all_tenants(self, concurrency: int = 4) -> RunReport:
        """Fan out across every ACTIVE/PROVISIONING tenant.

        Bounded concurrency, and one tenant's failure is captured rather than
        raised, so a single bad schema cannot block the rest of the estate. The
        caller decides what to do with RunReport.failed.
        """
        slugs = self.tenant_slugs()
        results: list[TenantResult] = []

        def _one(slug: str) -> TenantResult:
            try:
                return TenantResult(slug=slug, applied=self.apply_tenant(slug))
            except Exception as exc:
                logger.exception("tenant migration failed for %s", slug)
                return TenantResult(slug=slug, error=f"{type(exc).__name__}: {exc}")

        if slugs:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
                results = list(pool.map(_one, slugs))
        return RunReport(results=results)

    # --- introspection ------------------------------------------------------

    def tenant_slugs(self) -> list[str]:
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT slug FROM app.tenant_registry"
                " WHERE status IN ('ACTIVE', 'PROVISIONING') ORDER BY slug"
            ).fetchall()
        return [r[0] for r in rows]

    def head(self) -> str | None:
        """Head of the COMMON tenant chain — the one version every tenant shares.

        Extension chains are deliberately excluded: "head" is a single release
        marker, and there is no single answer once each slug has its own tail.
        Use drift_report() for the per-tenant question.
        """
        migrations = _load(TENANT_DIR)
        return migrations[-1].filename if migrations else None

    def drift_report(self) -> dict[str, list[str]]:
        """Map slug -> pending migration filenames. Empty dict == no drift.

        Silent drift is how one tenant ends up two migrations behind and nobody
        notices until a query fails in production, so this is surfaced as data
        for an alert rule, not written to a log.

        "At head" is per tenant, not global: expected = the common chain PLUS
        that slug's extension chain (TYPED-TABLES-PLAN.md 7). Comparing every
        tenant against one list would make a tenant with an unapplied extension
        look healthy, which is precisely the tenant whose queries are about to
        fail on a missing column.
        """
        common = [m.filename for m in _load(TENANT_DIR)]
        drift: dict[str, list[str]] = {}

        with psycopg.connect(self._dsn, autocommit=True) as conn:
            for slug in self.tenant_slugs():
                expected = [*common, *(m.filename for m in _load_ext(slug))]
                try:
                    done = set(
                        self._applied_map(
                            conn, sql.Identifier(silver_schema(slug), "__migration_version")
                        )
                    )
                except psycopg.Error:
                    drift[slug] = expected  # schema missing entirely
                    conn.rollback()
                    continue
                pending = [f for f in expected if f not in done]
                if pending:
                    drift[slug] = pending
        return drift

    def assert_no_drift(self) -> None:
        drift = self.drift_report()
        if drift:
            summary = "; ".join(f"{s}: {len(p)} pending" for s, p in sorted(drift.items()))
            raise SchemaDriftError(f"tenant schemas not at head — {summary}")

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _applied_map(
        conn: psycopg.Connection[tuple[object, ...]], table: str | sql.Identifier
    ) -> dict[str, str]:
        stmt = (
            sql.SQL("SELECT filename, checksum FROM {}").format(table)
            if isinstance(table, sql.Identifier)
            else sql.SQL("SELECT filename, checksum FROM " + table)
        )
        rows = cast("list[tuple[str, str]]", conn.execute(stmt).fetchall())
        return dict(rows)

    @staticmethod
    def _verify_checksum(m: Migration, recorded: str, *, scope: str) -> None:
        """An applied migration whose body has changed is a hard error.

        Editing an already-applied migration means the database and the
        repository disagree about what was run — which, for a file that grants
        privileges, is exactly the state nobody should be guessing about.
        """
        if recorded != m.checksum:
            raise MigrationError(
                f"checksum mismatch for {m.filename} in {scope}: recorded {recorded[:12]}, "
                f"file {m.checksum[:12]}. An applied migration has been edited — "
                f"add a new migration instead."
            )


def _load_migrations_for_test(directory: Path) -> Sequence[Migration]:  # pragma: no cover
    return _load(directory)
