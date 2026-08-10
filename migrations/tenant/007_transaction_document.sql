-- =============================================================================
-- Tenant migration 007 — Archetype 1: the transaction document.
--
-- Eleven in-scope document types, one table pair. Sales register, purchase
-- register, credit/debit note register, HO common input service invoice, GTA
-- consignment note, Bill of Entry (client and ICEGATE), e-invoice IRN register,
-- e-way bill register, shipping bill, SEZ Bill of Entry.
--
-- WHAT THIS REPLACES. Migration 002 created purchase_invoice and
-- purchase_invoice_line — a deliberate vertical slice, chosen for the skeleton
-- because it exercises the line-item path. Generalising it now rather than
-- copying it ten more times is the rule ARCHITECTURE.md 6 states plainly:
-- adding the twelfth type must be a registry row, not a sprint, and the moment
-- it becomes a sprint the archetype abstraction has failed.
--
-- The v1_ read contract is UNCHANGED. v1_purchase_invoice and
-- v1_purchase_invoice_line keep their exact column lists and are redefined over
-- the new tables, so inafinplatform/v2 sees nothing (ARCHITECTURE.md 2.4 — the
-- view is the contract precisely so Pipeline 1 can do this).
--
-- WHAT VARIES PER TYPE lives in platform_ref.document_type.field_contract
-- (shared 009), not here. This table is the part that does not vary.
-- =============================================================================

-- --- Header -----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {{silver}}.transaction_document (
    doc_id          uuid PRIMARY KEY,
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    entity_id       uuid NOT NULL,

    -- The ARTEFACT type, and only an archetype-1 one. Same CHECK-pinned
    -- composite FK as entitlement_instrument (tenant 006): the archetype is
    -- pinned to the constant 1, so the registry decides membership and storing
    -- a GSTR-1 here is a foreign key violation rather than a review comment.
    --
    -- Registry codes only. Migration 008 settles why: PURCHASE_REGISTER, not
    -- PURCHASE_INVOICE.
    doc_type        text NOT NULL,
    doc_archetype   smallint NOT NULL DEFAULT 1
        CHECK (doc_archetype = 1),

    -- Materialised from the type's field contract at write time, not joined at
    -- read time. See the note in shared 008: this records what we understood
    -- the direction to be when we ingested, which is what keeps a historical
    -- report reproducible after a contract correction.
    direction       text NOT NULL,
    direction_vt    text NOT NULL DEFAULT 'Direction'
        CHECK (direction_vt = 'Direction'),

    doc_number      text NOT NULL,
    doc_date        date NOT NULL,

    -- NULLABLE, and the contract decides whether that is legitimate:
    --   REQUIRED  registered supplier on a purchase register
    --   OPTIONAL  B2C sale, unregistered GTA — absence is a FACT, not a gap
    --   FOREIGN   import BoE, export shipping bill — there is no GSTIN to have
    -- Enforcing "always present" here would reject every valid import; leaving
    -- it unconstrained would let a purchase register lose its supplier and
    -- still pass. The distinction is per document type, so it lives in the
    -- registry and is enforced in src/silver/transaction.py.
    counterparty_gstin text
        CHECK (counterparty_gstin IS NULL
               OR counterparty_gstin ~ '^[0-9]{2}[A-Z0-9]{13}$'),
    counterparty_name  text,

    total_taxable_value numeric(18, 2) NOT NULL,
    total_tax_value     numeric(18, 2) NOT NULL,
    total_value         numeric(18, 2) NOT NULL,
    currency            text NOT NULL DEFAULT 'INR',

    -- Everything the contract declares beyond the fixed shape: irn, ewb_number,
    -- be_number, port_code, original_doc_number. JSONB because it differs per
    -- type and is read after the indexed predicates have cut the candidate set
    -- — the same reasoning as entitlement_instrument.scope (tenant 006).
    attributes      jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- Bitemporal (ARCHITECTURE.md 8). Nothing is updated in place.
    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,

    bronze_ingest_id uuid NOT NULL,

    CONSTRAINT transaction_document_window CHECK (valid_to > valid_from),
    CONSTRAINT transaction_document_totals
        CHECK (total_value = total_taxable_value + total_tax_value),

    FOREIGN KEY (doc_type, doc_archetype)
        REFERENCES platform_ref.document_type (doc_type_code, archetype),
    FOREIGN KEY (direction_vt, direction)
        REFERENCES platform_ref.universal_master (value_type, value)
);

-- One live version per (entity, type, counterparty, number).
--
-- COALESCE, not the bare column: NULLs are DISTINCT in a unique index, so two
-- B2C invoices sharing a number would both be accepted and the duplicate would
-- surface as a doubled turnover figure. Collapsing NULL to '' makes "no
-- counterparty" one value that collides with itself, which is the intent.
CREATE UNIQUE INDEX IF NOT EXISTS transaction_document_natural_key_uq
    ON {{silver}}.transaction_document
       (entity_id, doc_type, COALESCE(counterparty_gstin, ''), doc_number)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS transaction_document_batch_idx
    ON {{silver}}.transaction_document (batch_id);

