-- =============================================================================
-- Tenant migration 009 — sales_register reconciled against the reference schema.
--
-- `reference/inafin_a1_schema.sql` arrived 2026-08-07, after tenant 008 was
-- written and applied. 008's business columns were inferred from GSTR-1; several
-- were wrong and several were missing. This replaces them.
--
-- DROP and CREATE, not a chain of ALTERs. Fifteen ALTER statements describe a
-- diff; the target shape is what gets reviewed and replicated 23 more times, so
-- it is written out in full. Safe because the tables hold no rows — there is no
-- production tenant (all six deploy blockers in TODO.md are open, including B5).
-- If that ever stops being true, this migration is not the template to copy.
--
-- WHAT THE REFERENCE SUPPLIED — business columns and their names. The reference
-- is flat (header repeated on every line row); the header/line split is ours
-- (TYPED-TABLES-PLAN.md 6), so the same column names appear at both grains, and
-- the table says which grain you are reading.
--
-- WHAT OUR DESIGN KEEPS, because the reference has no substitute for it:
--
--   bronze_ingest_id   The reference has NO Bronze linkage at all. Without it
--                      there is no path from a Silver row to the byte-exact
--                      artefact under Object Lock, which is the evidentiary
--                      chain a CA needs in a hearing.
--   batch_id           The reference has `batch_id bigint not null` with no
--                      foreign key and no table behind it. Here it references
--                      ingest_batch, the queue of record (invariant 1).
--   bitemporal columns The reference has none — a correction overwrites, and
--                      what we believed when a return was filed is lost. That is
--                      unusable for Forensic Mode and for reproducing a past
--                      filing (ARCHITECTURE.md 8).
--   natural key        The reference keys on (client_id, gstin, tax_period,
--                      row_hash), which dedups BYTE-IDENTICAL rows only. A
--                      corrected invoice inserts a second row with nothing
--                      marking the first stale, and tax_period in the key makes
--                      the same invoice in two months' files two live rows.
--                      TYPED-TABLES-PLAN.md 5 exists because of this.
--   entity_id          Not client_id. Isolation is the schema and the GRANT
--                      (invariant 2); a client discriminator column would be a
--                      weaker duplicate of a stronger control.
--   created_by default The reference reads identity from an `app.current_user`
--                      GUC. That is supplied by the caller, so it records a
--                      claim rather than a fact — and session-scoped set_config
--                      is refused by scripts/check_static.py. Under SET LOCAL
--                      ROLE, current_user is what the database saw.
-- =============================================================================

DROP VIEW IF EXISTS {{silver}}.v1_sales_register_line;
DROP VIEW IF EXISTS {{silver}}.v1_sales_register;
DROP TABLE IF EXISTS {{silver}}.sales_register_line;
DROP TABLE IF EXISTS {{silver}}.sales_register;


-- --- Header -------------------------------------------------------------------

