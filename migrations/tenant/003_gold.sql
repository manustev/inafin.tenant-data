-- =============================================================================
-- Tenant migration 003 — Gold: reconciliation output, owned by Pipeline 2.
--
-- Pipeline 1 (ingest role) has NO grant here at all. Pipeline 2 (recon role)
-- has DML here and SELECT on nothing but the silver v1_ views. Neither can do
-- the other's job — ARCHITECTURE.md 5.1.
-- =============================================================================

-- The dedupe key for batch consumption. NOT keyed on batch_id alone: re-running
-- a batch after a rule change is a legitimate new execution, not a duplicate.
-- The composite key makes retries safe AND makes "which corpus version produced
-- this finding" answerable — which is the question a CA is asked in a hearing.
CREATE TABLE IF NOT EXISTS {{gold}}.batch_execution (
    batch_id             uuid NOT NULL,
    rule_catalog_version text NOT NULL,
    corpus_version       text NOT NULL,
    tenant_pack_version  text NOT NULL,
    executed_at          timestamptz NOT NULL DEFAULT now(),
    row_count            integer NOT NULL CHECK (row_count >= 0),
    PRIMARY KEY (batch_id, rule_catalog_version, corpus_version, tenant_pack_version)
);

CREATE INDEX IF NOT EXISTS batch_execution_executed_idx
    ON {{gold}}.batch_execution (executed_at);


-- Consumer cursor. Lives in GOLD, not Silver, deliberately: Silver is read-only
-- to Pipeline 2, so `SELECT ... FOR UPDATE SKIP LOCKED` against the batch table
-- is not available and not wanted. Claim state belongs to the claimant.
CREATE TABLE IF NOT EXISTS {{gold}}.consumer_watermark (
    consumer_name text NOT NULL,
    document_type text NOT NULL,
    last_ready_at timestamptz NOT NULL,
    last_batch_id uuid,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer_name, document_type)
);


-- Minimal fact record so the skeleton produces real output. The full shape is
-- v2's InvoiceFactRecord; this carries only what Phase 1 needs to prove the
-- lineage chain resolves end to end.
CREATE TABLE IF NOT EXISTS {{gold}}.fact_record (
    fact_id          uuid PRIMARY KEY,
    batch_id         uuid NOT NULL,
    invoice_id       uuid NOT NULL,
    rule_id          text NOT NULL,
    fact_type        text NOT NULL,
    severity         text NOT NULL CHECK (severity IN ('INFO', 'WARN', 'CRITICAL')),
    detail           text NOT NULL,

    -- Lineage. Every Gold row names the Bronze object it ultimately derives
    -- from, so a finding resolves to a byte-exact, Object-Locked artefact in
    -- one query chain.
    bronze_ingest_id uuid NOT NULL,

    rule_catalog_version text NOT NULL,
    corpus_version       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fact_record_batch_idx  ON {{gold}}.fact_record (batch_id);
CREATE INDEX IF NOT EXISTS fact_record_invoice_idx ON {{gold}}.fact_record (invoice_id);
