-- =============================================================================
-- Tenant migration 020 — three Archetype 2 registers, added for the A2-A7
-- extraction adapters (streamed-marinating-gray.md phase 6): PAYROLL_TDS_
-- REGISTER (A3.05), FIRC_BRC_REGISTER (A5.14), FORM_15CA_15CB (A7.04).
--
-- Same idiom as migration 010's 23 Group A1 registers (see that migration's
-- header for the envelope/key-kind reasoning) — this is the SAME mechanism
-- (RegisterSpec + RegisterLoader), not a new one, per TYPED-TABLES-PLAN.md
-- 10 step 3's precedent. All three use a plain single-column
-- `doc_type_code` FK (not the composite archetype-pinned FK migrations
-- 016-019 use) because that is what the existing register mechanism already
-- does for every other flat register — no new pattern introduced here.
--
-- All three are NATURAL-keyed: each carries its own document-level reference
-- number (an employee/vendor ref, an export invoice number, a Form 15CA
-- acknowledgement number), so a corrected re-export supersedes rather than
-- landing beside the original — the same key strategy as
-- credit_debit_note_register, not purchase_register's content-keyed
-- siblings.
-- =============================================================================

-- --- PAYROLL_TDS_REGISTER (payroll_tds_register) ----------------------------
CREATE TABLE IF NOT EXISTS {{silver}}.payroll_tds_register (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id        uuid NOT NULL,
    gstin            platform_ref.gstin NOT NULL,
    tax_period       date NOT NULL,
    doc_type_code    text NOT NULL DEFAULT 'PAYROLL_TDS_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'PAYROLL_TDS_REGISTER'),
    batch_id         uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash         text NOT NULL,

    -- The employee/contractor's own ref (EMP0231, CONS0012, ...) — the
    -- payslip run's identifier for this person, not ours.
    ref_no           text NOT NULL,
    person_name      text NOT NULL,
    role_title       text,
    -- Employee vs Contractor/Professional is THE determinant this register
    -- exists to carry (module docstring: TDS s.192 = employer-employee,
    -- s.194J/194C = professional/contractor, relevant to RCM on
    -- non-executive sitting fees). NOT a fixed enum: the real specimen's
    -- own "Classification" column carries "Employee", "Contractor" AND
    -- "Professional" — a CHECK narrower than the source register's actual
    -- vocabulary would reject a legitimate row, so this is free text, same
    -- as note_type is a real fixed enum but this genuinely is not.
    classification   text NOT NULL,
    tds_section      text NOT NULL,
    gross_amount     platform_ref.money_inr NOT NULL,
    tds_deducted     platform_ref.money_inr NOT NULL,

    valid_from       date NOT NULL,
    valid_to         date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    superseded_at    timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL DEFAULT current_user,
    modified_at      timestamptz,
    modified_by      text,

    CONSTRAINT payroll_tds_register_window CHECK (valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS payroll_tds_register_natural_key_uq
    ON {{silver}}.payroll_tds_register (entity_id, gstin, tax_period, ref_no)
    WHERE superseded_at IS NULL;

CREATE OR REPLACE VIEW {{silver}}.v1_payroll_tds_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id,
       row_hash, ref_no, person_name, role_title, classification, tds_section,
       gross_amount, tds_deducted, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.payroll_tds_register;


-- --- FIRC_BRC_REGISTER (firc_brc_register) ----------------------------------
CREATE TABLE IF NOT EXISTS {{silver}}.firc_brc_register (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id        uuid NOT NULL,
    gstin            platform_ref.gstin NOT NULL,
    tax_period       date NOT NULL,
    doc_type_code    text NOT NULL DEFAULT 'FIRC_BRC_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'FIRC_BRC_REGISTER'),
    batch_id         uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash         text NOT NULL,

    export_invoice_no text NOT NULL,
    invoice_date       date NOT NULL,
    -- Foreign-currency amount, kept as text with its currency code inline
    -- ("USD 1,85,000") rather than platform_ref.money_inr — the register's
    -- whole point is realisation of a NON-rupee receipt; forcing it through
    -- an INR-scaled domain would either truncate the currency or require an
    -- FX rate this register does not carry.
    amount_foreign     text NOT NULL,
    ad_bank            text NOT NULL,
    -- NULL is a real, legitimate value here: "Pending as on 31-Mar-2025" has
    -- no FIRC number yet — the 12-month realisation window (A7.01) is still
    -- open, not a missing fact.
    firc_no            text,
    realisation_status text NOT NULL,

    valid_from       date NOT NULL,
    valid_to         date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    superseded_at    timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL DEFAULT current_user,
    modified_at      timestamptz,
    modified_by      text,

    CONSTRAINT firc_brc_register_window CHECK (valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS firc_brc_register_natural_key_uq
    ON {{silver}}.firc_brc_register (entity_id, gstin, tax_period, export_invoice_no)
    WHERE superseded_at IS NULL;

CREATE OR REPLACE VIEW {{silver}}.v1_firc_brc_register AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id,
       row_hash, export_invoice_no, invoice_date, amount_foreign, ad_bank, firc_no,
       realisation_status, valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.firc_brc_register;


-- --- FORM_15CA_15CB (form_15ca_15cb) ----------------------------------------
CREATE TABLE IF NOT EXISTS {{silver}}.form_15ca_15cb (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id        uuid NOT NULL,
    gstin            platform_ref.gstin NOT NULL,
    tax_period       date NOT NULL,
    doc_type_code    text NOT NULL DEFAULT 'FORM_15CA_15CB'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'FORM_15CA_15CB'),
    batch_id         uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,
    row_hash         text NOT NULL,

    form_15ca_ack_no    text NOT NULL,
    remittee_name       text NOT NULL,
    remittance_amount_inr platform_ref.money_inr,
    nature_of_remittance  text,
    form_15cb_cert_no     text,
    -- The CA's classification of the payment (royalty/FTS vs. reimbursement
    -- vs. goods, ...) — the module docstring's "key input into RCM
    -- applicability and treaty-position determination".
    classification         text,
    tax_withheld_inr        platform_ref.money_inr,

    valid_from       date NOT NULL,
    valid_to         date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    superseded_at    timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL DEFAULT current_user,
    modified_at      timestamptz,
    modified_by      text,

    CONSTRAINT form_15ca_15cb_window CHECK (valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS form_15ca_15cb_natural_key_uq
    ON {{silver}}.form_15ca_15cb (entity_id, gstin, tax_period, form_15ca_ack_no)
    WHERE superseded_at IS NULL;

CREATE OR REPLACE VIEW {{silver}}.v1_form_15ca_15cb AS
SELECT id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id,
       row_hash, form_15ca_ack_no, remittee_name, remittance_amount_inr,
       nature_of_remittance, form_15cb_cert_no, classification, tax_withheld_inr,
       valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.form_15ca_15cb;
