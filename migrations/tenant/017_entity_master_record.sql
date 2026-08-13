-- =============================================================================
-- Tenant migration 017 — Archetype 7: the entity/counterparty master record.
--
-- Follows migration 006's idiom (see 016's header for the shared reasoning).
-- Seven document types land here: CERTIFICATE_OF_INCORPORATION,
-- SHAREHOLDING_PATTERN, RELATED_PARTY_REGISTER, GSTIN_REGISTER,
-- DIRECTOR_LIST_WITH_DIN, FORM_DIR_12, KMP_LIST. Unlike archetype 3's
-- instrument_number, several of these documents are LISTS (a shareholding
-- table, a KMP roster) with no single external reference number of their own
-- — `reference_number` is therefore whatever a tier-1 label:value parser CAN
-- bind (an SRN, a CIN, a "financial years covered" string), which is honestly
-- a weaker key than an instrument's ARN. Several of these types will
-- legitimately extract as a named Partial rather than Extracted — see
-- src/extraction/entity_master_types.py.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.entity_master_record (
    record_id            uuid PRIMARY KEY,
    entity_id             uuid NOT NULL,

    master_type           text NOT NULL,
    master_archetype      smallint NOT NULL DEFAULT 7
        CHECK (master_archetype = 7),

    -- Whatever identifies THIS snapshot/filing of the master record — an SRN,
    -- a CIN, a "financial years covered" string. Required for the same reason
    -- entitlement_instrument.instrument_number is: without it, two ingests of
    -- the same list cannot be told apart from two genuinely different ones.
    reference_number      text NOT NULL,

    -- The date/period the facts in this record are true as of — not when we
    -- received it. A shareholding pattern "as on 31 March 2025" only answers
    -- the section 15 related-person test for that date.
    as_of_date             date NOT NULL,

    details                jsonb NOT NULL DEFAULT '{}'::jsonb,

    status                 text NOT NULL DEFAULT 'ACTIVE',
    status_vt              text NOT NULL DEFAULT 'Master_Record_Status'
        CHECK (status_vt = 'Master_Record_Status'),

    supersedes_record_id  uuid
        REFERENCES {{silver}}.entity_master_record (record_id),

    recorded_at            timestamptz NOT NULL DEFAULT now(),
    superseded_at          timestamptz,

    batch_id               uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id       uuid NOT NULL,

    FOREIGN KEY (master_type, master_archetype)
        REFERENCES platform_ref.document_type (doc_type_code, archetype),
    FOREIGN KEY (status_vt, status)
        REFERENCES platform_ref.universal_master (value_type, value)
);

CREATE INDEX IF NOT EXISTS entity_master_record_lookup_idx
    ON {{silver}}.entity_master_record (entity_id, master_type, as_of_date)
    WHERE superseded_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS entity_master_record_current_uq
    ON {{silver}}.entity_master_record (entity_id, master_type, reference_number)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.entity_master_record IS
    'Archetype 7 (streamed-marinating-gray.md). CIN, directors/DIN, KMP, '
    'shareholding, related parties, GSTIN register — one row per snapshot of '
    'a master fact about the entity itself, not a transaction.';

CREATE OR REPLACE VIEW {{silver}}.v1_entity_master_record AS
SELECT
    record_id,
    entity_id,
    master_type,
    reference_number,
    as_of_date,
    details,
    status,
    supersedes_record_id,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id
FROM {{silver}}.entity_master_record;
