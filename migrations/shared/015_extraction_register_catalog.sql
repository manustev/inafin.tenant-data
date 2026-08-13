-- =============================================================================
-- Shared migration 015 — table_name for the three Archetype 2 registers added
-- by tenant migration 020 (streamed-marinating-gray.md phase 6). Same pattern
-- as shared 011/013 for the 23 Group A1 registers: table_name is hand-written
-- here, not regenerated from registry/document_types.csv, because migration
-- 004 (document_registry_seed) is already applied and pinned.
-- =============================================================================

UPDATE platform_ref.document_type d
   SET table_name = v.tbl
  FROM (VALUES
    ('PAYROLL_TDS_REGISTER', 'payroll_tds_register'),
    ('FIRC_BRC_REGISTER', 'firc_brc_register'),
    ('FORM_15CA_15CB', 'form_15ca_15cb')
  ) AS v(code, tbl)
 WHERE d.doc_type_code = v.code;
