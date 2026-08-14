"""Fixtures for the API layer (`src/api/`) conformance tests.

Builds a FastAPI app wired directly to the test suite's own `app_pool` and a
real MinIO-backed `S3ObjectStore` (same pattern as `tests/handoff/conftest.py`),
rather than exercising `src.api.app`'s production `lifespan` — that lifespan
opens its own pool from env DSNs, which would be a second pool per test run
and would not let tests observe writes through the same connection the seed
fixtures use.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from strawberry.fastapi import GraphQLRouter
from tests.conftest import TENANT_A, TENANT_B

from src.api.auth import StaticTokenAuth
from src.api.graphql.schema import context_getter, schema
from src.api.routes_ingest import router as ingest_router
from src.bronze.service import BronzeIngestionService
from src.core.config import Settings
from src.core.pool import TenantScopedPool
from src.extraction.reader import FallbackPdfTextReader, PypdfReader
from src.provisioning.objectstore import S3ObjectStore
from src.reader.entitlement_reader import EntitlementReader
from src.reader.silver_reader import SilverReader

TOKEN_ACME = "test-acme-token"  # noqa: S105 -- test fixture, not a secret
TOKEN_GLOBEX = "test-globex-token"  # noqa: S105 -- test fixture, not a secret


@pytest.fixture(scope="session")
def api_object_store(settings: Settings) -> S3ObjectStore:
    store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket_prefix=settings.s3_bucket_prefix,
        retention_days=1,  # test bucket, not the CGST s.36 floor
    )
    for slug in (TENANT_A, TENANT_B):
        store.ensure_bucket(slug)
    return store


@pytest_asyncio.fixture
async def api_app(
    app_pool: TenantScopedPool, api_object_store: S3ObjectStore, settings: Settings
) -> FastAPI:
    app = FastAPI()
    app.state.pool = app_pool
    app.state.store = api_object_store
    app.state.pdf_text_reader = FallbackPdfTextReader(PypdfReader(), None)
    app.state.auth = StaticTokenAuth(
        Settings(api_tenant_tokens={TOKEN_ACME: TENANT_A, TOKEN_GLOBEX: TENANT_B})
    )
    app.state.bronze_service = BronzeIngestionService(
        app_pool, api_object_store, bucket_prefix=settings.s3_bucket_prefix
    )
    app.state.silver_reader = SilverReader(app_pool)
    app.state.entitlement_reader = EntitlementReader(app_pool)
    app.include_router(ingest_router)
    app.include_router(
        GraphQLRouter(schema, context_getter=context_getter), prefix="/graphql"
    )
    return app


@pytest.fixture
def api_client(api_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(api_app) as client:
        yield client
