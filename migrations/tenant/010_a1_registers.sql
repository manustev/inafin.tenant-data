-- =============================================================================
-- Tenant migration 010 — the remaining 23 Group A1 registers.
--
-- TYPED-TABLES-PLAN.md build order steps 4-5, against
-- reference/inafin_a1_schema.sql. sales_register (A1.01) landed separately as
-- the reviewed reference pattern (tenant 009); these follow it.
--
-- BUSINESS COLUMNS ARE THE REFERENCE'S, verbatim, with meta.* domains remapped
-- to platform_ref.*. THE ENVELOPE IS OURS, and it is the part the reference has
-- no substitute for:
--
--   entity_id          not client_id. Isolation is the schema and the GRANT
--                      (invariant 2), never a discriminator column.
--   batch_id           a real FK to ingest_batch, the queue of record. The
--                      reference has `batch_id bigint` pointing at nothing.
--   bronze_ingest_id   the reference has NO Bronze linkage at all, so no path
--                      from a row to the byte-exact artefact under Object Lock.
--   valid_from/_to,    absent from the reference entirely. Without them a
--   recorded_at,       correction overwrites, and what we believed when a return
--   superseded_at      was filed is lost (ARCHITECTURE.md 8).
--
-- TWO KINDS OF KEY, and the distinction is deliberate:
--
--   NATURAL KEY   the document carries an identifier — invoice_no, asset_id,
--                 contract_ref, sku. Keyed on it, partial on the live version,
--                 so a CORRECTED row supersedes its predecessor instead of
--                 landing beside it.
--   CONTENT KEY   the document carries no identifier at all — a trial-balance
--                 line, a cost allocation, a bank row. Keyed on
--                 (entity_id, gstin, period, row_hash), which is the reference's
--                 key: it dedups identical content and nothing more. Claiming a
--                 natural key here would silently merge two genuinely distinct
--                 rows that happen to look alike, and a resubmitted correction
--                 lands as a NEW row rather than superseding. That is a real
--                 limitation of these registers, not of this schema — it is
--                 recorded rather than papered over.
--
-- HEADER/LINE SPLITS ARE NOT APPLIED HERE. Only A1.01 has one. The reference is
-- line-grain and flat for all of these, so a header table would have no
-- ERP-supplied totals to hold — the empty abstraction TYPED-TABLES-PLAN.md 6
-- exists to avoid. Revisit per type when a real export shows invoice-level
-- totals; purchase_register is the first candidate, because step 4 has to
-- redefine the header-grain v1_purchase_invoice over it.
-- =============================================================================

-- --- CREDIT_DEBIT_NOTE_REGISTER (credit_debit_note_register) ------
CREATE TABLE IF NOT EXISTS {{silver}}.credit_debit_note_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'CREDIT_DEBIT_NOTE_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'CREDIT_DEBIT_NOTE_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    note_type text not null check (note_type in ('CN','DN')),
    note_no text not null,
    note_date date not null,
    original_invoice_no text not null,
    customer_gstin platform_ref.gstin,
    supply_type text,
    reason_code text,
    reason_text text,
    adjusted_taxable_value platform_ref.money_inr not null,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr default 0,
    sgst platform_ref.money_inr default 0,
    igst platform_ref.money_inr default 0,
    cess platform_ref.money_inr default 0,
    note_value platform_ref.money_inr,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT credit_debit_note_register_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS credit_debit_note_register_natural_key_uq
    ON {{silver}}.credit_debit_note_register (entity_id, gstin, note_no)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS credit_debit_note_register_batch_idx ON {{silver}}.credit_debit_note_register (batch_id);
CREATE INDEX IF NOT EXISTS credit_debit_note_register_gstin_period_idx
    ON {{silver}}.credit_debit_note_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_credit_debit_note_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, note_type, note_no, note_date, original_invoice_no, customer_gstin, supply_type, reason_code, reason_text, adjusted_taxable_value, gst_rate, cgst, sgst, igst, cess, note_value, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.credit_debit_note_register;


