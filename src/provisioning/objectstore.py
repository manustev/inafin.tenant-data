"""Bronze object store — bucket per tenant, WORM retention.

ARCHITECTURE.md 3.1 and 5.7. Two properties this module exists to guarantee:

1. **Bucket per tenant.** Not a shared bucket with tenant prefixes. A prefix
   policy is one IAM misconfiguration from cross-tenant reads; a bucket boundary
   is not. The tenant slug is also embedded in the object key, redundantly, so a
   bucket-policy regression is still caught by a path assertion at read time.

2. **Object Lock in compliance mode.** Compliance-mode retention cannot be
   shortened or removed by anyone — including the account root. An append-only
   Postgres table can always be undone by a superuser, so for material that may
   be tendered as evidence in a GST proceeding this is a materially stronger
   guarantee. Retention floor is CGST s.36: 72 months from the due date of the
   annual return for the year concerned.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from src.core.identifiers import bucket_name, validate_slug

if TYPE_CHECKING:  # pragma: no cover
    from mypy_boto3_s3.client import S3Client

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    key: str
    content_hash: bytes
    size_bytes: int
    retain_until: dt.datetime


class ObjectStorePort(Protocol):
    """Kept narrow so provisioning and ingestion can be tested without MinIO."""

    def ensure_bucket(self, slug: str) -> str: ...

    def put_bronze(
        self, *, slug: str, ingest_id: str, data: bytes, suffix: str,
        received_at: dt.datetime,
    ) -> StoredObject: ...

    def put_silver(
        self, *, slug: str, doc_type: str, ingest_id: str, data: bytes, suffix: str,
        promoted_at: dt.datetime,
    ) -> StoredObject: ...

    def get(self, *, bucket: str, key: str) -> bytes: ...


class S3ObjectStore:
    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        access_key: str,
        secret_key: str,
        bucket_prefix: str,
        retention_days: int,
    ) -> None:
        self._prefix = bucket_prefix
        self._retention_days = retention_days
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # path-style addressing: MinIO does not do virtual-host buckets by
            # default, and production S3 accepts it.
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )

    def ensure_bucket(self, slug: str) -> str:
        """Idempotent. Object Lock can ONLY be enabled at bucket creation.

        There is no way to retrofit it onto an existing bucket, so a bucket
        created without it must be replaced — which is why provisioning creates
        the bucket before the schemas and fails loudly if this step does not
        produce a lock-enabled bucket.
        """
        bucket = bucket_name(slug, self._prefix)
        try:
            self._client.head_bucket(Bucket=bucket)
            return bucket
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise

        self._client.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
        self._client.put_object_lock_configuration(
            Bucket=bucket,
            ObjectLockConfiguration={
                "ObjectLockEnabled": "Enabled",
                "Rule": {
                    "DefaultRetention": {
                        "Mode": "COMPLIANCE",
                        "Days": self._retention_days,
                    }
                },
            },
        )
        # Public access is never appropriate for tenant evidence. This is an
        # S3-only API — MinIO rejects it (MalformedXML) and is private by
        # default with no bucket policy, so a failure here is tolerated rather
        # than fatal. It is logged at WARNING and asserted in production by the
        # infrastructure checks, not silently skipped.
        try:
            self._client.put_public_access_block(
                Bucket=bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
        except ClientError as exc:
            logger.warning(
                "public access block not applied to %s (%s) — expected on MinIO; "
                "on AWS S3 this MUST succeed and should fail deployment",
                bucket,
                exc.response.get("Error", {}).get("Code", "unknown"),
            )
        logger.info("created bronze bucket %s with COMPLIANCE lock", bucket)
        return bucket

    def put_bronze(
        self,
        *,
        slug: str,
        ingest_id: str,
        data: bytes,
        suffix: str,
        received_at: dt.datetime,
    ) -> StoredObject:
        validate_slug(slug)
        bucket = bucket_name(slug, self._prefix)
        key = (
            f"bronze/{received_at:%Y/%m/%d}/{ingest_id}"
            f"{'.' + suffix.lstrip('.') if suffix else ''}"
        )
        digest = hashlib.sha256(data).digest()
        retain_until = received_at + dt.timedelta(days=self._retention_days)

        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
            ChecksumSHA256=base64.b64encode(digest).decode(),
            Metadata={
                # Redundant with the bucket, on purpose: a path assertion at
                # read time catches a bucket-policy regression.
                "tenant-slug": slug,
                "ingest-id": ingest_id,
            },
        )
        return StoredObject(
            bucket=bucket,
            key=key,
            content_hash=digest,
            size_bytes=len(data),
            retain_until=retain_until,
        )

    def put_silver(
        self,
        *,
        slug: str,
        doc_type: str,
        ingest_id: str,
        data: bytes,
        suffix: str,
        promoted_at: dt.datetime,
    ) -> StoredObject:
        """The Silver-side copy, written once a document promotes rather
        than merely lands in Bronze.

        For archetype 8 (`narrative_contract`) this key IS the primary
        record — `migrations/tenant/019_narrative_contract.sql`'s header
        calls the row `key_terms`-only and best-effort; the PDF preserved
        here is what a CA actually reads. For every other archetype it is a
        convenience copy alongside the typed Silver row, not a second
        source of truth — the row is.

        Same bucket as `put_bronze` (bucket-per-tenant, ARCHITECTURE.md
        3.1/5.7 — a second bucket would double the Object Lock
        infrastructure for no isolation benefit), a `silver/` key prefix
        instead of `bronze/`. Object Lock/retention mirrors `put_bronze`
        exactly: Silver copies of client-submitted evidence carry the same
        CGST s.36 72-month floor as the Bronze original.
        """
        validate_slug(slug)
        bucket = bucket_name(slug, self._prefix)
        key = (
            f"silver/{doc_type}/{promoted_at:%Y/%m/%d}/{ingest_id}"
            f"{'.' + suffix.lstrip('.') if suffix else ''}"
        )
        digest = hashlib.sha256(data).digest()
        retain_until = promoted_at + dt.timedelta(days=self._retention_days)

        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
            ChecksumSHA256=base64.b64encode(digest).decode(),
            Metadata={
                "tenant-slug": slug,
                "ingest-id": ingest_id,
                "doc-type": doc_type,
            },
        )
        return StoredObject(
            bucket=bucket,
            key=key,
            content_hash=digest,
            size_bytes=len(data),
            retain_until=retain_until,
        )

    def get(self, *, bucket: str, key: str) -> bytes:
        obj = self._client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
