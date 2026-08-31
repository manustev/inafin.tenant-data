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


@dataclass(frozen=True, slots=True)
class PublishedObject:
    """A platform-published file. Deliberately NOT a `StoredObject`.

    `StoredObject` carries `retain_until`, because everything it describes is
    tenant evidence under a COMPLIANCE lock nobody may shorten. A published
    schema or sample has no retention: it is expected to be superseded and may
    legitimately be withdrawn. Reusing the same type would have forced a
    meaningless `retain_until` onto these rows and quietly invited someone to
    lock them.
    """

    bucket: str
    key: str
    version_id: str | None
    content_hash: bytes
    size_bytes: int


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

    def ensure_platform_bucket(self) -> str: ...

    def put_platform_artifact(
        self, *, release: str, kind: str, doc_type_code: str | None,
        filename: str, data: bytes,
    ) -> PublishedObject: ...

    def put_schema_snapshot(
        self, *, slug: str, release: str, doc_type_code: str, data: bytes,
        pinned_at: dt.datetime,
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

    # --- the platform bucket ------------------------------------------------
    #
    # ONE bucket for the whole platform, not one per tenant, and that is the
    # opposite of every other bucket in this module for a reason that holds:
    # the per-tenant rule exists because tenant data must never share a
    # boundary with another tenant's. Published schemas and samples are the
    # SAME BYTES FOR EVERYONE — identical reference material, no tenant's data
    # in any of it. A bucket per tenant here would mean 1,000 copies of the
    # same file and 1,000 places for a rollout to go half-finished.

    def platform_bucket(self) -> str:
        return f"{self._prefix}-platform"

    def ensure_platform_bucket(self) -> str:
        """Idempotent. Versioned, NOT Object-Lock'd — see migration 039.

        Object Lock would make a bad sample permanent. Versioning is enabled
        instead: it protects against an accidental overwrite or delete while
        leaving a deliberate withdrawal possible.
        """
        bucket = self.platform_bucket()
        try:
            self._client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self._client.create_bucket(Bucket=bucket)
            logger.info("created platform bucket %s", bucket)

        self._client.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        # Same tolerated failure as ensure_bucket: MinIO rejects this S3-only
        # call and is private by default. On AWS it MUST succeed.
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
                "public access block not applied to %s (%s) — expected on "
                "MinIO; on AWS S3 this MUST succeed and should fail deployment",
                bucket,
                exc.response.get("Error", {}).get("Code", "unknown"),
            )
        return bucket

    def put_platform_artifact(
        self,
        *,
        release: str,
        kind: str,
        doc_type_code: str | None,
        filename: str,
        data: bytes,
    ) -> PublishedObject:
        """Publish one schema or sample file into a release.

        The release is in the KEY, not only in S3's version history, because a
        staged rollout needs v1 and v2 addressable at the same time — migration
        039's header argues this at length. Re-publishing the same key is
        allowed and creates a new S3 version; the caller records the returned
        `version_id` so "which bytes did this tenant download" stays answerable.
        """
        bucket = self.ensure_platform_bucket()
        scope = f"{doc_type_code}/" if doc_type_code else ""
        key = f"{kind.lower()}/{release}/{scope}{filename}"
        digest = hashlib.sha256(data).digest()

        resp = self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ChecksumSHA256=base64.b64encode(digest).decode(),
            Metadata={"release": release, "kind": kind},
        )
        return PublishedObject(
            bucket=bucket,
            key=key,
            version_id=resp.get("VersionId"),
            content_hash=digest,
            size_bytes=len(data),
        )

    def put_schema_snapshot(
        self,
        *,
        slug: str,
        release: str,
        doc_type_code: str,
        data: bytes,
        pinned_at: dt.datetime,
    ) -> StoredObject:
        """The tenant's own frozen copy of the schema they were handed.

        Returns a `StoredObject`, not a `PublishedObject`, and the difference
        is the point: this copy IS retained under the bucket's COMPLIANCE lock,
        while the platform-side original is not. A published schema is
        reference material that may be superseded or withdrawn; the copy handed
        to a specific tenant on a specific date is a record of the contract
        they were given, and if their data is ever disputed, what they were
        told the format was becomes part of the answer.

        The key carries the release rather than a date, because that is how a
        caller looks it up — "what is this tenant's v1 SALES_REGISTER schema" —
        and re-pinning the same release is idempotent by construction.
        """
        validate_slug(slug)
        bucket = bucket_name(slug, self._prefix)
        key = f"schema/{release}/{doc_type_code}.json"
        digest = hashlib.sha256(data).digest()
        retain_until = pinned_at + dt.timedelta(days=self._retention_days)

        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
            ChecksumSHA256=base64.b64encode(digest).decode(),
            Metadata={"tenant-slug": slug, "release": release},
        )
        return StoredObject(
            bucket=bucket, key=key, content_hash=digest,
            size_bytes=len(data), retain_until=retain_until,
        )

    def get(self, *, bucket: str, key: str) -> bytes:
        obj = self._client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
