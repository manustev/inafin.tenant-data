-- =============================================================================
-- Shared migration 011 — catalog for A1.01 SALES_REGISTER.
--
-- The shared half of build order step 3. Two things the typed table needs from
-- the platform catalog: the invoice classification vocabulary its
-- invoice_type column FKs to, and its entry in the code -> table map.
--
-- HAND-WRITTEN, unlike 004/005/009. table_name is populated a batch of types at
-- a time as their tables are built, so a generated seed would have to be
-- regenerated — and thereby edited — on every batch, which checksum pinning
-- forbids. registry/document_types.csv stays the source of truth and
-- test_registry_matches_reviewed_csv compares the two, so the CSV and this file
-- cannot drift apart quietly. See scripts/gen_registry_seed.py::_check_table_names.
-- =============================================================================

-- --- Invoice_Type ---------------------------------------------------------------
--
-- Category B Pattern 1, same as every other categorical value: a vocabulary in
-- universal_master reached through a CHECK-pinned _vt composite FK, never a
-- CHECK constraint listing the values. Adding a classification is then a data
-- entry rather than a migration against every tenant's table.
--
-- INFERRED, NOT taken from the customer's reference schema, which was not
-- available in this workspace. It is the first thing to reconcile when the
-- reference arrives — the column is a FK, so an unknown value fails loudly at
-- ingestion rather than being stored and believed.
--
-- These are the values an ERP SUPPLIES, which is not the same list as GSTR-1's
-- tables. B2CL and B2CS are deliberately absent: the split between them is
-- derived from invoice value and whether the supply is inter-state, and deriving
-- it at ingestion would put a statutory threshold in the parser. An ERP exports
-- 'B2C'; classifying it is Pipeline 2's job, on the same principle that keeps
-- the tax-head CHECK structural (tenant 008). The hand-written fixture
-- tests/fixtures/sales_register_typed_handwritten.csv says 'B2C' for exactly
-- this reason, and it was written against the document, not against this list.

INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    ('Invoice_Type', 'B2B',       'Registered recipient'),
    ('Invoice_Type', 'B2C',       'Unregistered recipient. The B2CL/B2CS split is derived downstream'),
    ('Invoice_Type', 'SEZWP',     'SEZ supply with payment of tax'),
    ('Invoice_Type', 'SEZWOP',    'SEZ supply without payment of tax'),
    ('Invoice_Type', 'EXPWP',     'Export with payment of IGST'),
    ('Invoice_Type', 'EXPWOP',    'Export under LUT or bond, without payment of tax'),
    ('Invoice_Type', 'DEXP',      'Deemed export'),
    ('Invoice_Type', 'NIL_RATED', 'Nil-rated supply'),
    ('Invoice_Type', 'EXEMPTED',  'Exempt supply'),
    ('Invoice_Type', 'NON_GST',   'Non-GST supply')
ON CONFLICT (value_type, value) DO NOTHING;


-- --- The code -> table map ------------------------------------------------------
--
-- Unqualified: it resolves inside each tenant's t_<slug>_silver. The line table
-- is sales_register_line by the <table_name>_line convention (shared 010).

UPDATE platform_ref.document_type
   SET table_name = 'sales_register'
 WHERE doc_type_code = 'SALES_REGISTER';

-- Asserted, not assumed. A silent no-op here would leave the registry claiming
-- the type has no table while every tenant has one — and nothing downstream
-- would notice until a lookup returned NULL. DDL is transactional, so this
-- rolls the migration back.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM platform_ref.document_type
         WHERE doc_type_code = 'SALES_REGISTER' AND table_name = 'sales_register'
    ) THEN
        RAISE EXCEPTION 'SALES_REGISTER did not receive its table_name';
    END IF;
END $$;
