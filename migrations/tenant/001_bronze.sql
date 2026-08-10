-- =============================================================================
-- Tenant migration 001 — Bronze: the artefact ledger.
--
-- Templated. {{bronze}} / {{silver}} / {{gold}} are substituted with this
-- tenant's quoted schema names by src/migrate/runner.py.
--
-- Bronze holds NO business content. The bytes live in the tenant's object store
-- bucket under Object Lock (compliance mode); this table is the index over them.
-- ARCHITECTURE.md 3.1 — one uniform rule: every ingest produces exactly one
-- Bronze object plus one ledger row, whether it arrived as a PDF upload or a
-- GSTN API response. Two code paths would mean two evidentiary standards.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{bronze}}.artefact_ledger (
    ingest_id              uuid PRIMARY KEY,
    entity_id              uuid NOT NULL,

    declared_document_type text NOT NULL,
    -- Composite FK into the single universal master table. `value` alone is not
    -- unique across value_types, so the referencing side pins the value_type in
    -- a CHECK-constrained column. This is the pattern every categorical column
    -- in the platform follows (Full Data Architecture, Category B Pattern 1).
    declared_document_type_vt text NOT NULL DEFAULT 'Document_Type'
        CHECK (declared_document_type_vt = 'Document_Type'),

    source_stream          text NOT NULL,
    source_stream_vt       text NOT NULL DEFAULT 'Source_Stream'
        CHECK (source_stream_vt = 'Source_Stream'),

    -- Chain of custody. Doc 1 Foundation: in Forensic Mode this record is
    -- itself a submitted legal artefact, so every field here is evidentiary.
    received_at            timestamptz NOT NULL DEFAULT now(),
    content_hash           bytea NOT NULL,          -- sha256 of the raw bytes
    object_key             text NOT NULL,
    object_bucket          text NOT NULL,
    size_bytes             bigint NOT NULL CHECK (size_bytes >= 0),
    original_filename      text,
    received_from          text NOT NULL,           -- upload principal / API endpoint

    status                 text NOT NULL DEFAULT 'RECEIVED',
    status_vt              text NOT NULL DEFAULT 'Artefact_Status'
        CHECK (status_vt = 'Artefact_Status'),

    promoted_batch_id      uuid,
    error_detail           text,

    FOREIGN KEY (declared_document_type_vt, declared_document_type)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (source_stream_vt, source_stream)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (status_vt, status)
        REFERENCES platform_ref.universal_master (value_type, value)
);

-- Deduplication is per-tenant BY CONSTRUCTION under schema-per-tenant.
-- ARCHITECTURE.md 9 finding 1: a global fingerprint key would let tenant B
-- learn that tenant A holds a byte-identical file, and would reject B's
-- upload as a duplicate of a document B cannot see. Do not re-centralise this.
CREATE UNIQUE INDEX IF NOT EXISTS artefact_ledger_content_hash_uq
    ON {{bronze}}.artefact_ledger (content_hash);

CREATE INDEX IF NOT EXISTS artefact_ledger_status_idx
    ON {{bronze}}.artefact_ledger (status, received_at);

CREATE INDEX IF NOT EXISTS artefact_ledger_batch_idx
    ON {{bronze}}.artefact_ledger (promoted_batch_id)
    WHERE promoted_batch_id IS NOT NULL;