CREATE TABLE {{silver}}.sales_register (
    -- Envelope: identity and scope ---------------------------------------------
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id       uuid NOT NULL,
    gstin           platform_ref.gstin NOT NULL,
    tax_period      date NOT NULL,

    -- Envelope: lineage ---------------------------------------------------------
    doc_type_code   text NOT NULL DEFAULT 'SALES_REGISTER'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'SALES_REGISTER'),
    batch_id        uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id uuid NOT NULL,

    -- Envelope: content identity ------------------------------------------------
    -- row_hash covers the header's own fields; document_hash covers row_hash plus
    -- every line's row_hash in line order. Both, because a corrected LINE under
    -- an unchanged header produces an identical header hash — so a header-only
    -- hash no-ops the resubmission and the stale line survives. The upsert
    -- compares document_hash.
    row_hash        text NOT NULL,
    document_hash   text NOT NULL,

    -- Business (reference schema, ingest.sales_register) -------------------------
    invoice_no      text NOT NULL,
    invoice_date    date NOT NULL,

    -- NULL is a FACT, not a gap: B2CS has no customer GSTIN.
    customer_gstin  platform_ref.gstin,

    invoice_type    text NOT NULL,
    invoice_type_vt text NOT NULL DEFAULT 'Invoice_Type'
        CHECK (invoice_type_vt = 'Invoice_Type'),

    -- Free text in the reference; given a vocabulary here (shared 012) because an
    -- uncontrolled string column accumulates 'Goods', 'GOODS' and 'goods ' and
    -- nothing can group on it.
    supply_type     text,
    supply_type_vt  text NOT NULL DEFAULT 'Supply_Type'
        CHECK (supply_type_vt = 'Supply_Type'),

    place_of_supply text CHECK (place_of_supply ~ '^[0-9]{2}$'),
    reverse_charge  boolean NOT NULL DEFAULT FALSE,
    currency        char(3) NOT NULL DEFAULT 'INR',

    -- Invoice-level charges. Reference columns, header grain.
    trade_discount  platform_ref.money_inr DEFAULT 0,
    freight         platform_ref.money_inr DEFAULT 0,
    packing         platform_ref.money_inr DEFAULT 0,
    insurance       platform_ref.money_inr DEFAULT 0,

    -- Totals AS SUPPLIED. Unconstrained on purpose: an ERP header that disagrees
    -- with the sum of its own lines is a FINDING, and this is the only place it
    -- can be recorded. Rejecting it destroys the evidence and reports a real
    -- finding as a parse error; reconciling it at load is worse
    -- (TYPED-TABLES-PLAN.md 6).
    --
    -- NULLABLE, and the distinction matters. The reference export is flat and
    -- carries no invoice-level totals at all — only line values. So NULL here
    -- means "the ERP made no invoice-level claim", which is different from a
    -- claim of zero and different again from a claim that disagrees with the
    -- lines. Defaulting NULL to the line sum would manufacture an ERP claim that
    -- was never made, and then every invoice would agree with itself by
    -- construction.
    taxable_value   platform_ref.money_inr,
    cgst            platform_ref.money_inr DEFAULT 0,
    sgst            platform_ref.money_inr DEFAULT 0,
    igst            platform_ref.money_inr DEFAULT 0,
    cess            platform_ref.money_inr DEFAULT 0,
    total_value     platform_ref.money_inr,
    -- Generated, exactly as the reference has it. Derived rather than supplied,
    -- so it cannot disagree with its own components — unlike total_value, which
    -- is the ERP's claim and must be able to.
    total_tax       platform_ref.money_inr
        GENERATED ALWAYS AS (COALESCE(cgst,0) + COALESCE(sgst,0)
                           + COALESCE(igst,0) + COALESCE(cess,0)) STORED,

    -- e-invoice / e-way bill references. Reference columns.
    irn             text,
    ewb_no          text,

    -- Bitemporal (ARCHITECTURE.md 8). Absent from the reference entirely.
    valid_from      date NOT NULL,
    valid_to        date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz,

    -- Audit.
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    modified_at     timestamptz,
    modified_by     text,

    CONSTRAINT sales_register_window CHECK (valid_to > valid_from),
    CONSTRAINT sales_register_supersession CHECK (
        (superseded_at IS NULL AND modified_at IS NULL)
        OR superseded_at IS NOT NULL
    ),

    FOREIGN KEY (invoice_type_vt, invoice_type)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (supply_type_vt, supply_type)
        REFERENCES platform_ref.universal_master (value_type, value)
);

-- tax_period EXCLUDED on purpose — an invoice number is unique for a
-- registration regardless of which period's file carried it. Also why this table
-- must not be partitioned by tax_period: a unique index on a partitioned table
-- must include every partition key column (TODO.md, "Partitioning").
CREATE UNIQUE INDEX sales_register_natural_key_uq
    ON {{silver}}.sales_register (entity_id, gstin, invoice_no)
    WHERE superseded_at IS NULL;

CREATE INDEX sales_register_batch_idx
    ON {{silver}}.sales_register (batch_id);

-- The reference's two standard lookups, minus client_id (isolation is the
-- schema, so the tenant half of `(client_id, gstin)` is already implied).
CREATE INDEX sales_register_gstin_period_idx
    ON {{silver}}.sales_register (gstin, tax_period);

COMMENT ON TABLE {{silver}}.sales_register IS
    'A1.01 SALES_REGISTER, reconciled against reference/inafin_a1_schema.sql. '
    'One row per invoice. Header totals are stored as supplied and never '
    'reconciled against the line sum — the disagreement is a finding.';