-- The period sweep Pipeline 2 runs: everything of one direction in a date
-- range. Partial on the current version for the same reason as tenant 006 —
-- a superseded row is never the answer to "what do we believe now".
CREATE INDEX IF NOT EXISTS transaction_document_period_idx
    ON {{silver}}.transaction_document (entity_id, direction, doc_date)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.transaction_document IS
    'Archetype 1 (ARCHITECTURE.md 6). Eleven document types collapse here. What '
    'varies per type is platform_ref.document_type.field_contract, not a column '
    '— if a new transaction type needs a schema change, the archetype has failed.';


-- --- Lines ------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {{silver}}.transaction_line (
    line_id         uuid PRIMARY KEY,
    doc_id          uuid NOT NULL
                        REFERENCES {{silver}}.transaction_document (doc_id) ON DELETE CASCADE,
    line_number     integer NOT NULL CHECK (line_number > 0),

    hsn_sac         text NOT NULL CHECK (hsn_sac ~ '^[0-9]{4,8}$'),
    description     text,
    quantity        numeric(18, 3) NOT NULL,
    unit_of_measure text,
    unit_price      numeric(18, 4) NOT NULL,
    taxable_value   numeric(18, 2) NOT NULL,
    gst_rate        numeric(5, 2) NOT NULL CHECK (gst_rate >= 0 AND gst_rate <= 100),

    cgst_amount     numeric(18, 2) NOT NULL DEFAULT 0,
    sgst_amount     numeric(18, 2) NOT NULL DEFAULT 0,
    igst_amount     numeric(18, 2) NOT NULL DEFAULT 0,
    -- Compensation cess. Absent from purchase_invoice, and its absence would
    -- have silently understated the tax on every cess-bearing line — coal,
    -- tobacco, motor vehicles. A column, not an attribute, because it is part
    -- of the tax split every type shares.
    cess_amount     numeric(18, 2) NOT NULL DEFAULT 0,
    itc_amount      numeric(18, 2) NOT NULL DEFAULT 0,

    attributes      jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- Structural only: a line is intra-state or inter-state, never both.
    -- WHETHER the head is correct is anomaly T3, decided by v2 against
    -- re-derived place of supply. Law does not belong in the ingestion path.
    CONSTRAINT transaction_line_tax_head CHECK (
        (igst_amount > 0 AND cgst_amount = 0 AND sgst_amount = 0)
        OR (igst_amount = 0)
    ),
    UNIQUE (doc_id, line_number)
);

CREATE INDEX IF NOT EXISTS transaction_line_hsn_idx
    ON {{silver}}.transaction_line (hsn_sac);

CREATE INDEX IF NOT EXISTS transaction_line_doc_idx
    ON {{silver}}.transaction_line (doc_id);


-- --- Backfill ---------------------------------------------------------------
-- Phase 1 rows carry doc_type 'PURCHASE_INVOICE', which is a ROW type and not a
-- registry code. Migration 008 settles that; here it becomes the artefact type
-- the register actually names. The FK would reject anything else, so a
-- mis-mapped backfill cannot commit.

INSERT INTO {{silver}}.transaction_document (
    doc_id, batch_id, entity_id, doc_type, direction,
    doc_number, doc_date, counterparty_gstin,
    total_taxable_value, total_tax_value, total_value, currency,
    attributes, valid_from, valid_to, recorded_at, superseded_at,
    bronze_ingest_id
)
SELECT
    i.invoice_id, i.batch_id, i.entity_id, 'PURCHASE_REGISTER', 'INWARD',
    i.invoice_number, i.invoice_date, i.supplier_gstin,
    i.total_taxable_value, i.total_tax_value, i.total_value, i.currency,
    -- payment_due_date is a PURCHASE_REGISTER contract attribute, so it moves
    -- from column to attribute. jsonb_strip_nulls, not to_jsonb: a NULL due
    -- date must be an ABSENT key, or the contract check would see the key
    -- present and treat an unknown date as a supplied one.
    jsonb_strip_nulls(jsonb_build_object('payment_due_date', i.payment_due_date)),
    i.valid_from, i.valid_to, i.recorded_at, i.superseded_at,
    i.bronze_ingest_id
FROM {{silver}}.purchase_invoice i
ON CONFLICT (doc_id) DO NOTHING;

INSERT INTO {{silver}}.transaction_line (
    line_id, doc_id, line_number, hsn_sac, description, quantity,
    unit_of_measure, unit_price, taxable_value, gst_rate,
    cgst_amount, sgst_amount, igst_amount, itc_amount
)
SELECT
    l.line_id, l.invoice_id, l.line_number, l.hsn_sac, l.description, l.quantity,
    l.unit_of_measure, l.unit_price, l.taxable_value, l.gst_rate,
    l.cgst_amount, l.sgst_amount, l.igst_amount, l.itc_amount
