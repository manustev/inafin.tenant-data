-- =============================================================================
-- Shared migration 010 — domains, and the code -> table map.
--
-- TYPED-TABLES-PLAN.md 3, and step 1 of its build order. This is the shared half
-- of the typed-table redesign: the reusable column types every typed table will
-- draw on, and the one genuinely new piece of catalog data that table-per-type
-- requires.
--
-- It creates no tenant table and retires nothing. Migrations are checksum-pinned
-- and 009 is applied, so field_contract stays exactly where it is — it simply
-- stops being read for types that acquire a table (TYPED-TABLES-PLAN.md 9).
-- =============================================================================

-- --- Domains ------------------------------------------------------------------
--
-- The point of these is that the constraint travels with the column. Under the
-- archetype design place_of_supply and invoice_type lived as untyped strings in
-- an `attributes` jsonb bag; a domain is the opposite trade — declared once,
-- enforced by the database on every write, and visible to the planner.
--
-- Not REVOKEd from PUBLIC. A domain holds no data, and every tenant role that
-- reads a column of this type needs USAGE on it. Granting that per tenant would
-- contend on the ACL row on concurrent fan-out (README invariant 8), and
-- granting it to platform_ref_reader would only re-state the default.

-- Stricter than the ^[0-9]{2}[A-Z0-9]{13}$ used by tenant migrations 002/006/007,
-- which accepts any 15 characters in the right shape and would pass '27AAAAAAAAAAAAA'.
-- This enforces the PAN embedded at positions 3-12:
--
--     27 AAACR 5055 K 1 Z 5
--     |  |     |    | | | +-- checksum          [0-9A-Z]
--     |  |     |    | | +---- registration type [0-9A-Z], not pinned -- see below
--     |  |     |    | +------ entity number     [0-9A-Z]
--     |  |     |    +-------- PAN holder initial
--     |  |     +------------- PAN sequence
--     |  +------------------- PAN name block
--     +---------------------- state code
--
-- Position 14 is deliberately NOT pinned to 'Z'. The widely-published regex pins
-- it, and doing so here would reject TDS ('D'), TCS ('C') and UIN registrations
-- — all of which legitimately appear as counterparties on a purchase register.
-- A domain that rejects valid data is worse than the loose check it replaced,
-- because the rejection happens at ingestion and the row is simply lost.
CREATE DOMAIN platform_ref.gstin AS text
    CHECK (VALUE ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$');

COMMENT ON DOMAIN platform_ref.gstin IS
    'GSTIN with the embedded PAN enforced. Position 14 is not pinned to Z: TDS, '
    'TCS and UIN registrations are valid counterparties.';

-- numeric(18, 2) matches every money column already in the schema. No sign
-- constraint: a credit note line, an ITC reversal and a debit adjustment are all
-- legitimately negative, and a NOT NULL CHECK (VALUE >= 0) here would have to be
-- dropped by the second document type that needs one.
CREATE DOMAIN platform_ref.money_inr AS numeric(18, 2);

COMMENT ON DOMAIN platform_ref.money_inr IS
    'Rupee amount, 2dp. Deliberately unsigned-agnostic — credit notes and '
    'reversals are negative.';

CREATE DOMAIN platform_ref.qty AS numeric(18, 3);

COMMENT ON DOMAIN platform_ref.qty IS
    'Quantity, 3dp. Matches transaction_line.quantity.';

CREATE DOMAIN platform_ref.tax_rate AS numeric(5, 2)
    CHECK (VALUE >= 0 AND VALUE <= 100);

COMMENT ON DOMAIN platform_ref.tax_rate IS
    'Percentage rate, 0-100. Structural only: WHICH rate is correct for an HSN '
    'is the corpus'' answer, not this column''s.';


-- --- document_type.table_name -------------------------------------------------
--
-- The registry already answers "what is this document"; this answers "where do
-- its rows land". Under the archetype design the answer was a constant, so the
-- column did not need to exist.
--
-- Unqualified, and per tenant it resolves inside t_<slug>_silver. It must not be
-- schema-qualified: the same code names a table in every tenant's schema, which
-- is the whole isolation model (TYPED-TABLES-PLAN.md 2).
--
-- HEADER TABLE ONLY. Where a type splits header from line (TYPED-TABLES-PLAN.md
-- 6), the line table is `<table_name>_line` by convention. A second column
-- naming it would be a second answer to a question the convention already
-- settles, and would let the two disagree.
--
-- NULL until the type's table is built. There are 62 STRUCTURED types and the
-- build order lands them in batches, so the column is populated by the shared
-- migration that accompanies each batch — never by editing this one.
ALTER TABLE platform_ref.document_type
    ADD COLUMN IF NOT EXISTS table_name text;

-- A plain lowercase identifier. Bounded at 50 so `<table_name>_line` and the
-- index names built from it stay inside Postgres' 63-byte NAMEDATALEN limit,
-- where over-long names are silently TRUNCATED rather than rejected — which is
-- how two tables end up sharing one index name.
ALTER TABLE platform_ref.document_type
    ADD CONSTRAINT document_type_table_name_format_ck
    CHECK (table_name IS NULL OR table_name ~ '^[a-z][a-z0-9_]{0,49}$');

-- Mirrors document_type_field_contract_scope_ck (009): a landing table for a
-- document type with no ingestion path is unreachable, and unreachable
-- configuration is where wrong assumptions survive.
--
-- Deliberately NOT narrowed to silver_storage = 'STRUCTURED'. Whether the 63
-- HYBRID types get typed tables is open (TYPED-TABLES-PLAN.md 11), and pinning
-- it here would mean dropping this constraint to answer that question.
ALTER TABLE platform_ref.document_type
    ADD CONSTRAINT document_type_table_name_scope_ck
    CHECK (table_name IS NULL OR in_scope);

-- One type per table, enforced rather than reviewed. Collapsing several types
-- onto one table with a discriminator column was considered and rejected
-- (TYPED-TABLES-PLAN.md 8); this is that decision as a constraint, so reversing
-- it requires a migration that says so out loud.
CREATE UNIQUE INDEX IF NOT EXISTS document_type_table_name_uq
    ON platform_ref.document_type (table_name)
    WHERE table_name IS NOT NULL;
