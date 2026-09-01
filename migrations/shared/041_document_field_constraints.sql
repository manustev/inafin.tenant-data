-- =============================================================================
-- Shared migration 041 — the published schema carries the database's own
-- constraints.
--
-- THE GAP THIS CLOSES, found by the ERP upload E2E suite (2026-09-01) across
-- 70 document types. `document_type_field.data_type` publishes the coarse
-- KIND a CSV cell parses as — text, date, decimal — because that is what
-- `src/silver/registers/spec.py`'s `Kind` is for. It says nothing about what
-- the COLUMN accepts, and the two are different questions:
--
--     supplier_gstin    published `text`     is platform_ref.gstin, whose
--                                            CHECK enforces the embedded PAN
--     gst_rate          published `decimal`  is numeric(6,3), bounded 0-100
--     itc_eligibility   published `text`     accepts four values
--     currency          published `text`     is character(3)
--
-- So a client could generate an export that satisfied the schema it
-- downloaded and still be refused by Postgres at ingestion. Until
-- `src/dispatch/trigger.py` learned to classify integrity errors (same date)
-- that refusal reached them as an opaque HTTP 500.
--
-- WHY COLUMNS HERE RATHER THAN NEW `Kind` MEMBERS. Adding GSTIN and
-- FIXED_WIDTH to `Kind` was the first proposal and was rejected:
-- `registers/spec.py`'s docstring already argues that re-declaring a CHECK
-- vocabulary or the GSTIN shape in Python creates a second copy of a rule the
-- database owns, free to disagree with it. Publishing a hand-maintained copy
-- one layer further out repeats that mistake somewhere nothing would catch
-- the drift. These columns are instead DERIVED from `pg_constraint` and
-- `pg_type` by `src/catalogue/field_constraints.py`, seeded by migration 042,
-- and re-derived against live DDL by
-- `tests/conformance/test_field_constraints.py` — the same gate shape
-- `test_register_specs.py::test_spec_matches_the_table` uses.
--
-- SCOPE: DERIVED TYPES ONLY, and that is honest rather than partial. A
-- DECLARED type's field list comes from a registry grammar cell and owns no
-- table, so there is no constraint to read; publishing empty constraints for
-- it would imply the database accepts anything. `document_type_schema
-- .provenance` already tells a client which kind they are looking at.
--
-- NULL EVERYWHERE MEANS "no such constraint on this column", never "not
-- looked at" — the derivation visits every published field of every DERIVED
-- type.
-- =============================================================================

ALTER TABLE platform_ref.document_type_field
    -- The schema-qualified domain, e.g. `platform_ref.gstin`. Published as a
    -- NAME as well as its parsed shape below: the name is stable and
    -- self-describing where a regex is neither, and two columns sharing a
    -- domain share it visibly.
    ADD COLUMN sql_domain text,

    -- A POSIX regex the value must match, from a `~` check on the column or
    -- on its domain. Verbatim as Postgres reports it — not re-expressed in
    -- another dialect, which would be a translation this layer cannot verify.
    ADD COLUMN pattern text,

    -- The COMPLETE accepted vocabulary, in the order the constraint declares
    -- it. An empty array is not a valid value: a column with no vocabulary
    -- carries NULL.
    ADD COLUMN allowed_values text[],

    -- INCLUSIVE bounds. A strict `> N` on an integer column is published as
    -- `min_value = N + 1`, which is exactly equivalent; a strict bound on a
    -- non-integer column has no exact inclusive form and is carried as a row
    -- in document_type_rule instead of being rounded into one here.
    ADD COLUMN min_value numeric,
    ADD COLUMN max_value numeric,

    -- character_maximum_length — the fixed-width codes the E2E finding names
    -- (character(2) for DR/CR, character(3) for a currency).
    ADD COLUMN max_length integer,

    -- numeric(p,s). Scale silently ROUNDS a value with too many decimal
    -- places; precision REJECTS one with too many digits. A client needs the
    -- second even though the first is invisible to them.
    ADD COLUMN numeric_precision integer,
    ADD COLUMN numeric_scale integer,

    ADD CONSTRAINT document_type_field_allowed_values_ck
        CHECK (allowed_values IS NULL OR cardinality(allowed_values) > 0),
    ADD CONSTRAINT document_type_field_bounds_ck
        CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value),
    ADD CONSTRAINT document_type_field_max_length_ck
        CHECK (max_length IS NULL OR max_length > 0);

COMMENT ON COLUMN platform_ref.document_type_field.sql_domain IS
    'Schema-qualified Postgres domain backing this field, or NULL. Derived '
    'from pg_type by src/catalogue/field_constraints.py — never hand-written.';

COMMENT ON COLUMN platform_ref.document_type_field.allowed_values IS
    'The complete accepted vocabulary from a CHECK, in declaration order. '
    'NULL means the column has no vocabulary, not that it has an empty one.';


-- --- Constraints that belong to no single field -------------------------------
--
-- Two kinds land here, and neither is expressible as a column above:
--
--   * genuinely multi-column business rules. sales_register_line's
--     intra-versus-inter-state rule (IGST positive implies CGST and SGST
--     zero) is real, is a rule a client trips over, and belongs to no one
--     field.
--   * any check whose shape `field_constraints.parse_check` does not
--     recognise. Publishing the expression verbatim is honest; inventing a
--     field-level approximation of it would not be.
--
-- ENVELOPE CONSTRAINTS ARE NOT PUBLISHED, at either grain. `doc_type_code =
-- 'X'`, `valid_to > valid_from` and the superseded_at/modified_at pairing are
-- real, but those columns are written by the loader and never supplied in an
-- export — a tenant can neither satisfy nor violate them. A constraint
-- reaches this table only if EVERY column it touches is a published field.
CREATE TABLE platform_ref.document_type_rule (
    doc_type_code text NOT NULL
        REFERENCES platform_ref.document_type_schema (doc_type_code)
        ON DELETE CASCADE,

    -- The Postgres constraint name. Load-bearing, not decoration: it is what
    -- `SilverConstraintViolation.constraint` reports when the rule fires
    -- (src/core/errors.py), so a client that received a 422 can look the
    -- failure up here and read what it actually violated.
    constraint_name text NOT NULL,

    -- pg_get_constraintdef's text with its type casts stripped for
    -- legibility and nothing else changed. Evidence, not prose.
    expression text NOT NULL,

    -- Every published field the rule constrains, in constraint column order.
    columns text[] NOT NULL CHECK (cardinality(columns) > 0),

    PRIMARY KEY (doc_type_code, constraint_name)
);

COMMENT ON TABLE platform_ref.document_type_rule IS
    'Constraints that are not reducible to one field''s shape — multi-column '
    'business rules, and checks whose expression shape the derivation does '
    'not parse. Derived from pg_constraint; seeded by migration 042.';


-- --- Grants -------------------------------------------------------------------
--
-- document_type_field's own grants already cover the new COLUMNS: a GRANT is
-- table-level, so app_login (037) and platform_ref_reader (040) can read them
-- without restatement. Only the new TABLE needs granting, and it gets exactly
-- the pair those two migrations established, for their reasons — 037's
-- pre-tenant onboarding case, 040's inside-a-tenant case. SELECT only.
GRANT SELECT ON platform_ref.document_type_rule TO app_login;
GRANT SELECT ON platform_ref.document_type_rule TO platform_ref_reader;
