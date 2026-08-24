-- =============================================================================
-- Tenant migration 026 — B1.03 GSTR_2B, the first archetype-5 typed table.
--
-- D1 (HANDOFF-2026-08-19-categoryB.md, decided 2026-08-19): typed columns,
-- narrative fields excepted. Follows tenant 008 (sales_register)'s reference
-- pattern exactly — bigint identity, doc_type_code pinned to a constant,
-- header/line split, natural key on the header, no bitemporal columns on the
-- line table (a superseded header keeps its own lines).
--
-- ONE TABLE PER REGISTRY TYPE (TYPED-TABLES-PLAN.md 8), not a shared header
-- across GSTR_1/2B/3B/9/9C — that shape was considered while designing this
-- migration and is exactly the "AMAZON_MTR/FLIPKART_GTR in one table with a
-- report_type discriminator" collapse §8 already rejected. GSTR_3B/1/9/9C each
-- get their own table set when built; nothing here names them.
--
-- WHY b2b/impg/isd SHARE gstr_2b_line, even though §8 says "one table per
-- type": these are not three registry document types, they are three
-- SECTIONS OF ONE document type's payload (`data.docdata.{b2b,impg,isd}` in
-- the real sample JSON) that always co-occur in the same file — exactly the
-- narrow exception §8 itself carves out ("collapse only where ... the types
-- always co-occur"). A `section` discriminator is the honest column for that,
-- the same way `sales_register_line` uses `line_number` for its own internal
-- structure.
--
-- CDNR IS DELIBERATELY NOT PARSED YET. `reference/B-Documents/`'s 24 real
-- GSTR-2B specimens all carry an EMPTY `cdnr` array — not one populated
-- example exists in this workspace. `Gstr2bParser` (src/silver/gstn_returns/
-- gstr2b.py) quarantines any artefact with a non-empty `cdnr` rather than
-- guess its `nt[]` shape from the b2b sibling's structure — see that module's
-- docstring. `section_vt`'s vocabulary (shared migration 033) still lists
-- CDNR as a valid value, so the column doesn't need a second migration the day
-- a real specimen arrives; only the parser needs extending.
--
-- Columns are heavily nullable across the three real sections on purpose,
-- the same "several real specimens genuinely have no clean second fact"
-- reasoning migration 006's docstring already states for entity_master_record/
-- narrative_contract: b2b carries item-level rate/itc fields impg and isd do
-- not, isd carries no taxable_value at all (ITC distribution, not a taxable
-- supply), impg carries neither cgst nor sgst (imports are IGST-only).
-- =============================================================================

-- --- Header -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {{silver}}.gstr_2b (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    entity_id           uuid NOT NULL,
    gstin               platform_ref.gstin NOT NULL,
    tax_period          date NOT NULL,

    doc_type_code       text NOT NULL DEFAULT 'GSTR_2B'
        REFERENCES platform_ref.document_type (doc_type_code)
        CHECK (doc_type_code = 'GSTR_2B'),

    batch_id            uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id    uuid NOT NULL,

    row_hash            text NOT NULL,
    document_hash       text NOT NULL,

    -- GSTN's own envelope fields (`gendt`, `version` in the real sample
    -- payload) — kept because a 2B statement is auto-generated, not filed, so
    -- there is no arn/filing_date/filing_status the way GSTR-1/3B will carry;
    -- generated_at is the closest equivalent fact this type actually has.
    generated_at        timestamptz,
    source_version      text,

    -- Bitemporal (ARCHITECTURE.md 8) -------------------------------------------
    valid_from          date NOT NULL,
    valid_to            date NOT NULL DEFAULT DATE '9999-12-31',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    superseded_at       timestamptz,

    -- Audit --------------------------------------------------------------------
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text NOT NULL DEFAULT current_user,
    modified_at         timestamptz,
    modified_by         text,

    CONSTRAINT gstr_2b_window CHECK (valid_to > valid_from),
    CONSTRAINT gstr_2b_supersession CHECK (
        (superseded_at IS NULL AND modified_at IS NULL)
        OR superseded_at IS NOT NULL
    )
);

