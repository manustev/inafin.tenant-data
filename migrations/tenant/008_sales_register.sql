-- =============================================================================
-- Tenant migration 008 — A1.01 SALES_REGISTER, the first typed table.
--
-- TYPED-TABLES-PLAN.md build order step 3. This is the REFERENCE PATTERN: it is
-- meant to be reviewed and then replicated across the other 23 A1 types, so the
-- reasoning is written out here rather than in each copy.
--
-- What it is not: it does not retire transaction_document (that is step 4, with
-- PURCHASE_REGISTER), and it does not touch v1_purchase_invoice. Migration 007
-- stays applied and untouched — checksum pinning means reversal is new
-- migrations, never edits.
--
-- Column provenance, stated because it matters to the review. The envelope is
-- TYPED-TABLES-PLAN.md 4. The business columns are derived from the registry row
-- (A1.01, direction OUTWARD, counterparty OPTIONAL) and from
-- document_type.field_contract, which named place_of_supply and invoice_type as
-- the two type-specific fields. The customer's reference schema was NOT
-- available in this workspace when this was written; every column below that is
-- not in the envelope or the field contract is INFERRED from GSTR-1's invoice
-- classification, and is the part to check against the reference first.
-- =============================================================================

-- --- Header -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {{silver}}.sales_register (
    -- Identity and scope ------------------------------------------------------
    --
    -- bigint identity, not uuid. Rows arrive in file order and are read in
    -- ranges; the archetype tables used client-generated uuids because the
    -- promoter needed the id before insert, which a header/line split no longer
    -- requires (the line insert reads the header's returned id).
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Business scope WITHIN the tenant, never the isolation boundary
    -- (ARCHITECTURE.md 6, TYPED-TABLES-PLAN.md 2). Isolation is GRANT.
    --
    -- entity_id, not the reference schema's client_id: this repo's tenant is
    -- the client and entity_id is the legal entity inside it, which is what
    -- ingest_batch already carries and what Pipeline 2 already reads.
    entity_id           uuid NOT NULL,

    -- The registration the register belongs to. An entity holds many GSTINs and
    -- invoice number series are per registration, so both columns are load-
    -- bearing in the natural key below.
    gstin               platform_ref.gstin NOT NULL,

    tax_period          date NOT NULL,

    -- Lineage: row -> batch -> artefact -> object store ------------------------
    --
    -- Pinned to the constant. Same reasoning as the composite FK on
    -- transaction_document: the table's membership is decided by the registry,
    -- so putting a credit note in the sales register is a constraint violation
    -- rather than a review comment. Here it is simpler — one table, one type.
    doc_type_code       text NOT NULL DEFAULT 'SALES_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'SALES_REGISTER'),

    batch_id            uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),

    -- Carried on the row, not reached through batch_id (TYPED-TABLES-PLAN.md 4).
    -- One hop from any row to the artefact under Object Lock, and the chain
    -- survives if batch bookkeeping is ever reorganised.
    bronze_ingest_id    uuid NOT NULL,

    -- Content identity --------------------------------------------------------
    --
    -- row_hash covers the HEADER's business fields. document_hash covers
    -- row_hash plus every line's row_hash in line order.
    --
    -- Both, and this is a gap in TYPED-TABLES-PLAN.md 5 rather than a flourish.
    -- The plan makes resubmission a row-level upsert keyed on row_hash — but a
    -- corrected line under an unchanged header produces an IDENTICAL header
    -- hash, so a header-only hash would no-op the resubmission and silently keep
    -- the stale line. The supersession decision therefore compares
    -- document_hash; row_hash is kept because it answers the narrower question
    -- ("did the header itself change") that a diff needs.
    row_hash            text NOT NULL,
    document_hash       text NOT NULL,

    -- Business ----------------------------------------------------------------

    invoice_no          text NOT NULL,
    invoice_date        date NOT NULL,

    -- Named by the field contract. Vocabulary seeded in shared migration 011,
    -- with the CHECK-pinned _vt composite FK used everywhere else
    -- (Category B Pattern 1).
    invoice_type        text NOT NULL,
    invoice_type_vt     text NOT NULL DEFAULT 'Invoice_Type'
        CHECK (invoice_type_vt = 'Invoice_Type'),

    -- Two-digit state code. Structural only: WHICH code is correct for a given
    -- supply is anomaly T3, decided downstream against re-derived place of
    -- supply, and the authoritative code list belongs to the corpus.
    place_of_supply     text NOT NULL CHECK (place_of_supply ~ '^[0-9]{2}$'),

    -- NULLABLE, and the absence is a FACT, not a gap. The field contract records
    -- counterparty=OPTIONAL for this type: a B2C sale has no customer GSTIN, and
    -- a NOT NULL here would reject every retail invoice.
    customer_gstin      platform_ref.gstin,
    customer_name       text,

    reverse_charge      boolean NOT NULL DEFAULT FALSE,

    -- Present when the supply was made through an operator (s.52 TCS).
    ecommerce_gstin     platform_ref.gstin,

    currency            text NOT NULL DEFAULT 'INR',

    -- Totals, AS SUPPLIED BY THE ERP ------------------------------------------
    --
    -- Deliberately UNCONSTRAINED, and this is the reason the header/line split
    -- exists at all (TYPED-TABLES-PLAN.md 6).
    --
    -- transaction_document carried CHECK (total_value = taxable + tax). That
    -- constraint rejects the one row worth having: an ERP export whose header
    -- total disagrees with its own tax breakdown, or with the sum of its lines,
    -- is a FINDING. Rejecting it at ingestion destroys the evidence and reports
    -- the file as unparseable; reconciling it silently at load is worse. It is
    -- stored exactly as supplied and Pipeline 2 does the comparison.
    total_taxable_value platform_ref.money_inr NOT NULL,
    total_cgst          platform_ref.money_inr NOT NULL DEFAULT 0,
    total_sgst          platform_ref.money_inr NOT NULL DEFAULT 0,
    total_igst          platform_ref.money_inr NOT NULL DEFAULT 0,
    total_cess          platform_ref.money_inr NOT NULL DEFAULT 0,
    total_invoice_value platform_ref.money_inr NOT NULL,

    -- Bitemporal (ARCHITECTURE.md 8) -------------------------------------------
    valid_from          date NOT NULL,
    valid_to            date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    superseded_at       timestamptz,

    -- Audit --------------------------------------------------------------------
    -- current_user is the tenant's ingest role, set by SET LOCAL ROLE. Defaulted
    -- rather than passed in, so it records who the database saw rather than who
    -- the application claimed.
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text NOT NULL DEFAULT current_user,
    modified_at         timestamptz,
    modified_by         text,

    CONSTRAINT sales_register_window CHECK (valid_to > valid_from),

    -- A closed row has both marks or neither. Half-closed is the state that
    -- makes "what do we believe now" ambiguous.
    CONSTRAINT sales_register_supersession CHECK (
        (superseded_at IS NULL AND modified_at IS NULL)
        OR superseded_at IS NOT NULL
    ),

    FOREIGN KEY (invoice_type_vt, invoice_type)
        REFERENCES platform_ref.universal_master (value_type, value)
);