-- --- PURCHASE_REGISTER (purchase_register) ------------------------
CREATE TABLE IF NOT EXISTS {{silver}}.purchase_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'PURCHASE_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'PURCHASE_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    supplier_gstin platform_ref.gstin,
    invoice_no text not null,
    invoice_date date not null,
    hsn_sac text,
    taxable_value platform_ref.money_inr not null,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr default 0,
    sgst platform_ref.money_inr default 0,
    igst platform_ref.money_inr default 0,
    cess platform_ref.money_inr default 0,
    gl_code text not null,
    cost_centre text not null,
    itc_eligibility text check (itc_eligibility in ('ELIGIBLE','INELIGIBLE','BLOCKED','PARTIAL')),
    rcm_flag boolean not null default false,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT purchase_register_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS purchase_register_natural_key_uq
    ON {{silver}}.purchase_register (entity_id, gstin, supplier_gstin, invoice_no)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS purchase_register_batch_idx ON {{silver}}.purchase_register (batch_id);
CREATE INDEX IF NOT EXISTS purchase_register_gstin_period_idx
    ON {{silver}}.purchase_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_purchase_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, supplier_gstin, invoice_no, invoice_date, hsn_sac, taxable_value, gst_rate, cgst, sgst, igst, cess, gl_code, cost_centre, itc_eligibility, rcm_flag, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.purchase_register;


