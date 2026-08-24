-- =============================================================================
-- Tenant migration 027 — B1.05 GSTR_3B, the second archetype-5 typed table.
--
-- Follows 026_gstr_2b.sql's pattern: own table set, not a shared header
-- (TYPED-TABLES-PLAN.md 8), sales_register (008)'s envelope/bitemporal
-- idiom.
--
-- SHAPE, CONFIRMED FROM THE REAL PAYLOAD, NOT ASSUMED. GSTR-3B is genuinely
-- different from GSTR-2B: a summary form of FIXED BOXES, not a grouped
-- invoice list. Two kinds of box:
--
--   1. Single-valued boxes (Table 3.1's five sub-items, Table 4(C)'s net
--      ITC, Table 6's interest/late-fee) — each becomes its OWN typed
--      column on the header, because there is exactly one of each per
--      return. No child table earns its keep for a value that never repeats.
--   2. Small fixed-vocabulary LISTS keyed by a `ty` code (Table 4(A)/4(B)/
--      4(D)'s ITC availed/reversed/ineligible, each 2-6 rows; Table 5's
--      inward supply, 2 rows) — these become child tables
--      (`gstr_3b_itc_detail`, `gstr_3b_inward_supply`), the same shape
--      `gstr_2b_itc_summary` already established, rather than one column
--      per (box, ty) combination on the header (would be ~40 columns for a
--      closed, small vocabulary — the child-table shape scales better and
--      is exactly as typed).
--
-- `inter_sup` (Table 3.2 — interstate supply to unregistered/composition/
-- UIN holders, POS-wise) is NOT modelled. All 31 real specimens in
-- `reference/B-Documents/` carry EMPTY `unreg_details`/`comp_details`/
-- `uin_details` arrays — the identical situation `026_gstr_2b.sql` names
-- for `cdnr`, and resolved the same way: `Gstr3bParser` rejects rather than
-- guesses if a real payload ever has one. See
-- src/silver/gstn_returns/gstr3b.py.
-- =============================================================================

-- --- Header -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {{silver}}.gstr_3b (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    entity_id                uuid NOT NULL,
    gstin                    platform_ref.gstin NOT NULL,
    tax_period               date NOT NULL,

    doc_type_code            text NOT NULL DEFAULT 'GSTR_3B'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'GSTR_3B'),

    batch_id                 uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id         uuid NOT NULL,

    row_hash                 text NOT NULL,
    document_hash            text NOT NULL,

    -- Filed, not auto-generated (unlike GSTR_2B) — this type genuinely has
    -- these three facts.
    arn                      text,
    filing_date              date,
    filing_status            text,
    filing_status_vt         text NOT NULL DEFAULT 'Gstr3b_Filing_Status'
        CHECK (filing_status_vt = 'Gstr3b_Filing_Status'),

    -- Table 3.1(a) — outward taxable supplies (other than zero-rated, nil,
    -- exempt).
    osup_det_taxable_value   platform_ref.money_inr NOT NULL DEFAULT 0,
    osup_det_igst            platform_ref.money_inr NOT NULL DEFAULT 0,
    osup_det_cgst            platform_ref.money_inr NOT NULL DEFAULT 0,
    osup_det_sgst            platform_ref.money_inr NOT NULL DEFAULT 0,
    osup_det_cess            platform_ref.money_inr NOT NULL DEFAULT 0,

    -- Table 3.1(b) — zero-rated (export/SEZ). No CGST/SGST box in the real
    -- payload: zero-rated supply is IGST-only or nil by construction.
    osup_zero_taxable_value  platform_ref.money_inr NOT NULL DEFAULT 0,
    osup_zero_igst           platform_ref.money_inr NOT NULL DEFAULT 0,
    osup_zero_cess           platform_ref.money_inr NOT NULL DEFAULT 0,

    -- Table 3.1(c) — nil-rated / exempt. Taxable value only, by construction.
    osup_nil_exempt_taxable_value platform_ref.money_inr NOT NULL DEFAULT 0,

    -- Table 3.1(d) — inward supply liable to reverse charge (becomes an
    -- OUTPUT liability for the recipient, hence it lives in Table 3.1, not 4).
    isup_rev_taxable_value   platform_ref.money_inr NOT NULL DEFAULT 0,
    isup_rev_igst            platform_ref.money_inr NOT NULL DEFAULT 0,
    isup_rev_cgst            platform_ref.money_inr NOT NULL DEFAULT 0,
    isup_rev_sgst            platform_ref.money_inr NOT NULL DEFAULT 0,
    isup_rev_cess            platform_ref.money_inr NOT NULL DEFAULT 0,

    -- Table 3.1(e) — non-GST outward supply. Taxable value only.
    osup_nongst_taxable_value platform_ref.money_inr NOT NULL DEFAULT 0,

    -- Table 4(C) — net ITC available (avl - rev), GSTN's own computed figure,
    -- stored as reported rather than recomputed from gstr_3b_itc_detail, same
    -- "as supplied" reasoning sales_register's header totals already use.
    itc_net_igst             platform_ref.money_inr NOT NULL DEFAULT 0,
    itc_net_cgst             platform_ref.money_inr NOT NULL DEFAULT 0,
    itc_net_sgst             platform_ref.money_inr NOT NULL DEFAULT 0,
    itc_net_cess             platform_ref.money_inr NOT NULL DEFAULT 0,

    -- Table 6 — interest and late fee (intr_ltfee.intr_details in the real
    -- payload; no separate late-fee sub-box exists in the sample set).
    interest_igst            platform_ref.money_inr NOT NULL DEFAULT 0,
    interest_cgst            platform_ref.money_inr NOT NULL DEFAULT 0,
    interest_sgst            platform_ref.money_inr NOT NULL DEFAULT 0,
    interest_cess            platform_ref.money_inr NOT NULL DEFAULT 0,

    -- Bitemporal (ARCHITECTURE.md 8) -------------------------------------------
    valid_from               date NOT NULL,
    valid_to                 date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at              timestamptz NOT NULL DEFAULT now(),
    superseded_at            timestamptz,

    -- Audit --------------------------------------------------------------------
    created_at               timestamptz NOT NULL DEFAULT now(),
    created_by               text NOT NULL DEFAULT current_user,
    modified_at              timestamptz,
    modified_by              text,

    CONSTRAINT gstr_3b_window CHECK (valid_to > valid_from),
    CONSTRAINT gstr_3b_supersession CHECK (
        (superseded_at IS NULL AND modified_at IS NULL)
        OR superseded_at IS NOT NULL
    ),

    FOREIGN KEY (filing_status_vt, filing_status)
        REFERENCES platform_ref.universal_master (value_type, value)
);