-- One current 2B statement per GSTIN per period — a re-download (say, after a
-- supplier amendment regenerates the same period's 2B) supersedes the prior
-- row rather than creating a second live one.
CREATE UNIQUE INDEX IF NOT EXISTS gstr_2b_natural_key_uq
    ON {{silver}}.gstr_2b (entity_id, gstin, tax_period)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS gstr_2b_batch_idx
    ON {{silver}}.gstr_2b (batch_id);

COMMENT ON TABLE {{silver}}.gstr_2b IS
    'B1.03 GSTR_2B. One row per (GSTIN, tax period) auto-generated ITC '
    'statement. Detail lines are gstr_2b_line; the itcsumm block is '
    'gstr_2b_itc_summary. Do not add GSTR_1/3B/9/9C to this table — each gets '
    'its own table set (TYPED-TABLES-PLAN.md 8).';


-- --- Lines ----------------------------------------------------------------------
--
-- NO bitemporal columns, same reasoning as sales_register_line: a header
-- supersession inserts a new header id and a fresh set of lines.

CREATE TABLE IF NOT EXISTS {{silver}}.gstr_2b_line (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    header_id               bigint NOT NULL
                                REFERENCES {{silver}}.gstr_2b (id) ON DELETE CASCADE,
    line_number             integer NOT NULL CHECK (line_number > 0),

    row_hash                text NOT NULL,

    section                 text NOT NULL,
    section_vt              text NOT NULL DEFAULT 'Gstr2b_Section'
        CHECK (section_vt = 'Gstr2b_Section'),

    -- Present for B2B/ISD (ctin), NULL for IMPG — an import has no GST-
    -- registered counterparty, only a port/BoE.
    counterparty_gstin      platform_ref.gstin,
    counterparty_name       text,

    document_kind           text NOT NULL,
    document_kind_vt        text NOT NULL DEFAULT 'Gstr2b_Document_Kind'
        CHECK (document_kind_vt = 'Gstr2b_Document_Kind'),

    document_number         text NOT NULL,
    document_date           date NOT NULL,

    -- B2B only — the item-level rate-wise breakdown (`items[]` in the real
    -- payload). NULL for IMPG/ISD, which carry one amount set per row with no
    -- HSN/rate split.
    item_number              integer,
    rate                      platform_ref.tax_rate,

    -- NULL for ISD: an ISD credit distribution has no taxable value, only
    -- tax amounts to distribute.
    taxable_value             platform_ref.money_inr,

    igst_amount               platform_ref.money_inr NOT NULL DEFAULT 0,
    cgst_amount               platform_ref.money_inr NOT NULL DEFAULT 0,
    sgst_amount               platform_ref.money_inr NOT NULL DEFAULT 0,
    cess_amount               platform_ref.money_inr NOT NULL DEFAULT 0,

    -- B2B only.
    itc_available             boolean,
    itc_unavailable_reason    text,
    place_of_supply           text CHECK (place_of_supply IS NULL OR place_of_supply ~ '^[0-9]{2}$'),
    reverse_charge            boolean,

    -- Everything else the real payload carries that is narrative/supplementary
    -- rather than reconciliation-critical: irn, irngendate, diffprcnt
    -- (b2b); isamd, refdt, portcode (impg); supprd, supfileddt, supfilingmode
    -- (b2b's supplier-filing metadata); doctype (isd's own document-type
    -- string, distinct from document_kind above).
    attributes                jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at                timestamptz NOT NULL DEFAULT now(),
    created_by                text NOT NULL DEFAULT current_user,

    CONSTRAINT gstr_2b_line_natural_key UNIQUE (header_id, line_number),

    FOREIGN KEY (section_vt, section)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (document_kind_vt, document_kind)
        REFERENCES platform_ref.universal_master (value_type, value)
);

CREATE INDEX IF NOT EXISTS gstr_2b_line_header_idx
    ON {{silver}}.gstr_2b_line (header_id);

COMMENT ON TABLE {{silver}}.gstr_2b_line IS
    'B1.03 GSTR_2B detail lines — B2B/IMPG/ISD sections flattened to one row '
    'per invoice item (B2B) or per document (IMPG/ISD). CDNR is not parsed '
    'yet (see migration header) but section_vt already allows the value.';


-- --- ITC summary ------------------------------------------------------------
--
-- The `itcsumm` block: GSTN's own aggregate per (category, availability),
-- kept as GSTN reported it rather than recomputed from gstr_2b_line, so a
-- disagreement between the two is a visible finding instead of being erased
-- by construction — same reasoning sales_register's header totals are stored
-- "as supplied" and never reconciled against the line sum at ingestion.

CREATE TABLE IF NOT EXISTS {{silver}}.gstr_2b_itc_summary (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    header_id       bigint NOT NULL
                        REFERENCES {{silver}}.gstr_2b (id) ON DELETE CASCADE,

    category        text NOT NULL,
    category_vt     text NOT NULL DEFAULT 'Gstr2b_Itc_Category'
        CHECK (category_vt = 'Gstr2b_Itc_Category'),

    availability    text NOT NULL,
    availability_vt text NOT NULL DEFAULT 'Gstr2b_Itc_Availability'
        CHECK (availability_vt = 'Gstr2b_Itc_Availability'),

    igst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    cgst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    sgst_amount     platform_ref.money_inr NOT NULL DEFAULT 0,
    cess_amount     platform_ref.money_inr NOT NULL DEFAULT 0,

    CONSTRAINT gstr_2b_itc_summary_natural_key UNIQUE (header_id, category, availability),

    FOREIGN KEY (category_vt, category)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (availability_vt, availability)
        REFERENCES platform_ref.universal_master (value_type, value)
);

COMMENT ON TABLE {{silver}}.gstr_2b_itc_summary IS
    'B1.03 GSTR_2B itcsumm block — GSTN''s own per-category ITC availability '
    'totals, stored as reported, not recomputed from gstr_2b_line.';


-- --- Read contract --------------------------------------------------------------
--
-- Definer rights. Do NOT add security_invoker = true (tenant 004 explains why).

CREATE OR REPLACE VIEW {{silver}}.v1_gstr_2b AS
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
    generated_at,
    source_version,
    valid_from,
    valid_to,
    recorded_at,
    superseded_at
FROM {{silver}}.gstr_2b;

CREATE OR REPLACE VIEW {{silver}}.v1_gstr_2b_line AS
SELECT
    l.id,
    l.header_id,
    h.batch_id,
    h.entity_id,
    l.line_number,
    l.row_hash,
    l.section,
    l.counterparty_gstin,
    l.counterparty_name,
    l.document_kind,
    l.document_number,
    l.document_date,
    l.item_number,
    l.rate,
    l.taxable_value,
    l.igst_amount,
    l.cgst_amount,
    l.sgst_amount,
    l.cess_amount,
    l.itc_available,
    l.itc_unavailable_reason,
    l.place_of_supply,
    l.reverse_charge,
    l.attributes,
    h.superseded_at
FROM {{silver}}.gstr_2b_line l
JOIN {{silver}}.gstr_2b h ON h.id = l.header_id;

CREATE OR REPLACE VIEW {{silver}}.v1_gstr_2b_itc_summary AS
SELECT
    s.id,
    s.header_id,
    h.batch_id,
    h.entity_id,
    s.category,
    s.availability,
    s.igst_amount,
    s.cgst_amount,
    s.sgst_amount,
    s.cess_amount,
    h.superseded_at
FROM {{silver}}.gstr_2b_itc_summary s
JOIN {{silver}}.gstr_2b h ON h.id = s.header_id;
