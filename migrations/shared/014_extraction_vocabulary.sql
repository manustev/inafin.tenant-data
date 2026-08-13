-- =============================================================================
-- Shared migration 014 — vocabulary for the four archetype tables built for
-- the A2-A7 extraction adapters (streamed-marinating-gray.md).
--
-- Mirrors shared migration 007 (entitlement vocabulary): each new tenant table
-- (proceeding_event, entity_master_record, financial_statement_extract,
-- narrative_contract; tenant migrations 016-019) FKs its authority/status/type
-- columns here, so a mis-typed value is a foreign key violation, not a review
-- comment. document_type_code_archetype_uq (shared 007) already covers the
-- (doc_type_code, archetype) composite FK target for ANY archetype number, not
-- just 3 — it is a plain unique index, general from the start, so no new index
-- is needed here.
-- =============================================================================

INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    -- --- proceeding_event (archetype 6) -------------------------------------
    ('Proceeding_Authority', 'RBI',    'Reserve Bank of India — FEMA compounding'),
    ('Proceeding_Authority', 'GSTN',   'GST Network — job-work / return acknowledgements'),
    ('Proceeding_Authority', 'CBIC',   'CBIC or jurisdictional GST Commissioner'),
    ('Proceeding_Authority', 'CUSTOMS','Customs — proceedings and orders'),

    ('Proceeding_Status', 'OPEN',      'Proceeding ongoing, not yet settled'),
    ('Proceeding_Status', 'CLOSED',    'Settled — e.g. compounding order paid and closed'),
    ('Proceeding_Status', 'FILED',     'Acknowledgement of a filing, no further status to track'),

    -- --- entity_master_record (archetype 7) ---------------------------------
    ('Master_Record_Status', 'ACTIVE',   'Current as of the record''s as_of_date'),
    ('Master_Record_Status', 'HISTORIC', 'Superseded by a later as_of_date snapshot'),

    -- --- financial_statement_extract (archetype 4) --------------------------
    ('Statement_Status', 'AUDITED',    'Extract drawn from audited financial statements'),
    ('Statement_Status', 'DRAFT',      'Not yet audited/finalised'),

    -- --- narrative_contract (archetype 8) -----------------------------------
    ('Contract_Status', 'ACTIVE',      'In force as of ingestion'),
    ('Contract_Status', 'EXPIRED',     'Term has lapsed'),
    ('Contract_Status', 'TERMINATED',  'Ended before its term')
ON CONFLICT (value_type, value) DO NOTHING;


-- --- A5.17 ITC_04_ACKNOWLEDGEMENT: registry correction --------------------
--
-- registry/document_types.csv classifies this SOURCES-side, from the Source
-- Document Register's Category A grouping, as archetype 5 (FILED_RETURN) —
-- the same family as GSTR-1/3B. But ARCHITECTURE.md 6/7 draws archetype 5 as
-- "mostly Stream A portal-poller territory" (a return the platform polls the
-- GSTN portal for), and ITC-04 acknowledgements are not that: they are a
-- document a client UPLOADS (a portal-generated PDF extract, same intake path
-- as every other archetype 3/6/7/8 sample in this batch), and structurally
-- they are a point-in-time proceeding record — "this job-work quarter's
-- dispatch/return reconciliation was filed and acknowledged" — not a return
-- whose CURRENT state the platform re-polls. streamed-marinating-gray.md
-- names this explicitly: "pragmatically, A5.17 ITC-04 Acknowledgement (a
-- document-issued event, not a portal-polled return — the true archetype-5
-- types like GSTR-1/3B stay Stream A/out of scope)".
--
-- Migration 004 (document_registry_seed) is applied and pinned, so this is a
-- correction migration, not an edit — the same technique migration 012 used to
-- correct a shared domain after it was already applied. Do NOT regenerate 004
-- from registry/document_types.csv to "fix" this: that would rewrite an
-- applied, checksum-pinned migration. The CSV's `archetype` cell for
-- ITC_04_ACKNOWLEDGEMENT has been updated to 6 as documentation of record, but
-- scripts/gen_registry_seed.py must not be re-run over it while 004 stays
-- pinned.
UPDATE platform_ref.document_type
   SET archetype = 6
 WHERE doc_type_code = 'ITC_04_ACKNOWLEDGEMENT'
   AND archetype = 5;
