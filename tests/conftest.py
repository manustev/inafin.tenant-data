"""Shared fixtures.

Two tenants are provisioned for every run, seeded with **colliding natural
keys** — the same invoice number, the same supplier GSTIN, the same content
hash. Isolation bugs hide behind naturally distinct test data: if acme's rows
say 'acme' and globex's say 'globex', a leak is obvious by inspection and a
test that merely counts rows will pass a broken boundary. Making the data
identical removes that crutch.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import psycopg
import pytest
import pytest_asyncio

from src.core.config import Settings, get_settings
from src.core.pool import TenantScopedPool
from src.core.tenant import TenantContext
from src.extraction.base import DocumentExtractor
from src.extraction.registry import build_extractor_registry
from src.migrate.runner import MigrationRunner
from src.provisioning.service import ProvisioningService

TENANT_A = "acme"
TENANT_B = "globex"

# Deliberately identical across both tenants.
COLLIDING_INVOICE_NUMBER = "INV-COLLIDE-0001"
COLLIDING_SUPPLIER_GSTIN = "27AAAPA1234A1Z5"
COLLIDING_HSN = "84713010"


@dataclass(frozen=True)
class SeededTenant:
    slug: str
    ctx: TenantContext
    entity_id: uuid.UUID
    batch_id: uuid.UUID
    invoice_id: uuid.UUID
    ingest_id: uuid.UUID


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def provisioned(settings: Settings) -> dict[str, SeededTenant]:
    """Provision both tenants and seed colliding rows. Session-scoped."""
    MigrationRunner(settings.pg_migrate_dsn).apply_shared()
    svc = ProvisioningService(migrate_dsn=settings.pg_migrate_dsn)

    out: dict[str, SeededTenant] = {}
    for slug in (TENANT_A, TENANT_B):
        t = svc.provision(slug)
        out[slug] = _seed(settings.pg_migrate_dsn, slug, t.tenant_id)
    return out


def _seed(dsn: str, slug: str, tenant_id: uuid.UUID) -> SeededTenant:
    """Seed via the migration role, not through the code under test.

    A conformance suite that seeds through its own pool wrapper would fail to
    distinguish "isolation works" from "the writer was broken too".
    """
    ctx = TenantContext(slug=slug, tenant_id=tenant_id)
    entity_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    line_id = uuid.uuid4()
    ingest_id = uuid.uuid4()
    content_hash = hashlib.sha256(b"identical-bytes-in-both-tenants").digest()

    with psycopg.connect(dsn, autocommit=True) as conn:
        # Reset first. The suite must be re-runnable against an existing cluster:
        # artefact_ledger has a UNIQUE index on content_hash (per-tenant dedup),
        # and the seed deliberately reuses one hash, so a second run without
        # this would collide with the first run's row.
        conn.execute(
            psycopg.sql.SQL("TRUNCATE {}, {}, {}, {} CASCADE").format(
                psycopg.sql.Identifier(ctx.silver_schema, "transaction_line"),
                psycopg.sql.Identifier(ctx.silver_schema, "transaction_document"),
                psycopg.sql.Identifier(ctx.silver_schema, "purchase_register"),
                psycopg.sql.Identifier(ctx.silver_schema, "ingest_batch"),
            )
        )
        conn.execute(
            psycopg.sql.SQL("TRUNCATE {} CASCADE").format(
                psycopg.sql.Identifier(ctx.bronze_schema, "artefact_ledger")
            )
        )
        conn.execute(
            psycopg.sql.SQL("TRUNCATE {}, {}, {}").format(
                psycopg.sql.Identifier(ctx.gold_schema, "fact_record"),
                psycopg.sql.Identifier(ctx.gold_schema, "batch_execution"),
                psycopg.sql.Identifier(ctx.gold_schema, "consumer_watermark"),
            )
        )
        conn.execute(
            psycopg.sql.SQL(
                """
                INSERT INTO {}.artefact_ledger (
                    ingest_id, entity_id, declared_document_type, source_stream,
                    content_hash, object_key, object_bucket, size_bytes,
                    original_filename, received_from
                ) VALUES (%s, %s, 'PURCHASE_REGISTER', 'B', %s, %s, %s, %s,
                          'purchases.csv', 'test-seed')
                ON CONFLICT (ingest_id) DO NOTHING
                """
            ).format(psycopg.sql.Identifier(ctx.bronze_schema)),
            (ingest_id, entity_id, content_hash,
             f"bronze/2026/08/03/{ingest_id}.csv", ctx.bucket("inafin-tenant"), 4096),
        )
        conn.execute(
            psycopg.sql.SQL(
                """
                INSERT INTO {}.ingest_batch (
                    batch_id, entity_id, document_type, source_stream,
                    period_start, period_end, row_count, content_hash,
                    bronze_manifest_ref, status, ingest_run_id
                ) VALUES (%s, %s, 'PURCHASE_REGISTER', 'B',
                          DATE '2026-04-01', DATE '2026-04-30', 1, %s, %s,
                          'READY', %s)
                """
            ).format(psycopg.sql.Identifier(ctx.silver_schema)),
            (batch_id, entity_id, content_hash, str(ingest_id), uuid.uuid4()),
        )
        conn.execute(
            psycopg.sql.SQL(
                """
                INSERT INTO {}.transaction_document (
                    doc_id, batch_id, entity_id, doc_type, direction,
                    doc_number, counterparty_gstin, doc_date,
                    total_taxable_value, total_tax_value, total_value,
                    valid_from, bronze_ingest_id
                ) VALUES (%s, %s, %s, 'PURCHASE_REGISTER', 'INWARD',
                          %s, %s, DATE '2026-04-15',
                          100000.00, 18000.00, 118000.00, DATE '2026-04-15', %s)
                """
            ).format(psycopg.sql.Identifier(ctx.silver_schema)),
            (invoice_id, batch_id, entity_id, COLLIDING_INVOICE_NUMBER,
             COLLIDING_SUPPLIER_GSTIN, ingest_id),
        )
        conn.execute(
            psycopg.sql.SQL(
                """
                INSERT INTO {}.transaction_line (
                    line_id, doc_id, line_number, hsn_sac, description,
                    quantity, unit_of_measure, unit_price, taxable_value,
                    gst_rate, igst_amount, itc_amount
                ) VALUES (%s, %s, 1, %s, 'Automatic data processing machine',
                          10, 'NOS', 10000.0000, 100000.00, 18.00, 18000.00, 18000.00)
                """
            ).format(psycopg.sql.Identifier(ctx.silver_schema)),
            (line_id, invoice_id, COLLIDING_HSN),
        )
        # v1_purchase_invoice reads purchase_register (tenant migration 011),
        # not transaction_document, since TYPED-TABLES-PLAN.md 10 step 4 —
        # seeded separately so isolation tests that go through the view still
        # have a colliding row to find. The transaction_document/transaction_line
        # rows above stay: several gates assert on that table directly, and
        # PURCHASE_REGISTER is still a legal doc_type there (archetype 1
        # persists in the registry; only the promote.py write path and the view
        # moved — see src/silver/promote.py's _RETIRED_ARCHETYPE_1_TYPES).
        conn.execute(
            psycopg.sql.SQL(
                """
                INSERT INTO {}.purchase_register (
                    entity_id, gstin, tax_period, batch_id, bronze_ingest_id,
                    row_hash, supplier_gstin, invoice_no, invoice_date, hsn_sac,
                    taxable_value, gst_rate, cgst, sgst, igst, cess,
                    gl_code, cost_centre, itc_eligibility, rcm_flag, valid_from
                ) VALUES (%s, %s, DATE '2026-04-01', %s, %s,
                          'seed-hash', %s, %s, DATE '2026-04-15', %s,
                          100000.00, 18.00, 0, 0, 18000.00, 0,
                          'GL-TEST', 'CC-TEST', 'ELIGIBLE', false, DATE '2026-04-15')
                """
            ).format(psycopg.sql.Identifier(ctx.silver_schema)),
            (entity_id, COLLIDING_SUPPLIER_GSTIN, batch_id, ingest_id,
             COLLIDING_SUPPLIER_GSTIN, COLLIDING_INVOICE_NUMBER, COLLIDING_HSN),
        )

    return SeededTenant(
        slug=slug, ctx=ctx, entity_id=entity_id, batch_id=batch_id,
        invoice_id=invoice_id, ingest_id=ingest_id,
    )


@pytest.fixture(scope="session")
def tenant_a(provisioned: dict[str, SeededTenant]) -> SeededTenant:
    return provisioned[TENANT_A]


@pytest.fixture(scope="session")
def tenant_b(provisioned: dict[str, SeededTenant]) -> SeededTenant:
    return provisioned[TENANT_B]


@pytest_asyncio.fixture
async def app_pool(
    settings: Settings, provisioned: dict[str, SeededTenant]
) -> AsyncIterator[TenantScopedPool]:
    """The runtime pool: app_login, THROUGH PGBOUNCER, transaction pooling.

    Every conformance test uses this. Pointing it at Postgres directly would
    make gate 10.3 (role leakage across pooled connections) prove nothing.
    """
    _ = provisioned
    pool = TenantScopedPool(settings.pg_app_dsn, min_size=1, max_size=4)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def extractor_registry(
    app_pool: TenantScopedPool, tenant_a: SeededTenant
) -> dict[str, DocumentExtractor]:
    """The registry-driven `doc_type_code -> DocumentExtractor` instance map,
    built for real against the seeded `platform_ref.document_type` — the
    corrective-refactor replacement for importing a hand-written extractor
    class directly (`tests/conformance/test_extraction_*.py`'s old pattern).

    Built against `tenant_a`'s context, but `platform_ref` is shared catalog,
    not tenant-scoped data — which tenant's `ctx` opens the connection does
    not change what rows come back. Function-scoped, matching
    `build_extractor_registry`'s own "read per call, never cached" design
    (`src/extraction/registry.py`'s docstring) — a corrected `extraction_spec`
    cell must be visible to the very next test, not stale.
    """
    return await build_extractor_registry(app_pool, tenant_a.ctx)


@pytest.fixture
def admin(settings: Settings) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """Privileged control connection — the POSITIVE CONTROL.

    Used to prove that the rows a tenant cannot see actually exist. Without
    this, a failed seed makes every isolation test pass vacuously.
    """
    # rsplit, not replace: PG_SUPER_DSN contains "/postgres" twice (the
    # credential and the database), and replace() would mangle the userinfo.
    super_tenant_dsn = settings.pg_super_dsn.rsplit("/", 1)[0] + "/tenant_db"
    with psycopg.connect(super_tenant_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
def raw_app_conn(settings: Settings) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """A bare app_login connection through PgBouncer, with NO role assumed.

    Deliberately bypasses TenantScopedPool: some gates must observe what
    happens when the preamble is absent, which the wrapper always supplies.
    """
    with psycopg.connect(settings.pg_app_dsn, autocommit=True) as conn:
        yield conn
