-- =============================================================================
-- Tenant migration 018 — Archetype 4: the financial statement extract.
--
-- Follows migration 006's idiom (see 016's header). One document type in this
-- batch: A2.05 RELATED_PARTY_DISCLOSURE_IND_AS_24. `headline_figures` is
-- deliberately sparse from tier-1 deterministic parsing — full financial
-- statement table parsing is real future work (LLM or table-extraction
-- tier), named here as an accepted limitation, not deferred as a TODO
-- (streamed-marinating-gray.md).
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.financial_statement_extract (
    extract_id            uuid PRIMARY KEY,
    entity_id              uuid NOT NULL,

    statement_type         text NOT NULL,
    statement_archetype    smallint NOT NULL DEFAULT 4
        CHECK (statement_archetype = 4),

    -- The one label tier-1 reliably binds across this archetype's specimen —
    -- WHO verified the figures. Required for the same reason every other
    -- archetype table here requires its one anchor field.
    auditor                 text NOT NULL,

    -- "2024-25" style string, not a date — a financial statement covers a
    -- year, not a point. Nullable: not every specimen states it as a clean
    -- label:value line (see src/extraction/financial_statement_types.py).
    financial_year          text,
    filed_date               date,

    headline_figures         jsonb NOT NULL DEFAULT '{}'::jsonb,

    status                   text NOT NULL DEFAULT 'AUDITED',
    status_vt                text NOT NULL DEFAULT 'Statement_Status'
        CHECK (status_vt = 'Statement_Status'),

    supersedes_extract_id     uuid
        REFERENCES {{silver}}.financial_statement_extract (extract_id),

    recorded_at               timestamptz NOT NULL DEFAULT now(),
    superseded_at             timestamptz,

    batch_id                  uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id          uuid NOT NULL,

    FOREIGN KEY (statement_type, statement_archetype)
        REFERENCES platform_ref.document_type (doc_type_code, archetype),
    FOREIGN KEY (status_vt, status)
        REFERENCES platform_ref.universal_master (value_type, value)
);

CREATE INDEX IF NOT EXISTS financial_statement_extract_lookup_idx
    ON {{silver}}.financial_statement_extract (entity_id, statement_type)
    WHERE superseded_at IS NULL;

-- One current extract per (entity, type, bronze artefact) — there is no
-- external reference number on a financial statement extract the way an
-- instrument has an ARN, so the natural key is the artefact itself: the
-- resubmission guard is "the same PDF twice", not "the same filing number
-- twice". This is the CONTENT key strategy src/silver/registers/spec.py
-- documents for exactly this situation (no document-carried identifier).
CREATE UNIQUE INDEX IF NOT EXISTS financial_statement_extract_current_uq
    ON {{silver}}.financial_statement_extract (entity_id, statement_type, bronze_ingest_id)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.financial_statement_extract IS
    'Archetype 4 (streamed-marinating-gray.md). One row per audited-statement '
    'extract. headline_figures is intentionally sparse from tier-1 '
    'deterministic parsing.';

CREATE OR REPLACE VIEW {{silver}}.v1_financial_statement_extract AS
SELECT
    extract_id,
    entity_id,
    statement_type,
    auditor,
    financial_year,
    filed_date,
    headline_figures,
    status,
    supersedes_extract_id,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id
FROM {{silver}}.financial_statement_extract;
