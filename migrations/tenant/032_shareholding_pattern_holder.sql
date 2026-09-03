-- =============================================================================
-- Tenant migration 032 — SHAREHOLDING_PATTERN's table content becomes a real,
-- queryable fact, not a discarded PDF table.
--
-- THE GAP. `entity_master_record` (tenant 017) captures only two header
-- facts per document — `reference_number`, `as_of_date` — and its `details
-- jsonb` column, meant to carry "whatever varies per master type," has sat
-- empty since the table was created: nothing has ever populated it. For
-- SHAREHOLDING_PATTERN specifically that means the actual shareholder table
-- — who holds what %, across which financial year, pledged or not — was
-- never captured at all. A client cannot ask "who held >=25% of entity X on
-- a given date" (the Section 15 CGST related-person test this document
-- exists for) without re-parsing the raw PDF by hand.
--
-- WHY A TYPED TABLE, NOT `details jsonb`. Already decided this session,
-- twice over: `TYPED-TABLES-PLAN.md` overrules the archetype/jsonb-bag
-- design for STRUCTURED types, and `field_constraints.py`'s whole existence
-- this session is proof a CHECK/domain/bound only means anything on a real
-- column. `pct_holding numeric(5,2) CHECK (0-100)` here is what makes
-- ">=25%" a plain SQL predicate for v2, not a JSON path expression.
--
-- WHY ONE ROW PER (HOLDER, VALIDITY WINDOW), NOT ONE ROW PER HOLDER. The
-- specimen's two %-holding columns (FY24-25, FY23-24) are two DIFFERENT
-- facts with two different validity windows, not two columns of one fact —
-- collapsing them into one row with two %-columns would make "who held
-- >=25% on 12-Aug-2023" an application-level date computation instead of a
-- SQL predicate on `valid_from`/`valid_to`. Six shareholders x two periods
-- is twelve rows, not six.
--
-- SUPERSEDE: full-snapshot replace, matching `entity_master_record` itself.
-- A new PDF is a new whole snapshot; every fact row FK'd to the PRIOR
-- `record_id` is closed and the new PDF's full row set is inserted fresh
-- under the NEW `record_id`, linked via `supersedes_fact_id` where the same
-- (holder_name, valid_from) key existed before. No per-row diffing.
--
-- PROVENANCE, NO GATE. `bronze_ingest_id`/`extraction_run_id`/`confidence`
-- record HOW a fact was extracted (today: regex, confidence always 1.0) so
-- a future extraction tier can be told apart from this one without a schema
-- change — informational, not a promotion gate. This session explicitly
-- does not want a human sign-off step; a row that matches its declared
-- shape promotes straight to Silver, same as every other archetype here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.shareholding_pattern_holder (
    fact_id             uuid PRIMARY KEY,
    record_id           uuid NOT NULL
        REFERENCES {{silver}}.entity_master_record (record_id),
    entity_id           uuid NOT NULL,

    holder_name         text NOT NULL,
    shares              numeric(18, 0),
    pct_holding         numeric(5, 2) NOT NULL CHECK (pct_holding BETWEEN 0 AND 100),
    pledged             boolean NOT NULL DEFAULT FALSE,

    -- The window this %-holding was true for — NOT when we received the
    -- document. See this file's header on why this is two rows per holder,
    -- not two columns on one.
    valid_from          date NOT NULL,
    valid_to            date NOT NULL CHECK (valid_to > valid_from),

    bronze_ingest_id    uuid NOT NULL,
    extraction_run_id   uuid NOT NULL,
    confidence          numeric(3, 2) NOT NULL DEFAULT 1.0
        CHECK (confidence BETWEEN 0 AND 1),

    recorded_at         timestamptz NOT NULL DEFAULT now(),
    superseded_at       timestamptz,
    supersedes_fact_id  uuid
        REFERENCES {{silver}}.shareholding_pattern_holder (fact_id),

    batch_id            uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id)
);

-- One current fact per (holder, period) — a re-ingest of the SAME PDF's SAME
-- holder/period combination corrects in place (supersedes) rather than
-- accumulating a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS shareholding_pattern_holder_current_uq
    ON {{silver}}.shareholding_pattern_holder (entity_id, holder_name, valid_from)
    WHERE superseded_at IS NULL;

-- The query this whole table exists for: "who held >=X% of this entity as
-- of date D" is `valid_from <= D AND valid_to >= D`, and this index serves
-- exactly that range predicate.
CREATE INDEX IF NOT EXISTS shareholding_pattern_holder_lookup_idx
    ON {{silver}}.shareholding_pattern_holder (entity_id, valid_from, valid_to)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.shareholding_pattern_holder IS
    'One row per (shareholder, financial-year validity window) extracted '
    'from a SHAREHOLDING_PATTERN PDF''s table — the proof-of-concept typed '
    'fact table for archetype 7''s table-shaped types (2026-09-02 session).';
