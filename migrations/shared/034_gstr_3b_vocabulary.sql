-- =============================================================================
-- Shared migration 034 — GSTR_3B vocabulary and registry wiring.
--
-- No CHECK widen needed here (unlike 033) — GSTN_JSON_PROMOTE already exists
-- as a valid dispatch_mechanism value.
-- =============================================================================

UPDATE platform_ref.document_type
   SET dispatch_mechanism = 'GSTN_JSON_PROMOTE',
       table_name = 'gstr_3b'
 WHERE doc_type_code = 'GSTR_3B';

INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    -- Only value seen in any of the 31 real specimens. Not inventing the
    -- rest of GSTN's status vocabulary (e.g. a not-yet-filed state) without
    -- a real example — same discipline CLAUDE.md already names for the
    -- onboarding tables.
    ('Gstr3b_Filing_Status', 'Filed', 'GSTR-3B has been filed on the GST portal'),

    ('Gstr3b_Itc_Box', 'AVAILED',    'Table 4(A) itc_avl — ITC availed this period'),
    ('Gstr3b_Itc_Box', 'REVERSED',   'Table 4(B) itc_rev — ITC reversed this period'),
    ('Gstr3b_Itc_Box', 'INELIGIBLE', 'Table 4(D) itc_inelg — ITC not eligible'),

    ('Gstr3b_Itc_Type', 'IMPG', 'Import of goods'),
    ('Gstr3b_Itc_Type', 'IMPS', 'Import of services'),
    ('Gstr3b_Itc_Type', 'ISRC', 'Inward supplies liable to reverse charge (other than IMPG/IMPS)'),
    ('Gstr3b_Itc_Type', 'ISD',  'ISD credit received'),
    ('Gstr3b_Itc_Type', 'OTH',  'All other ITC'),
    ('Gstr3b_Itc_Type', 'RUL',  'Reversal under GST rules (42/43 etc.)'),

    ('Gstr3b_Inward_Supply_Type', 'GST',    'Inward supply from composition/nil/exempt registered dealers'),
    ('Gstr3b_Inward_Supply_Type', 'NONGST', 'Inward supply of non-GST goods/services')
ON CONFLICT (value_type, value) DO NOTHING;
