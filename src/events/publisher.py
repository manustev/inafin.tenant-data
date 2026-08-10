"""Kafka doorbell.

ARCHITECTURE.md 2.1 — Kafka is an optimisation, never the queue of record. This
module is written so that misuse is difficult:

  * ``publish`` NEVER raises. A broker outage degrades latency, not correctness,
    because the batch is already READY in Silver and a polling consumer will
    find it. Raising here would let a Kafka failure roll back or retry an
    ingestion that has already committed.

  * The payload is a manifest — ids, counts, hashes, a period. No invoice
    numbers, no GSTINs, no amounts. Because no tenant business data crosses the
    wire, a shared topic partitioned by tenant is a scaling decision rather than
    a security one, and a mis-delivered message costs a consumer one refused
    read rather than a disclosure.
"""

from __future__ import annotations

import json
import logging
from types import TracebackType

from aiokafka import AIOKafkaProducer

from src.core.identifiers import kafka_key
from src.silver.promote import BatchManifest

logger = logging.getLogger(__name__)


class BatchPublisher:
    def __init__(self, bootstrap: str, topic: str, *, enabled: bool = True) -> None:
        self._bootstrap = bootstrap
        self._topic = topic
        self._enabled = enabled
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if not self._enabled:
            logger.info("kafka disabled — consumers will discover work by polling")
            return
        try:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                acks="all",
                enable_idempotence=True,
                linger_ms=5,
            )
            await self._producer.start()
        except Exception:
            # Same rationale as publish(): the doorbell failing to install must
            # not stop the house being built.
            logger.exception("kafka producer failed to start — falling back to poll-only")
            self._producer = None

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def __aenter__(self) -> BatchPublisher:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def publish(self, manifest: BatchManifest) -> bool:
        """Ring the doorbell. Returns whether it rang; never raises."""
        if self._producer is None:
            return False
        try:
            await self._producer.send_and_wait(
                self._topic,
                key=kafka_key(manifest.slug),
                value=json.dumps(manifest.to_json()).encode("utf-8"),
            )
            return True
        except Exception:
            logger.exception(
                "kafka publish failed for batch %s — batch remains READY in Silver "
                "and will be found by polling",
                manifest.batch_id,
            )
            return False