CREATE UNIQUE INDEX IF NOT EXISTS gstr_3b_natural_key_uq
    ON {{silver}}.gstr_3b (entity_id, gstin, tax_period)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS gstr_3b_batch_idx
    ON {{silver}}.gstr_3b (batch_id);

COMMENT ON TABLE {{silver}}.gstr_3b IS
    'B1.05 GSTR_3B. One row per (GSTIN, tax period) filed summary return. '
    'Table 3.1/4(C)/6 single-valued boxes are columns; Table 4(A/B/D) and '
    'Table 5''s small fixed-vocabulary lists are gstr_3b_itc_detail / '
    'gstr_3b_inward_supply. Table 3.2 (inter_sup) is not modelled — see '
    'migration header.';


-- --- ITC detail (Table 4(A)/4(B)/4(D)) --------------------------------------

CREATE TABLE IF NOT EXISTS {{silver}}.gstr_3b_itc_detail (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    header_id       bigint NOT NULL
                        REFERENCES {{silver}}.gstr_3b (id) ON DELETE CASCADE,

    box             text NOT NULL,
    box_vt          text NOT NULL DEFAULT 'Gstr3b_Itc_Box'
        CHECK (box_vt = 'Gstr3b_Itc_Box'),

    itc_type        text NOT NULL,
    itc_type_vt     text NOT NULL DEFAULT 'Gstr3b_Itc_Type'
        CHECK (itc_type_vt = 'Gstr3b_Itc_Type'),

    igst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    cgst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    sgst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    cess_amount     platform_ref.money_inr NOT NULL DEFAULT 0,

    CONSTRAINT gstr_3b_itc_detail_natural_key UNIQUE (header_id, box, itc_type),

    FOREIGN KEY (box_vt, box)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (itc_type_vt, itc_type)
        REFERENCES platform_ref.universal_master (value_type, value)
);