-- The natural key. TYPED-TABLES-PLAN.md 5: this is what moves idempotency from
-- the batch to the row and closes the resubmission gap.
--
-- tax_period is deliberately EXCLUDED. An invoice number must be unique for a
-- registration regardless of which period's file carried it, so INV-4471
-- appearing in both the April and May exports is caught as the ERP duplicate it
-- is. That exclusion is also why this table must not be partitioned by
-- tax_period — a unique index on a partitioned table must include every
-- partition key column (TODO.md, "Partitioning: decided NOT to partition").
--
-- Partial on the live version, so superseded rows accumulate freely.
CREATE UNIQUE INDEX IF NOT EXISTS sales_register_natural_key_uq
    ON {{silver}}.sales_register (entity_id, gstin, invoice_no)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS sales_register_batch_idx
    ON {{silver}}.sales_register (batch_id);

COMMENT ON TABLE {{silver}}.sales_register IS
    'A1.01 SALES_REGISTER. One row per invoice. Header totals are stored as '
    'supplied and never reconciled against the line sum — the disagreement is a '
    'finding, and Pipeline 2 owns it (TYPED-TABLES-PLAN.md 6).';


-- --- Lines --------------------------------------------------------------------
--
-- NO bitemporal columns, on purpose. A line's currency is its header's: closing
-- a header inserts a NEW header with a new id and a fresh set of lines, so the
-- superseded lines stay attached to the superseded header and are reachable
-- exactly as they were recorded. Duplicating superseded_at here would create a
-- second, independently-settable answer to one question.

