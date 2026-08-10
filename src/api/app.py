"""The ingestion surface — REST (upload/trigger/status) + GraphQL (reads).

Run with: `uvicorn src.api.app:app`

Everything here is assembly. The pieces themselves — `TenantScopedPool`,
`BronzeIngestionService`, `SilverReader`, `EntitlementReader` — are unchanged
from their library form; this module is what makes them reachable over HTTP,
one pool and one set of services per process, built once at startup and
handed to every request via `request.app.state` (`src/api/deps.py`,
`src/api/graphql/schema.py`).

Bronze->Silver promotion is deliberately NOT here and never will be
(HANDOFF-2026-08-07.md "Why the upsert must NOT go behind an API") — the
trigger route only records intent (`src/api/routes_ingest.py`).
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
    app.state.auth = StaticTokenAuth(settings)
    app.state.bronze_service = BronzeIngestionService(
        pool, store, bucket_prefix=settings.s3_bucket_prefix,
        scanner=build_scanner(settings),
    )
    app.state.silver_reader = SilverReader(pool)
    app.state.entitlement_reader = EntitlementReader(pool)

    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="inafin-tenant-data ingestion surface", lifespan=lifespan)
app.include_router(ingest_router)
app.include_router(
    GraphQLRouter(schema, context_getter=context_getter), prefix="/graphql"
)
