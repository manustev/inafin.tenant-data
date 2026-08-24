"""The ingestion surface — REST (upload/trigger/status) + GraphQL (reads).

Run with: `uvicorn src.api.app:app`

Everything here is assembly. The pieces themselves — `TenantScopedPool`,
`BronzeIngestionService`, `SilverReader`, `EntitlementReader` — are unchanged
from their library form; this module is what makes them reachable over HTTP,
one pool and one set of services per process, built once at startup and
handed to every request via `request.app.state` (`src/api/deps.py`,
`src/api/graphql/schema.py`).

The trigger route (`src/api/routes_ingest.py`) calls `src/dispatch/router.py`'s
`dispatch_load` synchronously, in-process, after recording intent
(`load_trigger`). This does not reopen HANDOFF-2026-08-07.md's "the upsert
must NOT go behind an API" — that argument is about never putting the loader
behind a SECOND, remote HTTP hop; nothing here does that. `dispatch_load`
calls straight into `RegisterLoader`/`SilverPromotionService`/
`run_extraction`, the same in-process psycopg calls a future worker consuming
`load_trigger` would make (TODO.md's still-open item, unchanged).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

from src.api.auth import StaticTokenAuth
from src.api.graphql.schema import context_getter, schema
from src.api.routes_ingest import router as ingest_router
from src.bronze.scan import build_scanner
from src.bronze.service import BronzeIngestionService
from src.core.config import get_settings
from src.core.pool import TenantScopedPool
from src.events.publisher import BatchPublisher
from src.extraction.reader import FallbackPdfTextReader, PypdfReader
from src.provisioning.objectstore import S3ObjectStore
from src.reader.entitlement_reader import EntitlementReader
from src.reader.silver_reader import SilverReader


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    pool = TenantScopedPool(
        settings.pg_app_dsn,
        min_size=settings.app_pool_min_size,
        max_size=settings.app_pool_max_size,
    )
    await pool.open()

    store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket_prefix=settings.s3_bucket_prefix,
        retention_days=settings.bronze_retention_days,
    )

    app.state.pool = pool
    app.state.store = store
    app.state.auth = StaticTokenAuth(settings)
    app.state.bronze_service = BronzeIngestionService(
        pool, store, bucket_prefix=settings.s3_bucket_prefix,
        scanner=build_scanner(settings),
    )
    app.state.silver_reader = SilverReader(pool)
    app.state.entitlement_reader = EntitlementReader(pool)

    # OCR fallback, off by default (Settings.ocr_enabled) — see
    # src/extraction/ocr.py's module docstring for why the real adapter is
    # never constructed unless explicitly opted in. FallbackPdfTextReader
    # with secondary=None is exactly PypdfReader's own behaviour, so this is
    # always safe to construct.
    secondary_reader = None
    if settings.ocr_enabled:
        from src.extraction.ocr import PaddleOcrReader

        secondary_reader = PaddleOcrReader()
    app.state.pdf_text_reader = FallbackPdfTextReader(PypdfReader(), secondary_reader)

    # Pipeline 2's doorbell. Started here, alongside the pool, so a broker
    # outage at process startup degrades to poll-only (BatchPublisher.start's
    # own fallback) rather than failing app startup entirely — the same
    # "Kafka is an optimisation" reasoning src/events/publisher.py's module
    # docstring states.
    batch_publisher = BatchPublisher(
        settings.kafka_bootstrap, settings.kafka_batch_topic, enabled=settings.kafka_enabled,
    )
    await batch_publisher.start()
    app.state.batch_publisher = batch_publisher

    try:
        yield
    finally:
        await batch_publisher.stop()
        await pool.close()


app = FastAPI(title="inafin-tenant-data ingestion surface", lifespan=lifespan)
app.include_router(ingest_router)
app.include_router(
    GraphQLRouter(schema, context_getter=context_getter), prefix="/graphql"
)