-- --- ADVANCE_RECEIPT_REGISTER (advance_receipt_register) ----------
CREATE TABLE IF NOT EXISTS {{silver}}.advance_receipt_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'ADVANCE_RECEIPT_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'ADVANCE_RECEIPT_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    receipt_no text,
    receipt_date date not null,
    amount platform_ref.money_inr not null,
    customer text,
    customer_gstin platform_ref.gstin,
    supply_type text check (supply_type in ('GOODS','SERVICE')),
    gst_rate platform_ref.tax_rate,
    gst_paid_on_advance platform_ref.money_inr default 0,
    invoice_linkage text,
    is_adjusted boolean not null default false,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT advance_receipt_register_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS advance_receipt_register_content_key_uq
    ON {{silver}}.advance_receipt_register (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS advance_receipt_register_batch_idx ON {{silver}}.advance_receipt_register (batch_id);
CREATE INDEX IF NOT EXISTS advance_receipt_register_gstin_period_idx
    ON {{silver}}.advance_receipt_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_advance_receipt_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, receipt_no, receipt_date, amount, customer, customer_gstin, supply_type, gst_rate, gst_paid_on_advance, invoice_linkage, is_adjusted, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.advance_receipt_register;


-- --- UNBILLED_REVENUE_SCHEDULE (unbilled_revenue_schedule) --------
CREATE TABLE IF NOT EXISTS {{silver}}.unbilled_revenue_schedule (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'UNBILLED_REVENUE_SCHEDULE'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'UNBILLED_REVENUE_SCHEDULE'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    item_id text,
    description text,
    contract_ref text,
    customer text,
    customer_gstin platform_ref.gstin,
    amount platform_ref.money_inr not null,
    date_of_service_completion date,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT unbilled_revenue_schedule_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS unbilled_revenue_schedule_content_key_uq
    ON {{silver}}.unbilled_revenue_schedule (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS unbilled_revenue_schedule_batch_idx ON {{silver}}.unbilled_revenue_schedule (batch_id);
CREATE INDEX IF NOT EXISTS unbilled_revenue_schedule_gstin_period_idx
    ON {{silver}}.unbilled_revenue_schedule (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_unbilled_revenue_schedule AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, item_id, description, contract_ref, customer, customer_gstin, amount, date_of_service_completion, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.unbilled_revenue_schedule;


-- --- JOBWORK_DISPATCH_REGISTER (job_work_dispatch_register) -------
CREATE TABLE IF NOT EXISTS {{silver}}.job_work_dispatch_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'JOBWORK_DISPATCH_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'JOBWORK_DISPATCH_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    challan_no text,
    date_of_goods_dispatch_to_job_worker date not null,
    description text,
    hsn text,
    qty platform_ref.qty,
    uom text,
    taxable_value platform_ref.money_inr,
    job_worker_gstin platform_ref.gstin,
    expected_return_date date,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT job_work_dispatch_register_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS job_work_dispatch_register_content_key_uq
    ON {{silver}}.job_work_dispatch_register (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS job_work_dispatch_register_batch_idx ON {{silver}}.job_work_dispatch_register (batch_id);
CREATE INDEX IF NOT EXISTS job_work_dispatch_register_gstin_period_idx
    ON {{silver}}.job_work_dispatch_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_job_work_dispatch_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, challan_no, date_of_goods_dispatch_to_job_worker, description, hsn, qty, uom, taxable_value, job_worker_gstin, expected_return_date, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.job_work_dispatch_register;


-- --- CONTINUOUS_SUPPLY_CONTRACT_REGISTER (running_account_contract_register) ---
CREATE TABLE IF NOT EXISTS {{silver}}.running_account_contract_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'CONTINUOUS_SUPPLY_CONTRACT_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'CONTINUOUS_SUPPLY_CONTRACT_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    contract_ref text not null,
    customer text,
    customer_gstin platform_ref.gstin,
    billing_frequency text,
    statement_date date,
    delivery_date date,
    amount platform_ref.money_inr,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT running_account_contract_register_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS running_account_contract_register_natural_key_uq
    ON {{silver}}.running_account_contract_register (entity_id, gstin, contract_ref)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS running_account_contract_register_batch_idx ON {{silver}}.running_account_contract_register (batch_id);
CREATE INDEX IF NOT EXISTS running_account_contract_register_gstin_period_idx
    ON {{silver}}.running_account_contract_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_running_account_contract_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, contract_ref, customer, customer_gstin, billing_frequency, statement_date, delivery_date, amount, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.running_account_contract_register;


-- --- AUDITED_PROFIT_AND_LOSS (audited_pl_account) -----------------
CREATE TABLE IF NOT EXISTS {{silver}}.audited_pl_account (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    fy              text NOT NULL CHECK (fy ~ '^[0-9]{4}-[0-9]{2}$'),
    doc_type_code   text NOT NULL DEFAULT 'AUDITED_PROFIT_AND_LOSS'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'AUDITED_PROFIT_AND_LOSS'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    gl_code text,
    gl_account text not null,
    narration text,
    account_type text check (account_type in ('REVENUE','EXPENSE','OTHER')),
    amount platform_ref.money_inr not null,
    dr_cr char(2) check (dr_cr in ('DR','CR')),
    gst_treatment text,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT audited_pl_account_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS audited_pl_account_content_key_uq
    ON {{silver}}.audited_pl_account (entity_id, gstin, fy, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS audited_pl_account_batch_idx ON {{silver}}.audited_pl_account (batch_id);
CREATE INDEX IF NOT EXISTS audited_pl_account_gstin_period_idx
    ON {{silver}}.audited_pl_account (gstin, fy);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_audited_pl_account AS
SELECT id, entity_id, gstin, fy, doc_type_code, batch_id, bronze_ingest_id, row_hash, gl_code, gl_account, narration, account_type, amount, dr_cr, gst_treatment, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.audited_pl_account;


-- --- TRIAL_BALANCE (trial_balance) --------------------------------
CREATE TABLE IF NOT EXISTS {{silver}}.trial_balance (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    fy              text NOT NULL CHECK (fy ~ '^[0-9]{4}-[0-9]{2}$'),
    doc_type_code   text NOT NULL DEFAULT 'TRIAL_BALANCE'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'TRIAL_BALANCE'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    gl_code text not null,
    account_description text not null,
    opening_balance platform_ref.money_inr default 0,
    closing_balance platform_ref.money_inr default 0,
    dr_cr char(2) check (dr_cr in ('DR','CR')),
    gst_treatment text,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT trial_balance_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS trial_balance_natural_key_uq
    ON {{silver}}.trial_balance (entity_id, gstin, gl_code)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS trial_balance_batch_idx ON {{silver}}.trial_balance (batch_id);
CREATE INDEX IF NOT EXISTS trial_balance_gstin_period_idx
    ON {{silver}}.trial_balance (gstin, fy);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_trial_balance AS
SELECT id, entity_id, gstin, fy, doc_type_code, batch_id, bronze_ingest_id, row_hash, gl_code, account_description, opening_balance, closing_balance, dr_cr, gst_treatment, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.trial_balance;


-- --- BALANCE_SHEET (balance_sheet) --------------------------------
CREATE TABLE IF NOT EXISTS {{silver}}.balance_sheet (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    fy              text NOT NULL CHECK (fy ~ '^[0-9]{4}-[0-9]{2}$'),
    doc_type_code   text NOT NULL DEFAULT 'BALANCE_SHEET'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'BALANCE_SHEET'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    line_item text not null,
    gl_code text,
    category text check (category in ('ADVANCE_FROM_CUSTOMER','UNBILLED_REVENUE','DEFERRED_REVENUE', 'CREDITOR','CAPITAL_GOODS','OTHER')),
    amount platform_ref.money_inr not null,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT balance_sheet_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS balance_sheet_natural_key_uq
    ON {{silver}}.balance_sheet (entity_id, gstin, line_item)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS balance_sheet_batch_idx ON {{silver}}.balance_sheet (batch_id);
CREATE INDEX IF NOT EXISTS balance_sheet_gstin_period_idx
    ON {{silver}}.balance_sheet (gstin, fy);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_balance_sheet AS
SELECT id, entity_id, gstin, fy, doc_type_code, batch_id, bronze_ingest_id, row_hash, line_item, gl_code, category, amount, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.balance_sheet;


-- --- FIXED_ASSET_REGISTER (fixed_asset_register) ------------------
CREATE TABLE IF NOT EXISTS {{silver}}.fixed_asset_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'FIXED_ASSET_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'FIXED_ASSET_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    asset_id text not null,
    description text,
    hsn text,
    purchase_date date,
    purchase_value platform_ref.money_inr,
    itc_availed platform_ref.money_inr default 0,
    useful_life_months integer,
    date_of_disposal date,
    disposal_value platform_ref.money_inr,
    disposal_type text check (disposal_type in ('SALE','SCRAP','TRANSFER','WRITE_OFF','OTHER')),

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT fixed_asset_register_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS fixed_asset_register_natural_key_uq
    ON {{silver}}.fixed_asset_register (entity_id, gstin, asset_id)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS fixed_asset_register_batch_idx ON {{silver}}.fixed_asset_register (batch_id);
CREATE INDEX IF NOT EXISTS fixed_asset_register_gstin_period_idx
    ON {{silver}}.fixed_asset_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_fixed_asset_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, asset_id, description, hsn, purchase_date, purchase_value, itc_availed, useful_life_months, date_of_disposal, disposal_value, disposal_type, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.fixed_asset_register;


-- --- CREDITOR_AGEING_REPORT (creditor_ageing_report) --------------
CREATE TABLE IF NOT EXISTS {{silver}}.creditor_ageing_report (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'CREDITOR_AGEING_REPORT'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'CREDITOR_AGEING_REPORT'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text not null,
    invoice_date date not null,
    outstanding_amount platform_ref.money_inr not null,
    ageing_days integer,
    ageing_bucket text,
    as_at_date date not null,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT creditor_ageing_report_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS creditor_ageing_report_natural_key_uq
    ON {{silver}}.creditor_ageing_report (entity_id, gstin, as_at_date, supplier_gstin, invoice_no)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS creditor_ageing_report_batch_idx ON {{silver}}.creditor_ageing_report (batch_id);
CREATE INDEX IF NOT EXISTS creditor_ageing_report_gstin_period_idx
    ON {{silver}}.creditor_ageing_report (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_creditor_ageing_report AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, supplier_gstin, supplier_name, invoice_no, invoice_date, outstanding_amount, ageing_days, ageing_bucket, as_at_date, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.creditor_ageing_report;


-- --- SUPPLIER_PAYMENT_REGISTER (payment_register) -----------------
CREATE TABLE IF NOT EXISTS {{silver}}.payment_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'SUPPLIER_PAYMENT_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'SUPPLIER_PAYMENT_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    payment_date date not null,
    amount platform_ref.money_inr not null,
    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text,
    payment_ref text,
    payment_mode text,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT payment_register_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS payment_register_content_key_uq
    ON {{silver}}.payment_register (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS payment_register_batch_idx ON {{silver}}.payment_register (batch_id);
CREATE INDEX IF NOT EXISTS payment_register_gstin_period_idx
    ON {{silver}}.payment_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_payment_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, payment_date, amount, supplier_gstin, supplier_name, invoice_no, payment_ref, payment_mode, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.payment_register;


-- --- BANK_STATEMENT_OUTWARD (bank_statement_outward) --------------
CREATE TABLE IF NOT EXISTS {{silver}}.bank_statement_outward (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'BANK_STATEMENT_OUTWARD'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'BANK_STATEMENT_OUTWARD'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    txn_date date not null,
    amount platform_ref.money_inr not null,
    beneficiary text,
    beneficiary_account text,
    narration text,
    bank_ref text,
    invoice_ref text,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT bank_statement_outward_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS bank_statement_outward_content_key_uq
    ON {{silver}}.bank_statement_outward (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS bank_statement_outward_batch_idx ON {{silver}}.bank_statement_outward (batch_id);
CREATE INDEX IF NOT EXISTS bank_statement_outward_gstin_period_idx
    ON {{silver}}.bank_statement_outward (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_bank_statement_outward AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, txn_date, amount, beneficiary, beneficiary_account, narration, bank_ref, invoice_ref, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.bank_statement_outward;


-- --- BANK_STATEMENT_INWARD_FX (bank_statement_inward_forex) -------
CREATE TABLE IF NOT EXISTS {{silver}}.bank_statement_inward_forex (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'BANK_STATEMENT_INWARD_FX'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'BANK_STATEMENT_INWARD_FX'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    txn_date date not null,
    amount_fcy numeric(18,2) not null,
    currency char(3) not null,
    amount_inr platform_ref.money_inr,
    remitter text,
    ad_bank_ref text,
    export_invoice_ref text,
    firc_no text,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT bank_statement_inward_forex_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS bank_statement_inward_forex_content_key_uq
    ON {{silver}}.bank_statement_inward_forex (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS bank_statement_inward_forex_batch_idx ON {{silver}}.bank_statement_inward_forex (batch_id);
CREATE INDEX IF NOT EXISTS bank_statement_inward_forex_gstin_period_idx
    ON {{silver}}.bank_statement_inward_forex (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_bank_statement_inward_forex AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, txn_date, amount_fcy, currency, amount_inr, remitter, ad_bank_ref, export_invoice_ref, firc_no, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.bank_statement_inward_forex;


-- --- STOCK_REGISTER (stock_inventory_register) --------------------
CREATE TABLE IF NOT EXISTS {{silver}}.stock_inventory_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'STOCK_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'STOCK_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    sku text not null,
    description text,
    hsn text,
    opening_qty platform_ref.qty,
    closing_qty platform_ref.qty,
    write_off_qty platform_ref.qty default 0,
    write_off_value platform_ref.money_inr default 0,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT stock_inventory_register_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS stock_inventory_register_natural_key_uq
    ON {{silver}}.stock_inventory_register (entity_id, gstin, tax_period, sku)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS stock_inventory_register_batch_idx ON {{silver}}.stock_inventory_register (batch_id);
CREATE INDEX IF NOT EXISTS stock_inventory_register_gstin_period_idx
    ON {{silver}}.stock_inventory_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_stock_inventory_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, sku, description, hsn, opening_qty, closing_qty, write_off_qty, write_off_value, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.stock_inventory_register;


-- --- SKU_MASTER (product_sku_master) ------------------------------
CREATE TABLE IF NOT EXISTS {{silver}}.product_sku_master (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'SKU_MASTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'SKU_MASTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    sku text not null,
    description text,
    hsn text,
    mrp_applicable boolean not null default false,
    declared_mrp platform_ref.money_inr,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT product_sku_master_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS product_sku_master_natural_key_uq
    ON {{silver}}.product_sku_master (entity_id, gstin, sku)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS product_sku_master_batch_idx ON {{silver}}.product_sku_master (batch_id);
CREATE INDEX IF NOT EXISTS product_sku_master_gstin_period_idx
    ON {{silver}}.product_sku_master (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_product_sku_master AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, sku, description, hsn, mrp_applicable, declared_mrp, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.product_sku_master;


-- --- INTER_GSTIN_TRANSACTION_REGISTER (inter_gstin_transaction_register) ---
CREATE TABLE IF NOT EXISTS {{silver}}.inter_gstin_transaction_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'INTER_GSTIN_TRANSACTION_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'INTER_GSTIN_TRANSACTION_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    from_gstin platform_ref.gstin not null,
    to_gstin platform_ref.gstin not null,
    txn_type text check (txn_type in ('STOCK_TRANSFER','CROSS_CHARGE','CAPITAL_GOOD_TRANSFER','OTHER')),
    txn_date date not null,
    invoice_no text,
    description text,
    hsn_sac text,
    taxable_value platform_ref.money_inr not null,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr default 0,
    sgst platform_ref.money_inr default 0,
    igst platform_ref.money_inr default 0,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT inter_gstin_transaction_register_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS inter_gstin_transaction_register_content_key_uq
    ON {{silver}}.inter_gstin_transaction_register (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS inter_gstin_transaction_register_batch_idx ON {{silver}}.inter_gstin_transaction_register (batch_id);
CREATE INDEX IF NOT EXISTS inter_gstin_transaction_register_gstin_period_idx
    ON {{silver}}.inter_gstin_transaction_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_inter_gstin_transaction_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, from_gstin, to_gstin, txn_type, txn_date, invoice_no, description, hsn_sac, taxable_value, gst_rate, cgst, sgst, igst, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.inter_gstin_transaction_register;


-- --- COST_ALLOCATION_REGISTER (cost_allocation_register) ----------
CREATE TABLE IF NOT EXISTS {{silver}}.cost_allocation_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'COST_ALLOCATION_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'COST_ALLOCATION_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    from_gstin platform_ref.gstin,
    to_gstin platform_ref.gstin,
    allocation_basis text,
    description text,
    amount platform_ref.money_inr not null,
    has_invoice boolean not null default false,
    invoice_no text,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT cost_allocation_register_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS cost_allocation_register_content_key_uq
    ON {{silver}}.cost_allocation_register (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS cost_allocation_register_batch_idx ON {{silver}}.cost_allocation_register (batch_id);
CREATE INDEX IF NOT EXISTS cost_allocation_register_gstin_period_idx
    ON {{silver}}.cost_allocation_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_cost_allocation_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, from_gstin, to_gstin, allocation_basis, description, amount, has_invoice, invoice_no, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.cost_allocation_register;


-- --- PLATFORM_SETTLEMENT_REPORT (platform_settlement_report) ------
CREATE TABLE IF NOT EXISTS {{silver}}.platform_settlement_report (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    -- SEVEN registry codes share this table, so doc_type_code is a list and
    -- has no default: the caller must say which operator's report this is.
    -- The one place TYPED-TABLES-PLAN.md 8's "one table per registry type"
    -- rule is broken, on the reference schema, confirmed 2026-08-07. Shared
    -- migration 013 relaxes the constraint that forbade it.
    doc_type_code   text NOT NULL
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code IN ('AMAZON_MTR',
                                 'FLIPKART_GTR',
                                 'MARKETPLACE_SETTLEMENT_SSR',
                                 'MARKETPLACE_DISBURSEMENT_DRR',
                                 'MARKETPLACE_RETURNS_VRET',
                                 'FOOD_DELIVERY_PARTNER_REPORT',
                                 'OTHER_MARKETPLACE_SETTLEMENT')),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    report_type text not null check (report_type in ('MTR','GTR','SSR','DRR','VRET','FDP','OTHER')),
    operator text not null,
    order_no text,
    order_date date,
    invoice_no text,
    invoice_date date,
    buyer_state text,
    taxable_value platform_ref.money_inr,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr default 0,
    sgst platform_ref.money_inr default 0,
    igst platform_ref.money_inr default 0,
    tcs platform_ref.money_inr default 0,
    marketplace_fee platform_ref.money_inr default 0,
    shipping_charges platform_ref.money_inr default 0,
    net_payout platform_ref.money_inr,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT platform_settlement_report_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS platform_settlement_report_content_key_uq
    ON {{silver}}.platform_settlement_report (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS platform_settlement_report_batch_idx ON {{silver}}.platform_settlement_report (batch_id);
CREATE INDEX IF NOT EXISTS platform_settlement_report_gstin_period_idx
    ON {{silver}}.platform_settlement_report (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_platform_settlement_report AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, report_type, operator, order_no, order_date, invoice_no, invoice_date, buyer_state, taxable_value, gst_rate, cgst, sgst, igst, tcs, marketplace_fee, shipping_charges, net_payout, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.platform_settlement_report;


-- --- ITC_REVERSAL_REGISTER (itc_reversal_register) ----------------
CREATE TABLE IF NOT EXISTS {{silver}}.itc_reversal_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'ITC_REVERSAL_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'ITC_REVERSAL_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    reversal_type text not null check (reversal_type in ('RULE_42','RULE_43','SEC_17_5','OTHER')),
    reversal_basis text,
    cgst_reversed platform_ref.money_inr default 0,
    sgst_reversed platform_ref.money_inr default 0,
    igst_reversed platform_ref.money_inr default 0,
    cess_reversed platform_ref.money_inr default 0,
    remarks text,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT itc_reversal_register_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS itc_reversal_register_content_key_uq
    ON {{silver}}.itc_reversal_register (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS itc_reversal_register_batch_idx ON {{silver}}.itc_reversal_register (batch_id);
CREATE INDEX IF NOT EXISTS itc_reversal_register_gstin_period_idx
    ON {{silver}}.itc_reversal_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_itc_reversal_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, reversal_type, reversal_basis, cgst_reversed, sgst_reversed, igst_reversed, cess_reversed, remarks, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.itc_reversal_register;


-- --- RCM_REGISTER (rcm_register) ----------------------------------
CREATE TABLE IF NOT EXISTS {{silver}}.rcm_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'RCM_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'RCM_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    rcm_category text not null,
    supplier_gstin platform_ref.gstin,
    supplier_name text,
    invoice_no text,
    invoice_date date,
    taxable_value platform_ref.money_inr not null,
    gst_rate platform_ref.tax_rate,
    rcm_liability platform_ref.money_inr,
    cgst platform_ref.money_inr default 0,
    sgst platform_ref.money_inr default 0,
    igst platform_ref.money_inr default 0,
    paid_period date,
    is_paid boolean not null default false,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT rcm_register_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS rcm_register_content_key_uq
    ON {{silver}}.rcm_register (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS rcm_register_batch_idx ON {{silver}}.rcm_register (batch_id);
CREATE INDEX IF NOT EXISTS rcm_register_gstin_period_idx
    ON {{silver}}.rcm_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_rcm_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, rcm_category, supplier_gstin, supplier_name, invoice_no, invoice_date, taxable_value, gst_rate, rcm_liability, cgst, sgst, igst, paid_period, is_paid, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.rcm_register;


-- --- FOREIGN_CURRENCY_PAYMENT_REGISTER (foreign_currency_payment_register) ---
CREATE TABLE IF NOT EXISTS {{silver}}.foreign_currency_payment_register (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'FOREIGN_CURRENCY_PAYMENT_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'FOREIGN_CURRENCY_PAYMENT_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    payment_date date not null,
    amount_fcy numeric(18,2) not null,
    currency char(3) not null,
    amount_inr platform_ref.money_inr,
    vendor text,
    country text,
    purpose text,
    invoice_contract_ref text,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT foreign_currency_payment_register_window CHECK (valid_to > valid_from)
);

-- Content key: no document identifier exists, so identical content is all
-- that can be deduped. A correction lands as a new row; see the header.
CREATE UNIQUE INDEX IF NOT EXISTS foreign_currency_payment_register_content_key_uq
    ON {{silver}}.foreign_currency_payment_register (entity_id, gstin, tax_period, row_hash)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS foreign_currency_payment_register_batch_idx ON {{silver}}.foreign_currency_payment_register (batch_id);
CREATE INDEX IF NOT EXISTS foreign_currency_payment_register_gstin_period_idx
    ON {{silver}}.foreign_currency_payment_register (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_foreign_currency_payment_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, payment_date, amount_fcy, currency, amount_inr, vendor, country, purpose, invoice_contract_ref, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.foreign_currency_payment_register;


-- --- HO_COMMON_INPUT_SERVICE_INVOICE (common_input_service_invoice) ---
CREATE TABLE IF NOT EXISTS {{silver}}.common_input_service_invoice (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,
    doc_type_code   text NOT NULL DEFAULT 'HO_COMMON_INPUT_SERVICE_INVOICE'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'HO_COMMON_INPUT_SERVICE_INVOICE'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash        text NOT NULL,

    invoice_no text not null,
    invoice_date date not null,
    supplier_gstin platform_ref.gstin,
    service_category text check (service_category in ('RENT','IT','INSURANCE','AUDIT','OTHER')),
    taxable_value platform_ref.money_inr not null,
    gst_rate platform_ref.tax_rate,
    cgst platform_ref.money_inr default 0,
    sgst platform_ref.money_inr default 0,
    igst platform_ref.money_inr default 0,

    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT common_input_service_invoice_window CHECK (valid_to > valid_from)
);

-- Natural key: the document identifies itself, so a correction supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS common_input_service_invoice_natural_key_uq
    ON {{silver}}.common_input_service_invoice (entity_id, gstin, supplier_gstin, invoice_no)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS common_input_service_invoice_batch_idx ON {{silver}}.common_input_service_invoice (batch_id);
CREATE INDEX IF NOT EXISTS common_input_service_invoice_gstin_period_idx
    ON {{silver}}.common_input_service_invoice (gstin, tax_period);

-- Common core only: audit columns and any tenant extension stay below the
-- view line, or v2 sees a different shape per tenant. Definer rights.
CREATE OR REPLACE VIEW {{silver}}.v1_common_input_service_invoice AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id, row_hash, invoice_no, invoice_date, supplier_gstin, service_category, taxable_value, gst_rate, cgst, sgst, igst, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.common_input_service_invoice;