-- --- Lines --------------------------------------------------------------------
--
-- No bitemporal columns: a line's currency is its header's. Closing a header
-- inserts a new header with a fresh set of lines, so superseded lines stay
-- attached to the superseded header and are reachable exactly as recorded.

CREATE TABLE {{silver}}.sales_register_line (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    header_id      bigint NOT NULL
                       REFERENCES {{silver}}.sales_register (id) ON DELETE CASCADE,
    line_number    integer NOT NULL CHECK (line_number > 0),
    row_hash       text NOT NULL,

    hsn_sac        text CHECK (hsn_sac IS NULL OR hsn_sac ~ '^[0-9]{4,8}$'),
    description    text,
    qty            platform_ref.qty,
    uom            text,
    unit_price     numeric(18, 4),
    taxable_value  platform_ref.money_inr NOT NULL,
    gst_rate       platform_ref.tax_rate,

    cgst           platform_ref.money_inr DEFAULT 0,
    sgst           platform_ref.money_inr DEFAULT 0,
    igst           platform_ref.money_inr DEFAULT 0,
    cess           platform_ref.money_inr DEFAULT 0,
    total_value    platform_ref.money_inr,
    total_tax      platform_ref.money_inr
        GENERATED ALWAYS AS (COALESCE(cgst,0) + COALESCE(sgst,0)
                           + COALESCE(igst,0) + COALESCE(cess,0)) STORED,

    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     text NOT NULL DEFAULT current_user,
    modified_at    timestamptz,
    modified_by    text,

    -- Structural only: a line is intra-state or inter-state, never both. WHETHER
    -- the head is correct is anomaly T3, decided downstream against re-derived
    -- place of supply. Law does not belong in the ingestion path.
    CONSTRAINT sales_register_line_tax_head CHECK (
        (COALESCE(igst,0) > 0 AND COALESCE(cgst,0) = 0 AND COALESCE(sgst,0) = 0)
        OR COALESCE(igst,0) = 0
    ),

    CONSTRAINT sales_register_line_natural_key UNIQUE (header_id, line_number)
);

CREATE INDEX sales_register_line_header_idx
    ON {{silver}}.sales_register_line (header_id);

CREATE INDEX sales_register_line_hsn_idx
    ON {{silver}}.sales_register_line (hsn_sac);

COMMENT ON TABLE {{silver}}.sales_register_line IS
    'A1.01 SALES_REGISTER lines. Column names follow the reference schema; the '
    'header/line split is ours, so the same names appear at both grains.';


-- --- Read contract --------------------------------------------------------------
--
-- COMMON CORE ONLY. Per-tenant extension columns (migrations/tenant_ext/) live
-- below this line and must never reach a v1_ view, or inafinplatform/v2 sees a
-- different shape per tenant and the view stops being a contract.
--
-- Definer rights. Do NOT add security_invoker = true (tenant 004 explains why).

CREATE VIEW {{silver}}.v1_sales_register AS
SELECT
    id, entity_id, gstin, tax_period,
    doc_type_code, batch_id, bronze_ingest_id,
    row_hash, document_hash,
    invoice_no, invoice_date, customer_gstin, invoice_type, supply_type,
    place_of_supply, reverse_charge, currency,
    trade_discount, freight, packing, insurance,
    taxable_value, cgst, sgst, igst, cess, total_value, total_tax,
    irn, ewb_no,
    valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.sales_register;


CREATE VIEW {{silver}}.v1_sales_register_line AS
SELECT
    l.id, l.header_id, h.batch_id, h.entity_id,
    l.line_number, l.row_hash,
    l.hsn_sac, l.description, l.qty, l.uom, l.unit_price,
    l.taxable_value, l.gst_rate,
    l.cgst, l.sgst, l.igst, l.cess, l.total_value, l.total_tax,
    -- Carried through so a consumer reading lines alone can tell live from
    -- superseded without joining back for it.
    h.superseded_at
FROM {{silver}}.sales_register_line l
JOIN {{silver}}.sales_register h ON h.id = l.header_id;
