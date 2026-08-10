-- =============================================================================
-- Tenant migration 012 — row-level rejection for the register loader path.
--
-- Customer-facing motivation: "did my upload succeed" cannot be answered file
-- by file once a file is accepted partially. src/silver/registers/loader.py
-- moves from file-level accept/quarantine-whole to row-level accept/reject —
-- valid rows promote into the batch, invalid rows are recorded here instead of
-- failing the other 499 rows in a 500-row file. This is the structured record
-- that recommendation replaces: quarantined_artefact's free-text `reason`
-- (capped at 20 messages) with one row per rejection, so a report can group by
-- error_code / column and give an exact row count, not a truncated string.
--
-- TWO TABLES, TWO FAILURE MODES, NOT ONE GENERALISED. quarantined_artefact
-- (tenant 005) stays for FILE-level failures — bad encoding, a missing
-- required column, zero data rows — where no batch is created because no row
-- could be identified at all. rejected_row is for ROW-level failures inside an
-- otherwise-loadable file, where a batch IS created and most rows succeeded.
-- Collapsing these into one table would force a nullable batch_id and lose the
-- FK to ingest_batch that makes "show me this batch's rejections" a join
-- instead of a second lookup by ingest_id.
--
-- Scope: the register loader path only (src/silver/registers/loader.py). The
-- archetype-1 path (src/silver/promote.py, the 10 types still on it after
-- tenant 011) is unchanged and stays file-level — that is a separate decision,
-- not made here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.rejected_row (
    id               bigint generated always as identity primary key,

    -- The batch the accepted rows around this rejection landed in. NOT a
    -- lookup back to quarantined_artefact — a row rejection means the FILE
    -- succeeded (a batch exists), which is exactly the case
    -- quarantined_artefact does not cover.
    batch_id         uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    doc_type_code    text NOT NULL,
    bronze_ingest_id uuid NOT NULL,

    -- 1-based line within the source file (header excluded for CSV; the line
    -- number itself for NDJSON), so a customer report can point at the exact
    -- row without re-parsing the artefact.
    source_line      integer NOT NULL,
    -- The single column primarily at fault, when the rejection names one.
    -- Nullable: a malformed NDJSON line or "not a JSON object" has no column.
    column_name      text,
    message          text NOT NULL,

    rejected_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rejected_row_batch_idx ON {{silver}}.rejected_row (batch_id);

COMMENT ON TABLE {{silver}}.rejected_row IS
    'Rows dropped by the register loader from an otherwise-accepted file. '
    'One row per rejection: source_line, the column at fault where known, '
    'and why. The file''s accepted rows are in the batch this FKs to.';