FROM {{silver}}.purchase_invoice_line l
ON CONFLICT (line_id) DO NOTHING;

-- Backfill completeness, asserted rather than assumed. A silently partial
-- backfill would present as missing invoices in a reconciliation months later,
-- with nothing left to point at. DDL is transactional in Postgres, so raising
-- here rolls the whole migration back and the tenant stays at 006.
DO $$
DECLARE
    v_src_docs  bigint;
    v_dst_docs  bigint;
    v_src_lines bigint;
    v_dst_lines bigint;
BEGIN
    SELECT count(*) INTO v_src_docs  FROM {{silver}}.purchase_invoice;
    SELECT count(*) INTO v_src_lines FROM {{silver}}.purchase_invoice_line;
    SELECT count(*) INTO v_dst_docs
      FROM {{silver}}.transaction_document WHERE doc_type = 'PURCHASE_REGISTER';
    SELECT count(*) INTO v_dst_lines
      FROM {{silver}}.transaction_line tl
      JOIN {{silver}}.transaction_document td ON td.doc_id = tl.doc_id
     WHERE td.doc_type = 'PURCHASE_REGISTER';

    IF v_src_docs <> v_dst_docs OR v_src_lines <> v_dst_lines THEN
        RAISE EXCEPTION
            'backfill incomplete: % of % documents, % of % lines',
            v_dst_docs, v_src_docs, v_dst_lines, v_src_lines;
    END IF;
END $$;


-- --- Read contract ----------------------------------------------------------
-- Column lists are BYTE-FOR-BYTE what migration 004 published. CREATE OR
-- REPLACE VIEW enforces that itself — it refuses a changed column list — so
-- this statement failing IS the alarm that the v2 contract was about to break.
--
-- Definer rights. Do NOT add security_invoker = true (tenant 004 explains why).

CREATE OR REPLACE VIEW {{silver}}.v1_purchase_invoice AS
SELECT
    doc_id            AS invoice_id,
    batch_id,
    entity_id,
    doc_number        AS invoice_number,
    counterparty_gstin AS supplier_gstin,
    doc_date          AS invoice_date,
    total_taxable_value,
    total_tax_value,
    total_value,
    (attributes ->> 'payment_due_date')::date AS payment_due_date,
    currency,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    bronze_ingest_id
FROM {{silver}}.transaction_document
WHERE doc_type = 'PURCHASE_REGISTER';


CREATE OR REPLACE VIEW {{silver}}.v1_purchase_invoice_line AS
SELECT
    l.line_id,
    l.doc_id AS invoice_id,
    d.batch_id,
    l.line_number,
    l.hsn_sac,
    l.description,
    l.quantity,
    l.unit_of_measure,
    l.unit_price,
    l.taxable_value,
    l.gst_rate,
    l.cgst_amount,
    l.sgst_amount,
    l.igst_amount,
    l.itc_amount
FROM {{silver}}.transaction_line l
JOIN {{silver}}.transaction_document d ON d.doc_id = l.doc_id
WHERE d.doc_type = 'PURCHASE_REGISTER';


-- The generalised contract. v2 migrates to these at its own pace; the two views
-- above exist so it does not have to do so today.
CREATE OR REPLACE VIEW {{silver}}.v1_transaction_document AS
SELECT
    doc_id,
    batch_id,
    entity_id,
    doc_type,
    direction,
    doc_number,
    doc_date,
    counterparty_gstin,
    counterparty_name,
    total_taxable_value,
    total_tax_value,
    total_value,
    currency,
    attributes,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at,
    bronze_ingest_id
FROM {{silver}}.transaction_document;


CREATE OR REPLACE VIEW {{silver}}.v1_transaction_line AS
SELECT
    l.line_id,
    l.doc_id,
    d.batch_id,
    d.doc_type,
    d.direction,
    l.line_number,
    l.hsn_sac,
    l.description,
    l.quantity,
    l.unit_of_measure,
    l.unit_price,
    l.taxable_value,
    l.gst_rate,
    l.cgst_amount,
    l.sgst_amount,
    l.igst_amount,
    l.cess_amount,
    l.itc_amount,
    l.attributes
FROM {{silver}}.transaction_line l
JOIN {{silver}}.transaction_document d ON d.doc_id = l.doc_id;


-- --- Retire the superseded tables -------------------------------------------
-- Safe only because the views above no longer reference them. Dropped rather
-- than left in place: two tables holding the same purchase invoices, one of
-- them silently stale after the next ingest, is the state that produces two
-- different answers to one question.
DROP TABLE IF EXISTS {{silver}}.purchase_invoice_line;
DROP TABLE IF EXISTS {{silver}}.purchase_invoice;
