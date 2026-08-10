"""Fixtures for the Bronze -> Silver -> Kafka -> Gold walking skeleton."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from src.bronze.service import BronzeIngestionService
from src.core.config import Settings
from src.core.pool import TenantScopedPool
from src.events.publisher import BatchPublisher
from src.provisioning.objectstore import S3ObjectStore
from src.reader.silver_reader import EngineVersions, SilverReader
from src.silver.promote import SilverPromotionService

PERIOD_START = dt.date(2026, 4, 1)
PERIOD_END = dt.date(2026, 4, 30)

BASE_VERSIONS = EngineVersions(
    rule_catalog_version="rc-2026.04.1",
    corpus_version="corpus-2026.04.15",
    tenant_pack_version="pack-v1",
)


#: The registry code. PURCHASE_REGISTER is no longer usable here — it no
#: longer promotes through this archetype-1 path (TYPED-TABLES-PLAN.md 10 step
#: 4, tenant migration 011, src/silver/promote.py's
#: _RETIRED_ARCHETYPE_1_TYPES) — so these end-to-end handoff gates, which exist
#: to prove generic pipeline properties (doorbell equivalence, replay
#: determinism, watermark skip, isolation) and were never about
#: PURCHASE_REGISTER specifically, use BILL_OF_ENTRY instead. Any type still on
#: the archetype-1 path would do; PURCHASE_REGISTER's own new path (Bronze ->
#: RegisterLoader -> purchase_register) is exercised by
#: tests/conformance/test_registers.py.
BILL_OF_ENTRY = "BILL_OF_ENTRY"


def bill_of_entry_csv(
    *,
    be_number: str = "BE-1001",
    counterparty_name: str = "Shenzhen Kaiyuan Electronics Co Ltd",
    lines: int = 2,
) -> bytes:
    """A BILL_OF_ENTRY export in the canonical archetype-1 column names.

    counterparty=FOREIGN (registry contract): no GSTIN, a name instead.
    be_number/be_date/port_code are the contract's required doc attributes —
    they land in `attributes`, not in dedicated columns.
    """
    header = (
        "doc_number,counterparty_name,doc_date,be_number,be_date,port_code,"
        "line_number,hsn_sac,description,quantity,uom,unit_price,taxable_value,"
        "gst_rate,igst_amount,itc_amount\n"
    )
    rows = "".join(
        f"{be_number},{counterparty_name},2026-04-15,{be_number},2026-04-15,"
        f"INNSA1,{n},8471301{n % 10},Item {n},10,NOS,1000.0000,10000.00,18.00,"
        f"1800.00,1800.00\n"
        for n in range(1, lines + 1)
    )
    return (header + rows).encode("utf-8")


@pytest.fixture(scope="session")
def object_store(settings: Settings) -> S3ObjectStore:
    return S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket_prefix=settings.s3_bucket_prefix,
        # 1 day, not the CGST s.36 floor of 2190. COMPLIANCE-mode locks cannot
        # be shortened or deleted by anyone, so a 6-year retention in a dev
        # bucket would be genuinely permanent litter on the MinIO volume.
        retention_days=1,
    )


@pytest_asyncio.fixture
async def bronze(
    app_pool: TenantScopedPool, object_store: S3ObjectStore, settings: Settings
) -> BronzeIngestionService:
    for slug in ("acme", "globex"):
        object_store.ensure_bucket(slug)
    return BronzeIngestionService(app_pool, object_store, bucket_prefix=settings.s3_bucket_prefix)


@pytest_asyncio.fixture
async def promoter(app_pool: TenantScopedPool) -> SilverPromotionService:
    return SilverPromotionService(app_pool)


@pytest_asyncio.fixture
async def reader(app_pool: TenantScopedPool) -> SilverReader:
    return SilverReader(app_pool, consumer_name="test-recon")


@pytest_asyncio.fixture
async def publisher(settings: Settings) -> AsyncIterator[BatchPublisher]:
    pub = BatchPublisher(settings.kafka_bootstrap, settings.kafka_batch_topic, enabled=True)
    await pub.start()
    try:
        yield pub
    finally:
        await pub.stop()


@pytest.fixture
def entity_id() -> uuid.UUID:
    return uuid.uuid4()
