-- =============================================================================
-- Shared migration 012 — reconcile the catalog against the customer's reference
-- schema (`reference/inafin_a1_schema.sql`, received 2026-08-07).
--
-- Shared 010 and 011 were written WITHOUT the reference — it was described in an
-- earlier session but never landed as a file. Two things in them were guesses,
-- and both were wrong. This corrects them. 010 and 011 are applied and pinned,
-- so this is a new migration, not an edit.
--
-- What is NOT changed, and deliberately:
--
--   * platform_ref.gstin does not pin position 14 to 'Z'. meta.gstin does
--     (`[1-9A-Z]Z[0-9A-Z]`). Confirmed 2026-08-06: a government TDS deductor
--     ('D') is a legitimate customer on a sales register, and dropping those
--     rows at ingestion is worse than accepting a slightly wider alphabet.
--   * money_inr and qty already match the reference exactly.
-- =============================================================================

-- --- 1. tax_rate: numeric(5,2) -> numeric(6,3) ---------------------------------
--
-- The reference uses numeric(6,3). Ours was numeric(5,2), which silently ROUNDS
-- a three-decimal rate rather than rejecting it — the worst failure mode
-- available, because the row loads and the number is wrong.
--
-- A domain's underlying type cannot be altered in place, and tenant tables
-- already depend on this one. Shared migrations run before tenant migrations, so
-- the dependency cannot be removed first by a tenant migration. The columns are
-- therefore detached, the domain is rebuilt, and the columns are reattached —
-- all inside this migration's transaction, so there is no window in which a
-- tenant column is unconstrained.
--
-- This is the general pattern for evolving a shared domain that tenant tables
-- use. It will be needed again.

CREATE TEMP TABLE _tax_rate_cols ON COMMIT DROP AS
SELECT n.nspname AS sch, c.relname AS tbl, a.attname AS col
  FROM pg_attribute a
  JOIN pg_class     c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_type      t ON t.oid = a.atttypid
 WHERE t.typname = 'tax_rate'
   AND t.typnamespace = 'platform_ref'::regnamespace
   AND c.relkind = 'r'
   AND NOT a.attisdropped;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT * FROM _tax_rate_cols LOOP
        EXECUTE format('ALTER TABLE %I.%I ALTER COLUMN %I TYPE numeric(6,3)',
                       r.sch, r.tbl, r.col);
    END LOOP;
END $$;

DROP DOMAIN platform_ref.tax_rate;

CREATE DOMAIN platform_ref.tax_rate AS numeric(6, 3)
    CHECK (VALUE >= 0 AND VALUE <= 100);

COMMENT ON DOMAIN platform_ref.tax_rate IS
    'Percentage rate, 0-100, 3dp — matches meta.tax_rate in the reference '
    'schema. Structural only: WHICH rate is correct for an HSN is the corpus'' '
    'answer, not this column''s.';

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT * FROM _tax_rate_cols LOOP
        EXECUTE format('ALTER TABLE %I.%I ALTER COLUMN %I TYPE platform_ref.tax_rate',
                       r.sch, r.tbl, r.col);
    END LOOP;
END $$;


-- --- 2. Invoice_Type: adopt the reference's classification ----------------------
--
-- Shared 011 argued that an ERP supplies 'B2C' and that splitting it into
-- B2CL/B2CS is a derivation belonging downstream, because the split turns on
-- invoice value and inter-state status. The reference settles it the other way:
--
--     check (invoice_type in ('B2B','B2CL','B2CS','EXP','SEZWP','SEZWOP','DE'))
--
-- The customer wants the classification at ingestion. 011's EXPWP/EXPWOP,
-- DEXP, NIL_RATED, EXEMPTED and NON_GST were inventions and are removed —
-- exports are one value ('EXP') and the with/without-payment distinction is
-- carried elsewhere.
--
-- DELETE, not just INSERT. Leaving 'B2C' resolvable would let an ERP keep
-- sending the unsplit value and pass the FK, which is precisely the outcome the
-- reference rules out. Safe because no row references these: sales_register is
-- rebuilt by tenant migration 009 in this same release.

DELETE FROM platform_ref.universal_master
 WHERE value_type = 'Invoice_Type'
   AND value NOT IN ('B2B', 'B2CL', 'B2CS', 'EXP', 'SEZWP', 'SEZWOP', 'DE');

INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    ('Invoice_Type', 'B2B',    'Registered recipient'),
    ('Invoice_Type', 'B2CL',   'Unregistered, inter-state, above threshold'),
    ('Invoice_Type', 'B2CS',   'Unregistered, consolidated'),
    ('Invoice_Type', 'EXP',    'Export'),
    ('Invoice_Type', 'SEZWP',  'SEZ supply with payment of tax'),
    ('Invoice_Type', 'SEZWOP', 'SEZ supply without payment of tax'),
    ('Invoice_Type', 'DE',     'Deemed export')
ON CONFLICT (value_type, value) DO NOTHING;


-- --- 3. Vocabularies the reconciled sales register needs ------------------------
--
-- supply_type is free text in the reference (`supply_type text`, no CHECK). It
-- is given a vocabulary here for the same reason every other categorical column
-- has one: an uncontrolled string column accumulates 'Goods', 'GOODS', 'G' and
-- 'goods ' and no report can group on it. If the reference later pins a
-- different list, this is a data change, not a migration against 24 tables.

INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    ('Supply_Type', 'GOODS',    'Supply of goods'),
    ('Supply_Type', 'SERVICE',  'Supply of services'),
    ('Supply_Type', 'COMPOSITE', 'Composite supply — principal supply determines treatment'),
    ('Supply_Type', 'MIXED',    'Mixed supply — highest rate applies')
ON CONFLICT (value_type, value) DO NOTHING;
