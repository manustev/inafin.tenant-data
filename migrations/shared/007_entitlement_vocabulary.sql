-- =============================================================================
-- Shared migration 007 — vocabulary for Archetype 3 (entitlement instruments).
--
-- ARCHITECTURE.md 6: 33 of the 125 in-scope document types are the same object.
-- LUT, IEC, every SEZ/EOU/STPI document, EPCG, AA, EODC, B-17, BLUT, AEO,
-- SCOMET, the taxpayer's own AAR, incentive approvals and entity-specific
-- clarifications all answer one question:
--
--     Was an active instrument of type T, covering HSN H, held by entity E
--     on date D?
--
-- One table, one index, one retrieval contract instead of thirty-three.
-- =============================================================================

-- --- Make the registry load-bearing -----------------------------------------
-- {{silver}}.entitlement_instrument FKs (instrument_type, 3) here, so only a
-- document type the REGISTRY classifies as archetype 3 can ever be stored as an
-- instrument. Without this the constraint would be a convention, and the first
-- mis-typed row would be discovered by a wrong reconciliation result rather
-- than by the database.
--
-- doc_type_code is already the primary key, so this index adds no restriction —
-- it exists solely to be a valid composite FK target.
CREATE UNIQUE INDEX IF NOT EXISTS document_type_code_archetype_uq
    ON platform_ref.document_type (doc_type_code, archetype);


INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    -- WHO ISSUED IT — distinct from document_type.source_system, which records
    -- where we obtained it. CLIENT_DGFT means "DGFT issued it, the client gave
    -- it to us": one authority, two provenance paths, and the entitlement check
    -- turns on the authority.
    ('Issuing_Authority', 'DGFT',              'Directorate General of Foreign Trade'),
    ('Issuing_Authority', 'CUSTOMS',           'Customs — bonds, licences, classification orders'),
    ('Issuing_Authority', 'DEV_COMMISSIONER',  'SEZ/EOU Development Commissioner'),
    ('Issuing_Authority', 'SEZ_AUTHORITY',     'SEZ authority — Letter of Approval'),
    ('Issuing_Authority', 'STPI',              'Software Technology Parks of India'),
    ('Issuing_Authority', 'RBI',               'Reserve Bank of India — FEMA approvals'),
    ('Issuing_Authority', 'GSTN',              'GST Network — LUT, ISD and practitioner records'),
    ('Issuing_Authority', 'CBDT',              'Central Board of Direct Taxes — APA'),
    ('Issuing_Authority', 'CBIC',              'CBIC or jurisdictional GST Commissioner'),
    ('Issuing_Authority', 'MIN_COMMERCE',      'Ministry of Commerce — SEZ notification'),
    ('Issuing_Authority', 'STATE_GOVERNMENT',  'State government — investment incentive schemes'),
    ('Issuing_Authority', 'AAR_AUTHORITY',     'Advance Ruling Authority (AAR / AAAR)'),
    ('Issuing_Authority', 'COURT',             'HC / SC / CESTAT'),

    -- LIFECYCLE. Not derivable from valid_to: an instrument inside its validity
    -- window can still be SUSPENDED (rejected SEZ APR) or CANCELLED (breached
    -- EOU LoP), and both retrospectively remove the entitlement. Expiry and
    -- standing are independent axes, and zero-rating depends on both.
    ('Instrument_Status', 'ACTIVE',     'In force and conferring its entitlement'),
    ('Instrument_Status', 'SUSPENDED',  'Temporarily not conferring — e.g. rejected SEZ APR'),
    ('Instrument_Status', 'CANCELLED',  'Withdrawn by the issuing authority'),
    ('Instrument_Status', 'DISCHARGED', 'Obligation fulfilled and closed — e.g. EODC issued'),
    ('Instrument_Status', 'EXPIRED',    'Validity window elapsed without renewal')
ON CONFLICT (value_type, value) DO NOTHING;
