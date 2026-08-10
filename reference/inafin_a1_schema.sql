-- =====================================================================
-- INAFIN GST Reconciliation Platform
-- Group A1 (Client-Provided Transaction Data) — Postgres schema
-- Covers registers A1.01 .. A1.24 from the Source Document Register
--
-- Layout:
--   meta   = catalog / reference data (the "register as data")
--   ingest = typed landing tables for ERP-provided data, one per register
--
-- Conventions:
--   * All money is NUMERIC (never float) to avoid paise-level recon noise.
--   * GSTIN validated by format via a domain.
--   * tax_period = first day of the tax-period month (transaction tables).
--   * FY-level financial statements (A1.08/09/10) use fy ('2024-25') instead.
--   * Every table carries an audit + lineage envelope and an audit trigger.
-- =====================================================================

begin;

create schema if not exists meta;
create schema if not exists ingest;

-- ---------------------------------------------------------------------
-- Reusable domains
-- ---------------------------------------------------------------------
drop domain if exists meta.gstin cascade;
create domain meta.gstin as char(15)
  check (value ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$');

drop domain if exists meta.money_inr cascade;
create domain meta.money_inr as numeric(18,2);

drop domain if exists meta.qty cascade;
create domain meta.qty as numeric(18,3);

drop domain if exists meta.tax_rate cascade;
create domain meta.tax_rate as numeric(6,3)
  check (value >= 0 and value <= 100);

-- ---------------------------------------------------------------------
-- Audit trigger: fills created_* on INSERT, protects them and stamps
-- modified_* on UPDATE. App identity is read from a session GUC:
--     SET app.current_user = 'jdoe';   -- set once per connection/txn
-- Falls back to session_user when the GUC is not set.
-- ---------------------------------------------------------------------
create or replace function meta.tg_audit()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    new.created_at := coalesce(new.created_at, now());
    new.created_by := coalesce(new.created_by,
                               current_setting('app.current_user', true),
                               session_user);
  elsif tg_op = 'UPDATE' then
    new.created_at := old.created_at;   -- immutable
    new.created_by := old.created_by;   -- immutable
    new.modified_at := now();
    new.modified_by := coalesce(current_setting('app.current_user', true),
                                session_user);
  end if;
  return new;
end $$;

-- =====================================================================
-- META CATALOG
-- =====================================================================

-- Reconciliation sections A1..A10 (output tax) and B1..B8 (ITC).
-- NOTE: these are recon SECTIONS, distinct from document GROUP codes.
-- Section titles below are indicative (derived from the register text)
-- and should be confirmed against Module 1.
create table meta.recon_section (
  code        text primary key,   -- 'A1'..'A10','B1'..'B8'
  tax_side    text not null check (tax_side in ('OUTPUT','ITC')),
  title       text not null
);

insert into meta.recon_section (code, tax_side, title) values
  ('A1','OUTPUT','Outward supply / sales reconciliation'),
  ('A2','OUTPUT','Turnover bridge (GSTR-9C Table 5)'),
  ('A3','OUTPUT','Rate, exemption and disposal on outward side'),
  ('A4','OUTPUT','Advances received'),
  ('A5','OUTPUT','Time of supply / unbilled / job work / continuous supply'),
  ('A6','OUTPUT','Related party and director RCM'),
  ('A7','OUTPUT','Inter-GSTIN supplies and cross-charge'),
  ('A8','OUTPUT','Exports and zero-rated supplies'),
  ('A9','OUTPUT','Marketplace / e-commerce'),
  ('A10','OUTPUT','Exemptions and targeted notifications'),
  ('B1','ITC','GSTR-2B ITC availability and returns'),
  ('B2','ITC','180-day payment rule'),
  ('B3','ITC','Section 17(5) / write-off reversals'),
  ('B4','ITC','Rule 42/43 common-credit pool'),
  ('B5','ITC','Reverse charge / import of services'),
  ('B6','ITC','ISD distribution'),
  ('B7','ITC','Capital goods (Rule 43/44)'),
  ('B8','ITC','Credit notes and amendments');

-- Document-type registry (the "enum master"). One row per A1 register.
create table meta.document_type (
  code         text primary key,                 -- 'A1.01'
  table_name   text not null unique,             -- 'sales_register'
  display_name text not null,
  source       text not null,                    -- 'Client ERP'
  obligation   text not null check (obligation in ('MANDATORY','CONDITIONAL')),
  mode         text not null check (mode in ('FORENSIC','OPERATIONAL','BOTH')),
  is_financial_statement boolean not null default false,  -- FY-level, not txn
  notes        text
);

insert into meta.document_type
  (code, table_name, display_name, source, obligation, mode, is_financial_statement, notes) values
  ('A1.01','sales_register','Sales register (outward supply, invoice-level)','Client ERP','MANDATORY','BOTH',false,'Line-item level mandatory. Primary source for all output tax sections. Formats per ADR-056.'),
  ('A1.02','credit_debit_note_register','Credit / debit note register','Client ERP','MANDATORY','BOTH',false,'One register for both CN and DN (note_type discriminator). Must carry original invoice number; unlinked CNs are an immediate finding.'),
  ('A1.03','purchase_register','Purchase register (inward supply, invoice-level)','Client ERP','MANDATORY','BOTH',false,'GL code and cost centre mandatory for Rule 42 pool segregation.'),
  ('A1.04','advance_receipt_register','Advance receipt register','Client ERP','CONDITIONAL','BOTH',false,'Required for all service taxpayers. Goods advance tracking post Notification 66/2017.'),
  ('A1.05','unbilled_revenue_schedule','Unbilled revenue schedule','Client Books/ERP','CONDITIONAL','BOTH',false,'date_of_service_completion is the critical field. Without it the 30-day rule is flag-only.'),
  ('A1.06','job_work_dispatch_register','Job work dispatch register','Client ERP','CONDITIONAL','BOTH',false,'Dispatch date triggers the 1-year/3-year statutory return deadline.'),
  ('A1.07','running_account_contract_register','Running account / continuous supply contract register','Client ERP/Contracts','CONDITIONAL','BOTH',false,'Section 12(4) continuous supply timing check.'),
  ('A1.08','audited_pl_account','Audited Profit and Loss Account','Client Books','MANDATORY','BOTH',true,'Starting point for GSTR-9C Table 5 turnover bridge. FY-level.'),
  ('A1.09','trial_balance','Trial balance (GL-level detail)','Client Books','MANDATORY','BOTH',true,'Maps each GL to GST treatment. Required for Rule 42 pool segregation. FY-level.'),
  ('A1.10','balance_sheet','Balance sheet (full year)','Client Books','MANDATORY','BOTH',true,'Advances, unbilled/deferred revenue, creditors, capital goods schedule. FY-level.'),
  ('A1.11','fixed_asset_register','Fixed asset register (asset-level)','Client Books','MANDATORY','BOTH',false,'Rule 44 disposal reversal cannot be computed from aggregate totals.'),
  ('A1.12','creditor_ageing_report','Creditor ageing report','Client Books','MANDATORY','BOTH',false,'Primary input for 180-day payment rule. Invoice date and status per invoice required.'),
  ('A1.13','payment_register','Payment register (supplier payments linked to invoice)','Client Books/Bank','MANDATORY','BOTH',false,'Bank transfer date is the payment date, not cheque issue date. Cross-verify with bank statement.'),
  ('A1.14','bank_statement_outward','Bank statements (outward payments to suppliers)','Client Bank','MANDATORY','BOTH',false,'Authoritative record of payment dates for the 180-day rule.'),
  ('A1.15','bank_statement_inward_forex','Bank statements (inward foreign currency receipts)','Client Bank','CONDITIONAL','BOTH',false,'Supports FIRC/BRC reconciliation and export proceeds realisation.'),
  ('A1.16','stock_inventory_register','Stock / inventory register (per SKU, with write-offs)','Client Books/WMS','CONDITIONAL','BOTH',false,'Stock-to-invoice reconciliation and Section 17(5)(h) write-off ITC reversal.'),
  ('A1.17','product_sku_master','Product / SKU master (MRP flag and declared MRP)','Client Master Data','CONDITIONAL','BOTH',false,'Mandatory for FMCG/retail. Cannot be derived from invoice register.'),
  ('A1.18','inter_gstin_transaction_register','Inter-GSTIN transaction register','Client ERP','CONDITIONAL','BOTH',false,'Stock transfers, cross-charges, capital good transfers between GSTINs under the same PAN.'),
  ('A1.19','cost_allocation_register','Cost allocation register (HO to branch)','Client Books','CONDITIONAL','BOTH',false,'Identifies allocations without invoice that may be taxable inter-GSTIN supplies. Contest item A7.2.'),
  ('A1.20','platform_settlement_report','Platform settlement reports (MTR/GTR/SSR/DRR/VRET)','Marketplace Operators','CONDITIONAL','BOTH',false,'Required for all marketplace sellers. report_type discriminator; field mapping per operator is configuration.'),
  ('A1.21','itc_reversal_register','ITC reversal register (Rule 42/43 and Section 17(5))','Client ERP/Books','CONDITIONAL','BOTH',false,'Client-maintained reversal records. Cross-checked against GSTR-3B.'),
  ('A1.22','rcm_register','RCM register','Client ERP','CONDITIONAL','BOTH',false,'Required for all taxpayers with RCM categories; important for import of services.'),
  ('A1.23','foreign_currency_payment_register','Foreign currency payment register','Client Bank/ERP','CONDITIONAL','BOTH',false,'Every foreign payment assessed for import of services RCM. Completeness-gated for B5 checks.'),
  ('A1.24','common_input_service_invoice','Common input service invoices at HO','Client ERP','CONDITIONAL','BOTH',false,'Required for ISD distribution verification. Post 01 Oct 2023 mandatory ISD compliance check.');

-- Many-to-many: which recon sections each document type feeds.
create table meta.document_type_section (
  doc_code     text not null references meta.document_type(code),
  section_code text not null references meta.recon_section(code),
  primary key (doc_code, section_code)
);

insert into meta.document_type_section (doc_code, section_code) values
  ('A1.01','A1'),('A1.01','A2'),('A1.01','A3'),('A1.01','A6'),('A1.01','A7'),('A1.01','A8'),('A1.01','A9'),
  ('A1.02','A1'),('A1.02','A2'),('A1.02','A8'),('A1.02','B8'),
  ('A1.03','B1'),('A1.03','B2'),('A1.03','B3'),('A1.03','B4'),('A1.03','B5'),('A1.03','B8'),
  ('A1.04','A4'),
  ('A1.05','A5'),
  ('A1.06','A5'),
  ('A1.07','A5'),
  ('A1.08','A2'),('A1.08','A3'),
  ('A1.09','A2'),('A1.09','A3'),('A1.09','B4'),
  ('A1.10','A4'),('A1.10','A5'),('A1.10','B2'),('A1.10','B7'),
  ('A1.11','A3'),('A1.11','B7'),
  ('A1.12','B2'),
  ('A1.13','B2'),
  ('A1.14','B2'),
  ('A1.15','A8'),
  ('A1.16','A1'),('A1.16','B3'),
  ('A1.17','A1'),
  ('A1.18','A7'),
  ('A1.19','A7'),
  ('A1.20','A9'),
  ('A1.21','B3'),('A1.21','B4'),
  ('A1.22','B5'),
  ('A1.23','B5'),
  ('A1.24','B6');

-- =====================================================================
-- INGEST TABLES
-- Envelope columns present on every table:
--   id, client_id, gstin, (tax_period | fy), doc_type_code, batch_id,
--   row_hash, created_at, created_by, modified_at, modified_by
-- =====================================================================

-- A1.01 -------------------------------------------------------------
create table ingest.sales_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.01' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  invoice_no     text        not null,
  invoice_date   date        not null,
  customer_gstin meta.gstin,
  invoice_type   text        check (invoice_type in ('B2B','B2CL','B2CS','EXP','SEZWP','SEZWOP','DE')),
  supply_type    text,
  hsn_sac        text,
  qty            meta.qty,
  uom            text,
  unit_price     numeric(18,4),
  trade_discount meta.money_inr default 0,
  freight        meta.money_inr default 0,
  packing        meta.money_inr default 0,
  insurance      meta.money_inr default 0,
  taxable_value  meta.money_inr not null,
  gst_rate       meta.tax_rate,
  cgst           meta.money_inr default 0,
  sgst           meta.money_inr default 0,
  igst           meta.money_inr default 0,
  cess           meta.money_inr default 0,
  total_value    meta.money_inr,
  total_tax      meta.money_inr generated always as (coalesce(cgst,0)+coalesce(sgst,0)+coalesce(igst,0)+coalesce(cess,0)) stored,
  currency       char(3)     not null default 'INR',
  place_of_supply text,
  reverse_charge boolean     not null default false,
  irn            text,
  ewb_no         text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.02 -------------------------------------------------------------
create table ingest.credit_debit_note_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.02' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  note_type      text        not null check (note_type in ('CN','DN')),
  note_no        text        not null,
  note_date      date        not null,
  original_invoice_no text   not null,     -- unlinked notes are a finding
  customer_gstin meta.gstin,
  supply_type    text,
  reason_code    text,
  reason_text    text,
  adjusted_taxable_value meta.money_inr not null,
  gst_rate       meta.tax_rate,
  cgst           meta.money_inr default 0,
  sgst           meta.money_inr default 0,
  igst           meta.money_inr default 0,
  cess           meta.money_inr default 0,
  note_value     meta.money_inr,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.03 -------------------------------------------------------------
create table ingest.purchase_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.03' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  supplier_gstin meta.gstin,
  invoice_no     text        not null,
  invoice_date   date        not null,
  hsn_sac        text,
  taxable_value  meta.money_inr not null,
  gst_rate       meta.tax_rate,
  cgst           meta.money_inr default 0,
  sgst           meta.money_inr default 0,
  igst           meta.money_inr default 0,
  cess           meta.money_inr default 0,
  gl_code        text        not null,     -- Rule 42 pool segregation
  cost_centre    text        not null,
  itc_eligibility text       check (itc_eligibility in ('ELIGIBLE','INELIGIBLE','BLOCKED','PARTIAL')),
  rcm_flag       boolean     not null default false,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.04 -------------------------------------------------------------
create table ingest.advance_receipt_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.04' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  receipt_no     text,
  receipt_date   date        not null,
  amount         meta.money_inr not null,
  customer       text,
  customer_gstin meta.gstin,
  supply_type    text        check (supply_type in ('GOODS','SERVICE')),
  gst_rate       meta.tax_rate,
  gst_paid_on_advance meta.money_inr default 0,
  invoice_linkage text,       -- invoice against which advance is adjusted
  is_adjusted    boolean     not null default false,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.05 -------------------------------------------------------------
create table ingest.unbilled_revenue_schedule (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.05' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  item_id        text,
  description    text,
  contract_ref   text,
  customer       text,
  customer_gstin meta.gstin,
  amount         meta.money_inr not null,
  date_of_service_completion date,   -- critical field; may be null (flag-only)

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.06 -------------------------------------------------------------
create table ingest.job_work_dispatch_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.06' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  challan_no     text,
  date_of_goods_dispatch_to_job_worker date not null,  -- triggers statutory deadline
  description    text,
  hsn            text,
  qty            meta.qty,
  uom            text,
  taxable_value  meta.money_inr,
  job_worker_gstin meta.gstin,
  expected_return_date date,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.07 -------------------------------------------------------------
create table ingest.running_account_contract_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.07' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  contract_ref   text        not null,
  customer       text,
  customer_gstin meta.gstin,
  billing_frequency text,
  statement_date date,
  delivery_date  date,
  amount         meta.money_inr,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.08 -------------------------------------------------------------  (FY-level)
create table ingest.audited_pl_account (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  fy             text        not null check (fy ~ '^[0-9]{4}-[0-9]{2}$'),
  doc_type_code  text        not null default 'A1.08' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  gl_code        text,
  gl_account     text        not null,
  narration      text,
  account_type   text        check (account_type in ('REVENUE','EXPENSE','OTHER')),
  amount         meta.money_inr not null,
  dr_cr          char(2)     check (dr_cr in ('DR','CR')),
  gst_treatment  text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, fy, row_hash)
);

-- A1.09 -------------------------------------------------------------  (FY-level)
create table ingest.trial_balance (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  fy             text        not null check (fy ~ '^[0-9]{4}-[0-9]{2}$'),
  doc_type_code  text        not null default 'A1.09' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  gl_code        text        not null,
  account_description text   not null,
  opening_balance meta.money_inr default 0,
  closing_balance meta.money_inr default 0,
  dr_cr          char(2)     check (dr_cr in ('DR','CR')),
  gst_treatment  text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, fy, row_hash)
);

