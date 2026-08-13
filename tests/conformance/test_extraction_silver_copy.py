"""put_silver — the Silver-side MinIO copy, verified against the REAL dev
MinIO (`api_object_store`, `tests/conformance/conftest.py`), same cluster
`put_bronze` is already proven against.

streamed-marinating-gray.md's verification step: "Verify a `silver/...` key
actually lands in MinIO after a real promotion, alongside the existing
`bronze/...` key — both readable, tenant-bucket-scoped."
"""

from __future__ import annotations

import datetime as dt
import pathlib
import uuid

import pytest
from tests.conftest import SeededTenant

from src.core.pool import TenantScopedPool
from src.extraction.base import DocumentExtractor
from src.extraction.labelvalue import Extracted
from src.extraction.reader import PypdfReader
from src.extraction.registry import build_extractor_registry
from src.provisioning.objectstore import S3ObjectStore

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "reference" / "A1-A7Documents"


async def test_put_silver_lands_a_readable_key_alongside_bronze(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    api_object_store: S3ObjectStore,
) -> None:
    pdf_bytes = (SAMPLES / "A5.02_IEC_Certificate.pdf").read_bytes()

    # The Bronze copy an upload would already have made — proves the two
    # coexist under the same tenant bucket, same as put_bronze's own tests.
    bronze_ingest_id = str(uuid.uuid4())
    bronze_obj = api_object_store.put_bronze(
        slug=tenant_a.slug, ingest_id=bronze_ingest_id, data=pdf_bytes,
        suffix="pdf", received_at=dt.datetime.now(dt.UTC),
    )
    assert api_object_store.get(bucket=bronze_obj.bucket, key=bronze_obj.key) == pdf_bytes

    # Built with `store=api_object_store` directly (not via the
    # `extractor_registry` fixture, which builds every extractor with
    # `store=None`) — this test needs the one instance that actually writes
    # the Silver MinIO copy.
    registry: dict[str, DocumentExtractor] = await build_extractor_registry(
        app_pool, tenant_a.ctx, store=api_object_store
    )
    extractor = registry["IEC_CERTIFICATE"]
    outcome = extractor.extract(PypdfReader().extract(pdf_bytes).pages)
    assert isinstance(outcome, Extracted)

    ingest_id = uuid.uuid4()
    result = await extractor.to_silver(
        outcome, tenant_a.ctx,
        entity_id=tenant_a.entity_id, ingest_id=ingest_id, pdf_bytes=pdf_bytes,
    )
    assert result.written

    expected_prefix = "silver/IEC_CERTIFICATE/"
    # Confirm a matching object actually exists by listing the bucket rather
    # than reconstructing the exact date-stamped key here — that key format
    # is put_silver's own concern (src/provisioning/objectstore.py), not
    # this test's.
    listing = api_object_store._client.list_objects_v2(
        Bucket=bronze_obj.bucket, Prefix=expected_prefix
    )
    keys = [obj["Key"] for obj in listing.get("Contents", [])]
    matching = [k for k in keys if str(ingest_id) in k]
    assert matching, f"no silver/ key for {ingest_id} found under {expected_prefix}: {keys}"
    assert api_object_store.get(bucket=bronze_obj.bucket, key=matching[0]) == pdf_bytes
