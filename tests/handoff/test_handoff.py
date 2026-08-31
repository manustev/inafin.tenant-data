"""Handoff gates — ARCHITECTURE.md 10.9 to 10.12, plus end-to-end lineage.

These prove the property the whole design turns on: **Pipeline 1 and Pipeline 2
run independently.** Kafka is a doorbell, Silver's batch table is the queue of
record, and the two pipelines share a store without sharing a runtime
dependency.
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from botocore.exceptions import ClientError
from psycopg import sql
from tests.conftest import SeededTenant
from tests.handoff.conftest import (
    BASE_VERSIONS,
    PERIOD_END,
    PERIOD_START,
    bill_of_entry_csv,
)

from src.bronze.service import BronzeIngestionService
from src.core.errors import ValidationRejected
from src.events.publisher import BatchPublisher
from src.reader.silver_reader import EngineVersions, SilverReader
from src.silver.promote import SilverPromotionService

pytestmark = pytest.mark.handoff


async def _ingest(
    bronze: BronzeIngestionService,
    promoter: SilverPromotionService,
    tenant: SeededTenant,
    entity_id: uuid.UUID,
    csv_bytes: bytes,
):
    receipt = await bronze.receive(
        tenant.ctx, entity_id=entity_id, data=csv_bytes, filename="purchases.csv",
        document_type="BILL_OF_ENTRY",
    )
    manifest = await promoter.promote_transaction_documents(
        tenant.ctx,
        document_type="BILL_OF_ENTRY",
        ingest_id=receipt.ingest_id,
        entity_id=entity_id,
        data=csv_bytes,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )
    return receipt, manifest


async def _consume(
    reader: SilverReader, tenant: SeededTenant, versions: EngineVersions
) -> set[uuid.UUID]:
    """Drain every pending batch, writing one fact per invoice.

    Returns the set of batch_ids actually executed, not a count. Tenants are
    session-scoped and shared across this module, so any test asserting an exact
    number of drained batches would pass or fail on execution order rather than
    on the property it means to check.
    """
    done: set[uuid.UUID] = set()
    for batch in await reader.pending_batches(tenant.ctx, versions, document_type="BILL_OF_ENTRY"):
        invoices = await reader.read_invoices(tenant.ctx, batch.batch_id)
        facts = [
            (
                inv.invoice_id,
                "B2_180_DAY",
                "PAYMENT_DUE_TRACKED",
                "INFO",
                f"payment due tracked for {inv.invoice_number}",
                inv.bronze_ingest_id,
            )
            for inv in invoices
        ]
        if await reader.record_execution(tenant.ctx, batch, versions, facts=facts):
            done.add(batch.batch_id)
    return done


# =============================================================================
# End-to-end lineage — the Phase 1 exit criterion
# =============================================================================


async def test_lineage_resolves_gold_fact_back_to_bronze_object(
    bronze, promoter, reader, tenant_a, entity_id, admin
) -> None:
    """A Gold finding must resolve to a byte-exact, Object-Locked artefact.

    This is the chain a CA needs in a hearing, and proving it now is far cheaper
    than discovering a broken link after 130 document types are built on it.
    """
    csv_bytes = bill_of_entry_csv(be_number="PINV-LINEAGE")
    receipt, manifest = await _ingest(bronze, promoter, tenant_a, entity_id, csv_bytes)
    assert manifest.batch_id in await _consume(reader, tenant_a, BASE_VERSIONS)

    row = admin.execute(
        sql.SQL(
            """
            SELECT f.detail, l.object_bucket, l.object_key, l.content_hash
            FROM {gold}.fact_record f
            JOIN {bronze}.artefact_ledger l ON l.ingest_id = f.bronze_ingest_id
            WHERE f.batch_id = %s
            LIMIT 1
            """
        ).format(
            gold=sql.Identifier(tenant_a.ctx.gold_schema),
            bronze=sql.Identifier(tenant_a.ctx.bronze_schema),
        ),
        (manifest.batch_id,),
    ).fetchone()

    assert row is not None, "no fact resolved back to a bronze artefact"
    _, bucket, key, content_hash = row
    assert bucket == tenant_a.ctx.bucket("inafin-tenant")
    assert key.endswith(".csv")
    assert bytes(content_hash) == receipt.content_hash

    # And the bytes are still retrievable, unchanged.
    assert await bronze.fetch(tenant_a.ctx, receipt.ingest_id) == csv_bytes


# =============================================================================
# 10.9 — Doorbell equivalence
# =============================================================================


async def test_kafka_on_and_poll_only_produce_identical_gold(
    bronze, promoter, reader, publisher, tenant_a, tenant_b, admin
) -> None:
    """Same input, once with the doorbell rung and once without. Identical output.

    This is the test that keeps Kafka an optimisation. The moment output depends
    on whether a message was delivered, the two pipelines are coupled by the
    broker and backfill over old periods stops working.
    """
    csv_bytes = bill_of_entry_csv(be_number="PINV-DOORBELL")

    # acme: doorbell rung.
    e1 = uuid.uuid4()
    _, m1 = await _ingest(bronze, promoter, tenant_a, e1, csv_bytes)
    rang = await publisher.publish(m1)
    assert rang, "kafka is up in this environment; the doorbell should have rung"
    assert m1.batch_id in await _consume(reader, tenant_a, BASE_VERSIONS)

    # globex: no publish at all — the consumer must still find the work.
    e2 = uuid.uuid4()
    _, m2 = await _ingest(bronze, promoter, tenant_b, e2, csv_bytes)
    assert m2.batch_id in await _consume(reader, tenant_b, BASE_VERSIONS)

    def facts_for(tenant, batch_id):
        return admin.execute(
            sql.SQL(
                "SELECT rule_id, fact_type, severity FROM {}.fact_record"
                " WHERE batch_id = %s ORDER BY rule_id"
            ).format(sql.Identifier(tenant.ctx.gold_schema)),
            (batch_id,),
        ).fetchall()

    assert facts_for(tenant_a, m1.batch_id) == facts_for(tenant_b, m2.batch_id)
    assert facts_for(tenant_a, m1.batch_id), "no facts produced — gate is vacuous"


# =============================================================================
# 10.10 — Retention loss
# =============================================================================


async def test_work_survives_a_purged_topic(
    bronze, promoter, reader, tenant_a, entity_id, settings
) -> None:
    """Simulate the broker being gone entirely. Nothing is lost.

    A disabled publisher is the strongest form of "the message never arrived":
    retention expiry, a purged topic, an outage, or a consumer that was down
    longer than the retention window all reduce to this.
    """
    dead = BatchPublisher(settings.kafka_bootstrap, settings.kafka_batch_topic, enabled=False)
    await dead.start()

    _, manifest = await _ingest(
        bronze,
        promoter,
        tenant_a,
        entity_id,
        bill_of_entry_csv(be_number="PINV-NOKAFKA"),
    )
    assert await dead.publish(manifest) is False

    pending = await reader.pending_batches(
        tenant_a.ctx, BASE_VERSIONS, document_type="BILL_OF_ENTRY"
    )
    assert manifest.batch_id in {b.batch_id for b in pending}
    assert manifest.batch_id in await _consume(reader, tenant_a, BASE_VERSIONS)


async def test_publish_failure_never_raises(settings) -> None:
    """A broker outage must not fail an ingestion that already committed."""
    broken = BatchPublisher("127.0.0.1:1", settings.kafka_batch_topic, enabled=True)
    await broken.start()
    try:
        import datetime as dt

        from src.silver.promote import BatchManifest

        manifest = BatchManifest(
            slug="acme",
            batch_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            document_type="BILL_OF_ENTRY",
            source_stream="B",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            row_count=1,
            content_hash_hex="00",
            bronze_manifest_ref=str(uuid.uuid4()),
            ready_at=dt.datetime.now(dt.UTC),
        )
        assert await broken.publish(manifest) is False
    finally:
        await broken.stop()


# =============================================================================
# 10.11 — Independence
# =============================================================================


async def test_pipeline_2_catches_up_after_being_down(bronze, promoter, reader, tenant_a) -> None:
    """Pipeline 1 runs for a long stretch with no consumer. Nothing is lost."""
    batch_ids = []
    for n in range(5):
        e = uuid.uuid4()
        _, m = await _ingest(
            bronze,
            promoter,
            tenant_a,
            e,
            bill_of_entry_csv(be_number=f"PINV-BACKLOG-{n}"),
        )
        batch_ids.append(m.batch_id)

    pending = {
        b.batch_id
        for b in await reader.pending_batches(
            tenant_a.ctx, BASE_VERSIONS, document_type="BILL_OF_ENTRY"
        )
    }
    assert set(batch_ids).issubset(pending), "backlog was not fully discoverable"

    consumed = await _consume(reader, tenant_a, BASE_VERSIONS)
    assert set(batch_ids).issubset(consumed), "backlog was not fully drained"
    still = {
        b.batch_id
        for b in await reader.pending_batches(
            tenant_a.ctx, BASE_VERSIONS, document_type="BILL_OF_ENTRY"
        )
    }
    assert not set(batch_ids) & still


async def test_late_committing_batch_is_not_skipped(
    bronze, promoter, reader, tenant_a, admin
) -> None:
    """The high-watermark skip, made concrete.

    A batch whose ready_at predates the current watermark — exactly what happens
    when a transaction takes its timestamp early and commits late — must still
    be picked up. Without the overlap window this batch is silently lost
    forever: no error, no alert, just a period that never reconciles.
    """
    e = uuid.uuid4()
    _, recent = await _ingest(
        bronze, promoter, tenant_a, e, bill_of_entry_csv(be_number="PINV-WM-A")
    )
    await _consume(reader, tenant_a, BASE_VERSIONS)  # advances the watermark

    e2 = uuid.uuid4()
    _, late = await _ingest(
        bronze, promoter, tenant_a, e2, bill_of_entry_csv(be_number="PINV-WM-B")
    )
    # Backdate it to just inside the overlap window, as a late commit would appear.
    admin.execute(
        sql.SQL(
            "UPDATE {}.ingest_batch SET ready_at = now() - interval '5 minutes' WHERE batch_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (late.batch_id,),
    )

    pending = {
        b.batch_id
        for b in await reader.pending_batches(
            tenant_a.ctx, BASE_VERSIONS, document_type="BILL_OF_ENTRY"
        )
    }
    assert late.batch_id in pending, (
        "a batch that committed late was skipped — the overlap window is not working"
    )
    assert recent.batch_id not in pending, "already-executed batch was re-offered"


# =============================================================================
# 10.12 — Replay determinism
# =============================================================================


async def test_replay_is_idempotent_and_rule_change_forces_rerun(
    bronze, promoter, reader, tenant_a, admin
) -> None:
    """Same version tuple: nothing new. Bumped rule version: a full re-run.

    This is what makes rule-change backfill an ordinary operation rather than a
    special tool — and what makes "which corpus version produced this finding"
    answerable afterwards.
    """
    e = uuid.uuid4()
    _, manifest = await _ingest(
        bronze, promoter, tenant_a, e, bill_of_entry_csv(be_number="PINV-REPLAY")
    )
    assert manifest.batch_id in await _consume(reader, tenant_a, BASE_VERSIONS)

    def fact_count() -> int:
        row = admin.execute(
            sql.SQL("SELECT count(*) FROM {}.fact_record WHERE batch_id = %s").format(
                sql.Identifier(tenant_a.ctx.gold_schema)
            ),
            (manifest.batch_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    after_first = fact_count()
    assert after_first > 0

    # Same versions -> this batch is not offered again, and produces no new rows.
    assert manifest.batch_id not in await _consume(reader, tenant_a, BASE_VERSIONS)
    assert fact_count() == after_first

    # Bumped rule catalog -> a new, complete execution.
    bumped = dataclasses.replace(BASE_VERSIONS, rule_catalog_version="rc-2026.05.1")
    offered = {
        b.batch_id
        for b in await reader.pending_batches(tenant_a.ctx, bumped, document_type="BILL_OF_ENTRY")
    }
    assert manifest.batch_id in offered, "rule change did not re-offer the batch"
    assert manifest.batch_id in await _consume(reader, tenant_a, bumped)
    assert fact_count() > after_first

    versions = admin.execute(
        sql.SQL(
            "SELECT DISTINCT rule_catalog_version FROM {}.fact_record"
            " WHERE batch_id = %s ORDER BY 1"
        ).format(sql.Identifier(tenant_a.ctx.gold_schema)),
        (manifest.batch_id,),
    ).fetchall()
    assert [v[0] for v in versions] == ["rc-2026.04.1", "rc-2026.05.1"]


# =============================================================================
# Validation and dedup
# =============================================================================


async def test_invalid_file_is_quarantined_not_promoted(
    bronze, promoter, tenant_a, entity_id, admin
) -> None:
    """A rejected artefact is retained as evidence, and creates no batch."""
    # BILL_OF_ENTRY's contract is counterparty=FOREIGN (registry): an overseas
    # supplier has no GSTIN to demand, so supplying one is the error — not a
    # missing one, as it would be for a REQUIRED-counterparty type.
    bad = (
        b"doc_number,counterparty_gstin,counterparty_name,doc_date,be_number,"
        b"be_date,port_code,line_number,hsn_sac,quantity,unit_price,"
        b"taxable_value,gst_rate\n"
        b"BE-BAD,27AAAPA1234A1Z5,Some Overseas Ltd,2026-04-15,BE-BAD,"
        b"2026-04-15,INNSA1,1,84713010,10,1000,10000,18\n"
    )

    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=bad, filename="bad.csv",
        document_type="BILL_OF_ENTRY",
    )
    with pytest.raises(ValidationRejected, match="overseas counterparty"):
        await promoter.promote_transaction_documents(
            tenant_a.ctx,
            document_type="BILL_OF_ENTRY",
            ingest_id=receipt.ingest_id,
            entity_id=entity_id,
            data=bad,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

    # The verdict lands in SILVER, not on the artefact.
    row = admin.execute(
        sql.SQL("SELECT reason FROM {}.quarantined_artefact WHERE ingest_id = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (receipt.ingest_id,),
    ).fetchone()
    assert row is not None, "quarantine verdict was not recorded"
    assert "overseas counterparty" in row[0]

    # ... and produced no batch.
    batches = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.ingest_batch WHERE bronze_manifest_ref = %s").format(
            sql.Identifier(tenant_a.ctx.silver_schema)
        ),
        (str(receipt.ingest_id),),
    ).fetchone()
    assert batches is not None and batches[0] == 0, (
        "a quarantined artefact must not produce a batch"
    )

    # The bytes survive — a rejected file is still evidence of what was sent.
    assert await bronze.fetch(tenant_a.ctx, receipt.ingest_id) == bad


async def test_identical_bytes_in_two_tenants_both_succeed(bronze, tenant_a, tenant_b) -> None:
    """ARCHITECTURE.md 9 finding 1, made a test.

    A global fingerprint key would reject the second tenant's upload as a
    duplicate of a document it cannot see — losing their file AND disclosing
    that someone else holds it. Under schema-per-tenant the dedup domain is one
    schema, so this is correct by construction. The test exists to keep it that
    way if anyone ever proposes centralising the registry.
    """
    shared = bill_of_entry_csv(be_number="PINV-SHARED-TEMPLATE")

    r1 = await bronze.receive(
        tenant_a.ctx, entity_id=uuid.uuid4(), data=shared, filename="t.csv",
        document_type="BILL_OF_ENTRY",
    )
    r2 = await bronze.receive(
        tenant_b.ctx, entity_id=uuid.uuid4(), data=shared, filename="t.csv",
        document_type="BILL_OF_ENTRY",
    )

    assert r1.deduplicated is False
    assert r2.deduplicated is False, "cross-tenant dedup collision — this is a disclosure"
    assert r1.content_hash == r2.content_hash
    assert r1.ingest_id != r2.ingest_id
    assert r1.bucket != r2.bucket


async def test_same_bytes_twice_in_one_tenant_dedups(bronze, tenant_a, entity_id) -> None:
    data = bill_of_entry_csv(be_number="PINV-DEDUP")
    first = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="BILL_OF_ENTRY",
    )
    second = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="BILL_OF_ENTRY",
    )
    assert second.deduplicated is True
    assert second.ingest_id == first.ingest_id


async def test_bronze_object_is_immutable(bronze, object_store, tenant_a, entity_id) -> None:
    """Object Lock in COMPLIANCE mode: the artefact version cannot be destroyed.

    Note the distinction that makes this test worth writing carefully. On a
    versioned bucket, DELETE without a VersionId succeeds — it writes a delete
    marker and hides the object without destroying it. Asserting on that would
    prove nothing about retention. The real WORM property is that deleting the
    *specific version* is refused, and that the bytes remain readable.
    """
    data = bill_of_entry_csv(be_number="PINV-WORM")
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="BILL_OF_ENTRY",
    )

    head = object_store._client.head_object(Bucket=receipt.bucket, Key=receipt.object_key)
    version_id = head["VersionId"]
    assert head["ObjectLockMode"] == "COMPLIANCE"

    with pytest.raises(ClientError) as exc:
        object_store._client.delete_object(
            Bucket=receipt.bucket,
            Key=receipt.object_key,
            VersionId=version_id,
            BypassGovernanceRetention=True,
        )
    assert exc.value.response["Error"]["Code"] in {"AccessDenied", "InvalidRequest"}

    assert await bronze.fetch(tenant_a.ctx, receipt.ingest_id) == data


async def test_one_tenants_artefact_is_invisible_to_another(
    bronze, tenant_a, tenant_b, entity_id, admin
) -> None:
    """Cross-check on the live data path: the boundary holds for real artefacts.

    tenant_b asks for tenant_a's ingest_id by its exact uuid. Under
    schema-per-tenant the lookup runs against globex's own ledger, so it simply
    is not there — the isolation shows up as absence, not as a denial.
    """
    receipt = await bronze.receive(
        tenant_a.ctx,
        entity_id=entity_id,
        data=bill_of_entry_csv(be_number="PINV-CROSSCHECK"),
        filename="t.csv",
        document_type="BILL_OF_ENTRY",
    )

    # POSITIVE CONTROL: the artefact genuinely exists in acme.
    row = admin.execute(
        sql.SQL("SELECT count(*) FROM {}.artefact_ledger WHERE ingest_id = %s").format(
            sql.Identifier(tenant_a.ctx.bronze_schema)
        ),
        (receipt.ingest_id,),
    ).fetchone()
    assert row is not None and row[0] == 1

    with pytest.raises(KeyError):
        await bronze.fetch(tenant_b.ctx, receipt.ingest_id)

    assert await bronze.fetch(tenant_a.ctx, receipt.ingest_id) is not None