-- A1.10 -------------------------------------------------------------  (FY-level)
create table ingest.balance_sheet (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  fy             text        not null check (fy ~ '^[0-9]{4}-[0-9]{2}$'),
  doc_type_code  text        not null default 'A1.10' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  line_item      text        not null,
  gl_code        text,
  category       text        check (category in
                   ('ADVANCE_FROM_CUSTOMER','UNBILLED_REVENUE','DEFERRED_REVENUE',
                    'CREDITOR','CAPITAL_GOODS','OTHER')),
  amount         meta.money_inr not null,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, fy, row_hash)
);

-- A1.11 -------------------------------------------------------------
create table ingest.fixed_asset_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.11' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  asset_id       text        not null,
  description    text,
  hsn            text,
  purchase_date  date,
  purchase_value meta.money_inr,
  itc_availed    meta.money_inr default 0,
  useful_life_months integer,
  date_of_disposal date,
  disposal_value meta.money_inr,
  disposal_type  text        check (disposal_type in ('SALE','SCRAP','TRANSFER','WRITE_OFF','OTHER')),

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.12 -------------------------------------------------------------
create table ingest.creditor_ageing_report (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.12' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  supplier_gstin meta.gstin,
  supplier_name  text,
  invoice_no     text        not null,
  invoice_date   date        not null,
  outstanding_amount meta.money_inr not null,
  ageing_days    integer,
  ageing_bucket  text,
  as_at_date     date        not null,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.13 -------------------------------------------------------------
create table ingest.payment_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.13' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  payment_date   date        not null,   -- bank transfer date, not cheque date
  amount         meta.money_inr not null,
  supplier_gstin meta.gstin,
  supplier_name  text,
  invoice_no     text,
  payment_ref    text,
  payment_mode   text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.14 -------------------------------------------------------------
create table ingest.bank_statement_outward (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.14' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  txn_date       date        not null,
  amount         meta.money_inr not null,
  beneficiary    text,
  beneficiary_account text,
  narration      text,
  bank_ref       text,
  invoice_ref    text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.15 -------------------------------------------------------------
create table ingest.bank_statement_inward_forex (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.15' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  txn_date       date        not null,
  amount_fcy     numeric(18,2) not null,
  currency       char(3)     not null,
  amount_inr     meta.money_inr,
  remitter       text,
  ad_bank_ref    text,
  export_invoice_ref text,
  firc_no        text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.16 -------------------------------------------------------------
create table ingest.stock_inventory_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.16' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  sku            text        not null,
  description    text,
  hsn            text,
  opening_qty    meta.qty,
  closing_qty    meta.qty,
  write_off_qty  meta.qty    default 0,
  write_off_value meta.money_inr default 0,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.17 -------------------------------------------------------------  (master data)
create table ingest.product_sku_master (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.17' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  sku            text        not null,
  description    text,
  hsn            text,
  mrp_applicable boolean     not null default false,
  declared_mrp   meta.money_inr,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.18 -------------------------------------------------------------
create table ingest.inter_gstin_transaction_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,     -- reporting GSTIN
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.18' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  from_gstin     meta.gstin  not null,
  to_gstin       meta.gstin  not null,
  txn_type       text        check (txn_type in ('STOCK_TRANSFER','CROSS_CHARGE','CAPITAL_GOOD_TRANSFER','OTHER')),
  txn_date       date        not null,
  invoice_no     text,
  description    text,
  hsn_sac        text,
  taxable_value  meta.money_inr not null,
  gst_rate       meta.tax_rate,
  cgst           meta.money_inr default 0,
  sgst           meta.money_inr default 0,
  igst           meta.money_inr default 0,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.19 -------------------------------------------------------------
create table ingest.cost_allocation_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.19' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  from_gstin     meta.gstin,   -- typically HO
  to_gstin       meta.gstin,   -- branch
  allocation_basis text,
  description    text,
  amount         meta.money_inr not null,
  has_invoice    boolean     not null default false,   -- allocations w/o invoice = contest A7.2
  invoice_no     text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.20 -------------------------------------------------------------
create table ingest.platform_settlement_report (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.20' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  report_type    text        not null check (report_type in ('MTR','GTR','SSR','DRR','VRET')),
  operator       text        not null,   -- Amazon, Flipkart, ...
  order_no       text,
  order_date     date,
  invoice_no     text,
  invoice_date   date,
  buyer_state    text,
  taxable_value  meta.money_inr,
  gst_rate       meta.tax_rate,
  cgst           meta.money_inr default 0,
  sgst           meta.money_inr default 0,
  igst           meta.money_inr default 0,
  tcs            meta.money_inr default 0,
  marketplace_fee meta.money_inr default 0,
  shipping_charges meta.money_inr default 0,
  net_payout     meta.money_inr,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.21 -------------------------------------------------------------
create table ingest.itc_reversal_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.21' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  reversal_type  text        not null check (reversal_type in ('RULE_42','RULE_43','SEC_17_5','OTHER')),
  reversal_basis text,
  cgst_reversed  meta.money_inr default 0,
  sgst_reversed  meta.money_inr default 0,
  igst_reversed  meta.money_inr default 0,
  cess_reversed  meta.money_inr default 0,
  remarks        text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.22 -------------------------------------------------------------
create table ingest.rcm_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.22' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  rcm_category   text        not null,
  supplier_gstin meta.gstin,
  supplier_name  text,
  invoice_no     text,
  invoice_date   date,
  taxable_value  meta.money_inr not null,
  gst_rate       meta.tax_rate,
  rcm_liability  meta.money_inr,
  cgst           meta.money_inr default 0,
  sgst           meta.money_inr default 0,
  igst           meta.money_inr default 0,
  paid_period    date,
  is_paid        boolean     not null default false,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.23 -------------------------------------------------------------
create table ingest.foreign_currency_payment_register (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.23' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  payment_date   date        not null,
  amount_fcy     numeric(18,2) not null,
  currency       char(3)     not null,
  amount_inr     meta.money_inr,
  vendor         text,
  country        text,
  purpose        text,
  invoice_contract_ref text,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- A1.24 -------------------------------------------------------------
create table ingest.common_input_service_invoice (
  id             bigint generated always as identity primary key,
  client_id      bigint      not null,
  gstin          meta.gstin  not null,     -- HO GSTIN receiving the invoice
  tax_period     date        not null,
  doc_type_code  text        not null default 'A1.24' references meta.document_type(code),
  batch_id       bigint      not null,
  row_hash       text        not null,

  invoice_no     text        not null,
  invoice_date   date        not null,
  supplier_gstin meta.gstin,
  service_category text      check (service_category in ('RENT','IT','INSURANCE','AUDIT','OTHER')),
  taxable_value  meta.money_inr not null,
  gst_rate       meta.tax_rate,
  cgst           meta.money_inr default 0,
  sgst           meta.money_inr default 0,
  igst           meta.money_inr default 0,

  created_at     timestamptz not null default now(),
  created_by     text        not null,
  modified_at    timestamptz,
  modified_by    text,
  unique (client_id, gstin, tax_period, row_hash)
);

-- ---------------------------------------------------------------------
-- Attach the audit trigger + common lookup indexes to every ingest table
-- ---------------------------------------------------------------------
do $$
declare
  t record;
begin
  for t in
    select table_name from information_schema.tables
    where table_schema = 'ingest' and table_type = 'BASE TABLE'
  loop
    execute format(
      'create trigger tg_audit_%1$s before insert or update on ingest.%1$I
         for each row execute function meta.tg_audit()', t.table_name);
    execute format(
      'create index ix_%1$s_client_gstin on ingest.%1$I (client_id, gstin)', t.table_name);
    execute format(
      'create index ix_%1$s_batch on ingest.%1$I (batch_id)', t.table_name);
  end loop;
end $$;

commit;
