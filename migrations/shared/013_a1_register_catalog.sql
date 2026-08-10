-- =============================================================================
-- Shared migration 013 — table_name for the remaining Group A1 registers.
--
-- The catalog half of tenant migration 010. Hand-written for the reason shared
-- 011 gives: table_name is populated a batch of types at a time, so a generated
-- seed would have to be regenerated — and thereby edited — on every batch, which
-- checksum pinning forbids. registry/document_types.csv stays the source of
-- truth and test_registry_matches_reviewed_csv compares the two.
--
-- Names are the reference schema's table names, so a column question can be
-- answered by opening reference/inafin_a1_schema.sql at the matching table.
--
-- ONE CONSTRAINT IS RELAXED HERE, deliberately. Shared 010 made table_name
-- UNIQUE, encoding TYPED-TABLES-PLAN.md 8's rule that one registry type gets one
-- table. The reference collapses the marketplace reports into a single
-- ingest.platform_settlement_report behind a report_type discriminator, and that
-- was confirmed over §8 on 2026-08-07. Seven registry codes (D1.01-D1.07)
-- therefore share one table and the unique index cannot stand.
--
-- It becomes a plain index, and the rule it encoded becomes a review question
-- rather than a constraint. That is a real loss, so it is stated rather than
-- quietly dropped: collapsing types onto one table is allowed ONLY where the
-- shape is provably identical and the types always co-occur. Marketplace reports
-- qualify — a seller with one generally has several, and each operator's field
-- mapping is configuration. Nothing else in the 125 does, and
-- scripts/gen_registry_seed.py::SHARED_TABLES is now the allowlist that says so.
-- =============================================================================

DROP INDEX IF EXISTS platform_ref.document_type_table_name_uq;

CREATE INDEX IF NOT EXISTS document_type_table_name_idx
    ON platform_ref.document_type (table_name)
    WHERE table_name IS NOT NULL;

UPDATE platform_ref.document_type d
   SET table_name = v.tbl
  FROM (VALUES
    ('CREDIT_DEBIT_NOTE_REGISTER', 'credit_debit_note_register'),
    ('PURCHASE_REGISTER', 'purchase_register'),
    ('ADVANCE_RECEIPT_REGISTER', 'advance_receipt_register'),
    ('UNBILLED_REVENUE_SCHEDULE', 'unbilled_revenue_schedule'),
    ('JOBWORK_DISPATCH_REGISTER', 'job_work_dispatch_register'),
    ('CONTINUOUS_SUPPLY_CONTRACT_REGISTER', 'running_account_contract_register'),
    ('AUDITED_PROFIT_AND_LOSS', 'audited_pl_account'),
    ('TRIAL_BALANCE', 'trial_balance'),
    ('BALANCE_SHEET', 'balance_sheet'),
    ('FIXED_ASSET_REGISTER', 'fixed_asset_register'),
    ('CREDITOR_AGEING_REPORT', 'creditor_ageing_report'),
    ('SUPPLIER_PAYMENT_REGISTER', 'payment_register'),
    ('BANK_STATEMENT_OUTWARD', 'bank_statement_outward'),
    ('BANK_STATEMENT_INWARD_FX', 'bank_statement_inward_forex'),
    ('STOCK_REGISTER', 'stock_inventory_register'),
    ('SKU_MASTER', 'product_sku_master'),
    ('INTER_GSTIN_TRANSACTION_REGISTER', 'inter_gstin_transaction_register'),
    ('COST_ALLOCATION_REGISTER', 'cost_allocation_register'),
    ('AMAZON_MTR', 'platform_settlement_report'),
    ('FLIPKART_GTR', 'platform_settlement_report'),
    ('MARKETPLACE_SETTLEMENT_SSR', 'platform_settlement_report'),
    ('MARKETPLACE_DISBURSEMENT_DRR', 'platform_settlement_report'),
    ('MARKETPLACE_RETURNS_VRET', 'platform_settlement_report'),
    ('FOOD_DELIVERY_PARTNER_REPORT', 'platform_settlement_report'),
    ('OTHER_MARKETPLACE_SETTLEMENT', 'platform_settlement_report'),
    ('ITC_REVERSAL_REGISTER', 'itc_reversal_register'),
    ('RCM_REGISTER', 'rcm_register'),
    ('FOREIGN_CURRENCY_PAYMENT_REGISTER', 'foreign_currency_payment_register'),
    ('HO_COMMON_INPUT_SERVICE_INVOICE', 'common_input_service_invoice')
  ) AS v(code, tbl)
 WHERE d.doc_type_code = v.code;

-- Asserted, not assumed. A silent no-op would leave the registry claiming these
-- types have no table while every tenant has one, and nothing downstream would
-- notice until a lookup returned NULL.
DO $$
DECLARE v_missing int;
BEGIN
    SELECT count(*) INTO v_missing FROM platform_ref.document_type
     WHERE doc_type_code IN ('CREDIT_DEBIT_NOTE_REGISTER', 'PURCHASE_REGISTER', 'ADVANCE_RECEIPT_REGISTER', 'UNBILLED_REVENUE_SCHEDULE', 'JOBWORK_DISPATCH_REGISTER', 'CONTINUOUS_SUPPLY_CONTRACT_REGISTER', 'AUDITED_PROFIT_AND_LOSS', 'TRIAL_BALANCE', 'BALANCE_SHEET', 'FIXED_ASSET_REGISTER', 'CREDITOR_AGEING_REPORT', 'SUPPLIER_PAYMENT_REGISTER', 'BANK_STATEMENT_OUTWARD', 'BANK_STATEMENT_INWARD_FX', 'STOCK_REGISTER', 'SKU_MASTER', 'INTER_GSTIN_TRANSACTION_REGISTER', 'COST_ALLOCATION_REGISTER', 'AMAZON_MTR', 'FLIPKART_GTR', 'MARKETPLACE_SETTLEMENT_SSR', 'MARKETPLACE_DISBURSEMENT_DRR', 'MARKETPLACE_RETURNS_VRET', 'FOOD_DELIVERY_PARTNER_REPORT', 'OTHER_MARKETPLACE_SETTLEMENT', 'ITC_REVERSAL_REGISTER', 'RCM_REGISTER', 'FOREIGN_CURRENCY_PAYMENT_REGISTER', 'HO_COMMON_INPUT_SERVICE_INVOICE')
       AND table_name IS NULL;
    IF v_missing > 0 THEN
        RAISE EXCEPTION '% A1 type(s) did not receive a table_name', v_missing;
    END IF;
END $$;
