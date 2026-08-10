-- =============================================================================
-- Tenant migration 002 — Silver: the batch manifest and purchase invoices.
--
-- ingest_batch is the QUEUE OF RECORD (ARCHITECTURE.md 2.1). Kafka is only a
-- doorbell: if Kafka is lost, purged, or never consumed, every pending unit of
-- work is still discoverable here. That is the property that lets Pipeline 1
-- and Pipeline 2 run independently, and it is what makes rule-change backfill
-- over old periods possible at all.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.ingest_batch (
    batch_id            uuid PRIMARY KEY,
    entity_id           uuid NOT NULL,

    document_type       text NOT NULL,
    document_type_vt    text NOT NULL DEFAULT 'Document_Type'
        CHECK (document_type_vt = 'Document_Type'),

    source_stream       text NOT NULL,
    source_stream_vt    text NOT NULL DEFAULT 'Source_Stream'
        CHECK (source_stream_vt = 'Source_Stream'),

    period_start        date NOT NULL,
    period_end          date NOT NULL,
    row_count           integer NOT NULL CHECK (row_count >= 0),

    -- Idempotency anchor: identical content re-ingested yields the same hash,
    -- so a redelivered file is recognisable without parsing it again.
    content_hash        bytea NOT NULL,
    bronze_manifest_ref text NOT NULL,

    status              text NOT NULL DEFAULT 'READY',
    status_vt           text NOT NULL DEFAULT 'Batch_Status'
        CHECK (status_vt = 'Batch_Status'),

    -- The cursor axis for Pipeline 2. Consumers read `ready_at > watermark -
    -- overlap` and dedupe, because a naive strict-greater cursor silently skips
    -- rows that take their timestamp early and commit late.
    ready_at            timestamptz NOT NULL DEFAULT now(),
    ingest_run_id       uuid NOT NULL,
    superseded_by       uuid REFERENCES {{silver}}.ingest_batch (batch_id),

    CONSTRAINT ingest_batch_period CHECK (period_end >= period_start),
    FOREIGN KEY (document_type_vt, document_type)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (source_stream_vt, source_stream)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (status_vt, status)
        REFERENCES platform_ref.universal_master (value_type, value)
);

-- The consumer's hot path. Partial index: only READY batches are ever scanned.
CREATE INDEX IF NOT EXISTS ingest_batch_ready_idx
    ON {{silver}}.ingest_batch (ready_at)
    WHERE status = 'READY';

CREATE INDEX IF NOT EXISTS ingest_batch_scope_idx
    ON {{silver}}.ingest_batch (entity_id, document_type, period_start, period_end);


-- --- Purchase invoice — header ---------------------------------------------
-- Archetype 1 (transaction line records). Hybrid storage: the original file
-- stays in Bronze under Object Lock; these are the fields the platform reasons
-- against. PHASE1-PLAN.md D5 — chosen for the skeleton precisely because it
-- exercises the line-item path, which is where the real shape lives.

CREATE TABLE IF NOT EXISTS {{silver}}.purchase_invoice (
    invoice_id          uuid PRIMARY KEY,
    batch_id            uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    entity_id           uuid NOT NULL,

    invoice_number      text NOT NULL,
    supplier_gstin      text NOT NULL CHECK (supplier_gstin ~ '^[0-9]{2}[A-Z0-9]{13}$'),
    invoice_date        date NOT NULL,
    total_taxable_value numeric(18, 2) NOT NULL,
    total_tax_value     numeric(18, 2) NOT NULL,
    total_value         numeric(18, 2) NOT NULL,
    payment_due_date    date,
    currency            text NOT NULL DEFAULT 'INR',

    -- Bitemporal (ARCHITECTURE.md 8). valid_* is when the fact was true in the
    -- world; recorded_at / superseded_at is when the platform knew it. Nothing
    -- is updated in place — a correction closes the prior version and appends.
    valid_from          date NOT NULL,
    valid_to            date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    superseded_at       timestamptz,

    -- Lineage back to the byte-exact artefact. This is the chain a CA needs in
    -- a hearing, and proving it end to end is a Phase 1 exit criterion.
    bronze_ingest_id    uuid NOT NULL,

    CONSTRAINT purchase_invoice_window CHECK (valid_to > valid_from),
    CONSTRAINT purchase_invoice_totals
        CHECK (total_value = total_taxable_value + total_tax_value)
);

-- One live version per (entity, supplier, invoice number). Partial, so
-- superseded versions accumulate freely — which is the point of bitemporality.
CREATE UNIQUE INDEX IF NOT EXISTS purchase_invoice_natural_key_uq
    ON {{silver}}.purchase_invoice (entity_id, supplier_gstin, invoice_number)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS purchase_invoice_batch_idx
    ON {{silver}}.purchase_invoice (batch_id);


-- --- Purchase invoice — lines ----------------------------------------------

CREATE TABLE IF NOT EXISTS {{silver}}.purchase_invoice_line (
    line_id        uuid PRIMARY KEY,
    invoice_id     uuid NOT NULL
                       REFERENCES {{silver}}.purchase_invoice (invoice_id) ON DELETE CASCADE,
    line_number    integer NOT NULL CHECK (line_number > 0),
    hsn_sac        text NOT NULL CHECK (hsn_sac ~ '^[0-9]{4,8}$'),
    description    text,
    quantity       numeric(18, 3) NOT NULL,
    unit_of_measure text,
    unit_price     numeric(18, 4) NOT NULL,
    taxable_value  numeric(18, 2) NOT NULL,
    gst_rate       numeric(5, 2) NOT NULL CHECK (gst_rate >= 0 AND gst_rate <= 100),
    cgst_amount    numeric(18, 2) NOT NULL DEFAULT 0,
    sgst_amount    numeric(18, 2) NOT NULL DEFAULT 0,
    igst_amount    numeric(18, 2) NOT NULL DEFAULT 0,
    itc_amount     numeric(18, 2) NOT NULL DEFAULT 0,

    -- A line is either intra-state (CGST+SGST) or inter-state (IGST), never
    -- both. Anomaly type T3 (tax head error) is detected downstream against
    -- re-derived place of supply; this only rejects the structurally impossible.
    CONSTRAINT purchase_invoice_line_tax_head CHECK (
        (igst_amount > 0 AND cgst_amount = 0 AND sgst_amount = 0)
        OR (igst_amount = 0)
    ),
    UNIQUE (invoice_id, line_number)
);

CREATE INDEX IF NOT EXISTS purchase_invoice_line_hsn_idx
    ON {{silver}}.purchase_invoice_line (hsn_sac);