COMMENT ON TABLE {{silver}}.gstr_3b_itc_detail IS
    'B1.05 GSTR_3B Table 4(A) itc_avl / 4(B) itc_rev / 4(D) itc_inelg, '
    'distinguished by box. AVAILED covers IMPG/IMPS/ISRC/ISD/OTH; REVERSED '
    'and INELIGIBLE cover RUL/OTH — the real payload''s own ty vocabulary.';


-- --- Inward supply (Table 5) -------------------------------------------------

CREATE TABLE IF NOT EXISTS {{silver}}.gstr_3b_inward_supply (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    header_id             bigint NOT NULL
                             REFERENCES {{silver}}.gstr_3b (id) ON DELETE CASCADE,

    supply_type           text NOT NULL,
    supply_type_vt       text NOT NULL DEFAULT 'Gstr3b_Inward_Supply_Type'
        CHECK (supply_type_vt = 'Gstr3b_Inward_Supply_Type'),

    inter_state_amount    platform_ref.money_inr NOT NULL DEFAULT 0,
    intra_state_amount    platform_ref.money_inr NOT NULL DEFAULT 0,

    CONSTRAINT gstr_3b_inward_supply_natural_key UNIQUE (header_id, supply_type),

    FOREIGN KEY (supply_type_vt, supply_type)
        REFERENCES platform_ref.universal_master (value_type, value)
);

COMMENT ON TABLE {{silver}}.gstr_3b_inward_supply IS
    'B1.05 GSTR_3B Table 5 — inward supplies from composition/exempt/nil-rated '
    'sources, GST vs non-GST, inter-state vs intra-state value.';


-- --- Read contract --------------------------------------------------------------

CREATE OR REPLACE VIEW {{silver}}.v1_gstr_3b AS
SELECT
    id, entity_id, gstin, tax_period, doc_type_code, batch_id, bronze_ingest_id,
    row_hash, document_hash, arn, filing_date, filing_status,
    osup_det_taxable_value, osup_det_igst, osup_det_cgst, osup_det_sgst, osup_det_cess,
    osup_zero_taxable_value, osup_zero_igst, osup_zero_cess,
    osup_nil_exempt_taxable_value,
    isup_rev_taxable_value, isup_rev_igst, isup_rev_cgst, isup_rev_sgst, isup_rev_cess,
    osup_nongst_taxable_value,
    itc_net_igst, itc_net_cgst, itc_net_sgst, itc_net_cess,
    interest_igst, interest_cgst, interest_sgst, interest_cess,
    valid_from, valid_to, recorded_at, superseded_at
FROM {{silver}}.gstr_3b;

CREATE OR REPLACE VIEW {{silver}}.v1_gstr_3b_itc_detail AS
SELECT
    d.id, d.header_id, h.batch_id, h.entity_id,
    d.box, d.itc_type, d.igst_amount, d.cgst_amount, d.sgst_amount, d.cess_amount,
    h.superseded_at
FROM {{silver}}.gstr_3b_itc_detail d
JOIN {{silver}}.gstr_3b h ON h.id = d.header_id;

CREATE OR REPLACE VIEW {{silver}}.v1_gstr_3b_inward_supply AS
SELECT
    s.id, s.header_id, h.batch_id, h.entity_id,
    s.supply_type, s.inter_state_amount, s.intra_state_amount,
    h.superseded_at
FROM {{silver}}.gstr_3b_inward_supply s
JOIN {{silver}}.gstr_3b h ON h.id = s.header_id;
