-- =============================================================================
-- Tenant migration 035 — KMP_LIST's table content becomes a real, queryable
-- fact: one row per key managerial person.
--
-- Same shape/reasoning as `gstin_register_entry` (tenant migration 033).
-- Uniqueness keys on `name`, not `din` — a KMP entry may have no DIN at all
-- (the CFO in the real specimen is "N/A (not a director)"), so `din` cannot
-- be relied on as a natural key the way `director_list_din` relies on it.
-- Same simplification `shareholding_pattern_holder` already accepts for
-- `holder_name`.
--
-- Column names (`name`, `designation`, `din`, `membership`) match
-- KMP_LIST's `table_columns` (shared migration 055) exactly —
-- `record_fact_rows` (`src/silver/entity_master_fact.py`) inserts a row's
-- keys as column names verbatim, so this table's columns are not free to
-- diverge from that regex's named groups.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.kmp_list_member (
    fact_id             uuid PRIMARY KEY,
    record_id           uuid NOT NULL
        REFERENCES {{silver}}.entity_master_record (record_id),
    entity_id           uuid NOT NULL,

    name                text NOT NULL,
    designation         text NOT NULL,
    din                 text,
    membership          text,

    bronze_ingest_id    uuid NOT NULL,
    extraction_run_id   uuid NOT NULL,
    confidence          numeric(3, 2) NOT NULL DEFAULT 1.0
        CHECK (confidence BETWEEN 0 AND 1),

    recorded_at         timestamptz NOT NULL DEFAULT now(),
    superseded_at       timestamptz,
    supersedes_fact_id  uuid
        REFERENCES {{silver}}.kmp_list_member (fact_id),

    batch_id            uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id)
);

-- One current fact per member name — a re-ingest of the same list corrects
-- the same person's row in place rather than accumulating a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS kmp_list_member_current_uq
    ON {{silver}}.kmp_list_member (entity_id, name)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS kmp_list_member_lookup_idx
    ON {{silver}}.kmp_list_member (entity_id)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.kmp_list_member IS
    'One row per key managerial person extracted from a KMP_LIST PDF''s '
    'table (2026-09-03 session — replicates the SHAREHOLDING_PATTERN table-fact pattern).';
