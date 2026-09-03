-- =============================================================================
-- Tenant migration 034 — DIRECTOR_LIST_WITH_DIN's table content becomes a
-- real, queryable fact: one row per director.
--
-- Same shape/reasoning as `gstin_register_entry` (tenant migration 033).
-- `cessation_date`/`currently_active` stay `text`, not `date`/`boolean` —
-- the real specimen's own values ("30-Sep-2024 (resigned)", "No
-- (resigned)") carry an annotation alongside the date/flag that a stricter
-- column type would silently discard; this is a known simplification, not
-- an oversight (see `src/extraction/entity_master_types.py`).
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.director_list_din (
    fact_id             uuid PRIMARY KEY,
    record_id           uuid NOT NULL
        REFERENCES {{silver}}.entity_master_record (record_id),
    entity_id           uuid NOT NULL,

    name                text NOT NULL,
    din                 text NOT NULL,
    designation         text NOT NULL,
    appointment_date    date NOT NULL,
    cessation_date      text,
    currently_active    text NOT NULL,

    bronze_ingest_id    uuid NOT NULL,
    extraction_run_id   uuid NOT NULL,
    confidence          numeric(3, 2) NOT NULL DEFAULT 1.0
        CHECK (confidence BETWEEN 0 AND 1),

    recorded_at         timestamptz NOT NULL DEFAULT now(),
    superseded_at       timestamptz,
    supersedes_fact_id  uuid
        REFERENCES {{silver}}.director_list_din (fact_id),

    batch_id            uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id)
);

-- One current fact per DIN — a re-ingest of the same list corrects the same
-- director's row in place rather than accumulating a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS director_list_din_current_uq
    ON {{silver}}.director_list_din (entity_id, din)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS director_list_din_lookup_idx
    ON {{silver}}.director_list_din (entity_id)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.director_list_din IS
    'One row per director extracted from a DIRECTOR_LIST_WITH_DIN PDF''s '
    'table (2026-09-03 session — replicates the SHAREHOLDING_PATTERN table-fact pattern).';