CREATE TABLE IF NOT EXISTS {{silver}}.sales_register_line (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    header_id       bigint NOT NULL
                        REFERENCES {{silver}}.sales_register (id) ON DELETE CASCADE,
    line_number     integer NOT NULL CHECK (line_number > 0),

    row_hash        text NOT NULL,

    hsn_sac         text NOT NULL CHECK (hsn_sac ~ '^[0-9]{4,8}$'),
    description     text,
    quantity        platform_ref.qty,
    unit_of_measure text,
    unit_price      numeric(18, 4),
    taxable_value   platform_ref.money_inr NOT NULL,
    gst_rate        platform_ref.tax_rate NOT NULL,

    cgst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    sgst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    igst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    cess_amount     platform_ref.money_inr NOT NULL DEFAULT 0,

    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    -- Structural only: a line is intra-state or inter-state, never both.
    -- WHETHER the head is correct is anomaly T3, decided downstream against
    -- re-derived place of supply. Law does not belong in the ingestion path.
    CONSTRAINT sales_register_line_tax_head CHECK (
        (igst_amount > 0 AND cgst_amount = 0 AND sgst_amount = 0)
        OR (igst_amount = 0)
    ),

    CONSTRAINT sales_register_line_natural_key UNIQUE (header_id, line_number)
);

CREATE INDEX IF NOT EXISTS sales_register_line_header_idx
    ON {{silver}}.sales_register_line (header_id);

COMMENT ON TABLE {{silver}}.sales_register_line IS
    'A1.01 SALES_REGISTER lines. Currency is inherited from the header: a '
    'superseded header keeps its own lines, so there is no superseded_at here.';


-- --- Read contract --------------------------------------------------------------
--
-- COMMON CORE ONLY. Per-tenant extension columns (migrations/tenant_ext/) live
-- BELOW this line and must never be added to a v1_ view — otherwise
-- inafinplatform/v2 sees a different shape per tenant and the view stops being a
-- contract (TYPED-TABLES-PLAN.md 7).
--
-- Definer rights. Do NOT add security_invoker = true (tenant 004 explains why).
--
-- Not filtered to superseded_at IS NULL, matching v1_purchase_invoice. The
-- column is exposed and the choice is v2's: filtering here would make a batch's
-- rows vanish from the view the moment they were superseded, which breaks
-- reproducing what a past run saw.

CREATE OR REPLACE VIEW {{silver}}.v1_sales_register AS
SELECT
    id,
    entity_id,
    gstin,
    tax_period,
    doc_type_code,
    batch_id,
    bronze_ingest_id,
    row_hash,
    document_hash,
    invoice_no,
    invoice_date,
    invoice_type,
    place_of_supply,
    customer_gstin,
    customer_name,
    reverse_charge,
    ecommerce_gstin,
    currency,
    total_taxable_value,
    total_cgst,
    total_sgst,
    total_igst,
    total_cess,
    total_invoice_value,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
FROM {{silver}}.sales_register;


CREATE OR REPLACE VIEW {{silver}}.v1_sales_register_line AS
SELECT
    l.id,
    l.header_id,
    h.batch_id,
    h.entity_id,
    l.line_number,
    l.row_hash,
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
    -- Carried through so a consumer reading lines alone can still tell live from
    -- superseded without joining back for it.
    h.superseded_at
FROM {{silver}}.sales_register_line l
JOIN {{silver}}.sales_register h ON h.id = l.header_id;
