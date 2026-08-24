-- =============================================================================
-- Shared migration 033 — GSTN_JSON_PROMOTE dispatch mechanism, and GSTR_2B's
-- registry row pointed at it.
--
-- D3 (HANDOFF-2026-08-19-categoryB.md, decided 2026-08-19): archetype-5
-- nested-JSON returns need a fifth dispatch mechanism, mirroring the
-- archetype-2 precedent (register_types.py: bespoke Python per type, not a
-- declarative spec) rather than extending extraction_spec's label:value
-- grammar, which has no answer for a 3-level grouped list.
--
-- `023_dispatch_mechanism.sql` is APPLIED and PINNED — its CHECK constraint
-- is widened here with DROP + ADD, never by editing that file, and
-- scripts/gen_registry_seed.py's DISPATCH_MECHANISM_PATH must NOT be re-run
-- over it (it would rewrite an applied migration). The precedent for this
-- exact move is migration 014's ITC_04_ACKNOWLEDGEMENT archetype correction:
-- a hand-written UPDATE against an applied migration's table, not a
-- regeneration.
-- =============================================================================

ALTER TABLE platform_ref.document_type
    DROP CONSTRAINT document_type_dispatch_mechanism_values_ck;

ALTER TABLE platform_ref.document_type
    ADD CONSTRAINT document_type_dispatch_mechanism_values_ck
    CHECK (dispatch_mechanism IN (
        'ARCHETYPE1_PROMOTE', 'PDF_EXTRACTION', 'REGISTER_LOADER',
        'SALES_REGISTER', 'GSTN_JSON_PROMOTE', ''
    ));

UPDATE platform_ref.document_type
   SET dispatch_mechanism = 'GSTN_JSON_PROMOTE',
       table_name = 'gstr_2b'
 WHERE doc_type_code = 'GSTR_2B';

-- --- Vocabulary for gstr_2b_line / gstr_2b_itc_summary (tenant 026) --------
--
-- CDNR is seeded even though the parser does not populate it yet (tenant
-- 026's header comment) — the vocabulary is the schema's contract, and a
-- parser catching up to it later is cheaper than a second migration to add
-- the value.

INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    ('Gstr2b_Section', 'B2B',  'Inward supply from a registered supplier'),
    ('Gstr2b_Section', 'CDNR', 'Credit/debit note from a registered supplier — not parsed yet, see tenant 026'),
    ('Gstr2b_Section', 'IMPG', 'IGST paid on import of goods, from ICEGATE via GSTN'),
    ('Gstr2b_Section', 'ISD',  'Input Service Distributor credit distribution'),

    ('Gstr2b_Document_Kind', 'INVOICE',     'B2B regular invoice (typ=R in the source payload)'),
    ('Gstr2b_Document_Kind', 'CREDIT_NOTE', 'CDNR credit note — not parsed yet, see tenant 026'),
    ('Gstr2b_Document_Kind', 'DEBIT_NOTE',  'CDNR debit note — not parsed yet, see tenant 026'),
    ('Gstr2b_Document_Kind', 'ISD_CREDIT',  'ISD distribution document'),
    ('Gstr2b_Document_Kind', 'BILL_OF_ENTRY', 'IMPG import Bill of Entry'),

    ('Gstr2b_Itc_Category', 'B2B',  'itcsumm category: inward supply from registered suppliers'),
    ('Gstr2b_Itc_Category', 'IMPG', 'itcsumm category: import of goods'),
    ('Gstr2b_Itc_Category', 'ISD',  'itcsumm category: ISD credit'),

    ('Gstr2b_Itc_Availability', 'AVAILABLE',     'itcavl block of itcsumm'),
    ('Gstr2b_Itc_Availability', 'NOT_AVAILABLE', 'itcnotavl block of itcsumm')
ON CONFLICT (value_type, value) DO NOTHING;
