-- =============================================================================
-- Tenant migration 019 — Archetype 8: the narrative contract.
--
-- Follows migration 006's idiom (see 016's header), with one deliberate
-- departure: EVERY business column here is nullable. ARCHITECTURE.md 6 calls
-- this archetype "Hybrid (blob + extracted terms)" — for the 11 sampled types
-- (MoA/AoA, JV agreement, board resolution, director service agreement,
-- cost-sharing/foreign-customer/overseas-vendor/job-work/long-duration/TP/
-- security-agency contracts) the specimen PDFs are prose, not `Label: Value`
-- (streamed-marinating-gray.md, confirmed by reading three of them directly).
-- A tier-1 parser genuinely cannot bind a reference number or an effective
-- date from a sentence like "entered into on 1 April 2023, effective from
-- Financial Year 2023-24" the way it can from a line that says
-- "LUT ARN: AD2704240123456". Making any field required here would make
-- EVERY document in this batch quarantine, which would be dishonest about
-- what the archetype's own design says: the MinIO Silver copy IS the primary
-- record for this archetype, `key_terms` is best-effort on top of it. A row
-- with an empty `key_terms` and a real `bronze_ingest_id` is the correct,
-- successful outcome for a contract tier-1 could not read into structured
-- terms — not a failure to paper over.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.narrative_contract (
    contract_id            uuid PRIMARY KEY,
    entity_id                uuid NOT NULL,

    contract_type            text NOT NULL,
    contract_archetype       smallint NOT NULL DEFAULT 8
        CHECK (contract_archetype = 8),

    -- All nullable — see header. A tier-1 label:value parser binds these only
    -- where a specimen happens to state them as a labelled line; most do not.
    counterparty_name         text,
    effective_date             date,
    term_end_date               date,

    key_terms                   jsonb NOT NULL DEFAULT '{}'::jsonb,

    status                      text NOT NULL DEFAULT 'ACTIVE',
    status_vt                   text NOT NULL DEFAULT 'Contract_Status'
        CHECK (status_vt = 'Contract_Status'),

    supersedes_contract_id     uuid
        REFERENCES {{silver}}.narrative_contract (contract_id),

    recorded_at                  timestamptz NOT NULL DEFAULT now(),
    superseded_at                timestamptz,

    batch_id                     uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id             uuid NOT NULL,

    FOREIGN KEY (contract_type, contract_archetype)
        REFERENCES platform_ref.document_type (doc_type_code, archetype),
    FOREIGN KEY (status_vt, status)
        REFERENCES platform_ref.universal_master (value_type, value)
);

CREATE INDEX IF NOT EXISTS narrative_contract_lookup_idx
    ON {{silver}}.narrative_contract (entity_id, contract_type)
    WHERE superseded_at IS NULL;

-- No document-carried reference number exists to key on (see header) — the
-- CONTENT strategy again, keyed on the artefact itself, one row per PDF.
CREATE UNIQUE INDEX IF NOT EXISTS narrative_contract_current_uq
    ON {{silver}}.narrative_contract (entity_id, contract_type, bronze_ingest_id)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.narrative_contract IS
    'Archetype 8 (streamed-marinating-gray.md). Hybrid blob+terms — the MinIO '
    'Silver copy is the primary record; key_terms is best-effort and legitimately '
    'empty for prose contracts a tier-1 label:value parser cannot read into '
    'structured terms.';

CREATE OR REPLACE VIEW {{silver}}.v1_narrative_contract AS
SELECT
    contract_id,
    entity_id,
    contract_type,
    counterparty_name,
    effective_date,
    term_end_date,
    key_terms,
    status,
    supersedes_contract_id,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id
FROM {{silver}}.narrative_contract;
