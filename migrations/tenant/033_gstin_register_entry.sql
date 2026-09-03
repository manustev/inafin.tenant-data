-- =============================================================================
-- Tenant migration 033 — GSTIN_REGISTER's table content becomes a real,
-- queryable fact: one row per GSTIN registered under the entity's PAN.
--
-- Replicates `shareholding_pattern_holder` (tenant migration 032)'s shape —
-- see that migration's header for the full "why a typed table, why no human
-- sign-off" reasoning, unchanged here. The one structural difference: this
-- table has no bitemporal validity window of its own — a GSTIN registration
-- is a single, current fact (state/status as of the document), not two
-- periods per row the way a %-holding is — so `valid_from`/`valid_to` are
-- dropped in favour of `EntityMasterExtractor`'s existing snapshot-level
-- `record_id` chain doing that work, same as every other envelope column.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.gstin_register_entry (
    fact_id             uuid PRIMARY KEY,
    record_id           uuid NOT NULL
        REFERENCES {{silver}}.entity_master_record (record_id),
    entity_id           uuid NOT NULL,

    gstin               text NOT NULL,
    state               text NOT NULL,
    registration_type   text NOT NULL,
    effective_date      date NOT NULL,
    status              text NOT NULL,
    principal_place     text,

    bronze_ingest_id    uuid NOT NULL,
    extraction_run_id   uuid NOT NULL,
    confidence          numeric(3, 2) NOT NULL DEFAULT 1.0
        CHECK (confidence BETWEEN 0 AND 1),

    recorded_at         timestamptz NOT NULL DEFAULT now(),
    superseded_at       timestamptz,
    supersedes_fact_id  uuid
        REFERENCES {{silver}}.gstin_register_entry (fact_id),

    batch_id            uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id)
);

-- One current fact per GSTIN — a re-ingest of the same register corrects the
-- same GSTIN's row in place rather than accumulating a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS gstin_register_entry_current_uq
    ON {{silver}}.gstin_register_entry (entity_id, gstin)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS gstin_register_entry_lookup_idx
    ON {{silver}}.gstin_register_entry (entity_id)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.gstin_register_entry IS
    'One row per GSTIN extracted from a GSTIN_REGISTER PDF''s table '
    '(2026-09-03 session — replicates the SHAREHOLDING_PATTERN table-fact pattern).';
