-- =============================================================================
-- Shared migration 003 — the Document Type Registry.
--
-- Doc 4 Section 2 as data. ARCHITECTURE.md 6: adding the 30th entitlement
-- instrument must be a row, not a sprint. This is the table that makes that
-- true — the per-document-type contract lives here, not in Python.
--
-- Two tables, deliberately:
--
--   document_type      the platform's vocabulary. One row per distinct
--                      ingestible document. Keyed by a stable code.
--   document_type_ref  the register's vocabulary. One row per Doc 4 ref,
--                      resolving to a document_type.
--
-- They are not the same set. The Source Document Register describes four
-- documents twice (A1.20 = D1.01-07, B3.04 = D5.07, B3.05 = D5.06,
-- C4.03 = D5.01 — the last stated by the register itself, "Stored in Group
-- D5"). Collapsing them in `document_type` while keeping every ref resolvable
-- in `document_type_ref` is what stops us building two ingestion paths for one
-- file, and stops the T9 completeness gate demanding the same document twice.
--
-- The CA engagement team speaks in refs. The platform speaks in codes. This is
-- the translation, and it is data.
-- =============================================================================

CREATE TABLE IF NOT EXISTS platform_ref.document_type (
    doc_type_code    text PRIMARY KEY,
    -- Every document type is also a universal_master value, so tenant tables
    -- FK to the same vocabulary Bronze declares against. Same CHECK-pinned
    -- _vt pattern as everywhere else (Category B Pattern 1).
    doc_type_code_vt text NOT NULL DEFAULT 'Document_Type'
        CHECK (doc_type_code_vt = 'Document_Type'),

    name             text NOT NULL,
    category         text NOT NULL CHECK (category IN ('A', 'B', 'C', 'D')),
    grp              text NOT NULL,

    -- Drives the T9 completeness gate. See the scope note below: MANDATORY here
    -- is the register's engagement-invariant reading. If obligation turns out
    -- to vary by client profile (onboarding items 19-25 are profile-gated) it
    -- belongs on the engagement, not on the document type. Open question 3.
    obligation       text NOT NULL CHECK (obligation IN ('MANDATORY', 'CONDITIONAL')),
    mode             text NOT NULL CHECK (mode IN ('BOTH', 'FORENSIC', 'OPERATIONAL')),
    sections         text[] NOT NULL,

    -- HOW it arrives, which is not the same as WHO holds it. A1.20 is filed
    -- under Category A (client-provided) but the marketplace operator is
    -- authoritative, so it is Stream C. The stream determines the connector;
    -- the category does not.
    stream           text,
    stream_vt        text NOT NULL DEFAULT 'Source_Stream'
        CHECK (stream_vt = 'Source_Stream'),

    archetype        smallint CHECK (archetype BETWEEN 1 AND 8),

    -- Bronze is uniform for every row here (ARCHITECTURE.md 3.1). This governs
    -- SILVER only. HYBRID means the blob stays the evidentiary record and a
    -- defined field set is extracted for querying — which is every instrument,
    -- order, certificate and contract in the register.
    silver_storage   text NOT NULL
        CHECK (silver_storage IN ('STRUCTURED', 'HYBRID', 'NONE')),

    source_system    text NOT NULL,

    -- FALSE for documents the register marks Mandatory that are NOT tenant
    -- data: B4.03 and B4.04 are bitemporal platform reference data sourced
    -- from INAFIN Corpus. They are recorded rather than deleted so a Mandatory
    -- obligation cannot silently vanish — but they are an obligation on
    -- inafin-gst-corpus, not on this repo.
    in_scope         boolean NOT NULL,

    notes            text NOT NULL DEFAULT '',

    FOREIGN KEY (doc_type_code_vt, doc_type_code)
        REFERENCES platform_ref.universal_master (value_type, value),
    -- MATCH SIMPLE: not enforced while stream IS NULL, which is exactly the
    -- out-of-scope case. CORPUS is deliberately NOT a Source_Stream value —
    -- it is the absence of an ingestion stream, not another one.
    FOREIGN KEY (stream_vt, stream)
        REFERENCES platform_ref.universal_master (value_type, value),

    CONSTRAINT document_type_scope_ck CHECK (
        (in_scope
            AND stream IS NOT NULL
            AND archetype IS NOT NULL
            AND silver_storage <> 'NONE')
        OR
        (NOT in_scope
            AND stream IS NULL
            AND archetype IS NULL
            AND silver_storage = 'NONE')
    )
);

COMMENT ON TABLE platform_ref.document_type IS
    'Doc 4 Section 2 as data. One row per distinct ingestible document type. '
    'Adding a document type is an INSERT; adding an archetype is a schema '
    'change. If adding the Nth instrument requires the latter, the archetype '
    'abstraction has failed (ARCHITECTURE.md 6).';


CREATE TABLE IF NOT EXISTS platform_ref.document_type_ref (
    register_ref  text NOT NULL,
    doc_type_code text NOT NULL
        REFERENCES platform_ref.document_type (doc_type_code),
    is_canonical  boolean NOT NULL,

    -- (ref, code) not (ref): A1.20 resolves to SEVEN codes. The register asks
    -- for "platform settlement reports (MTR/GTR/SSR/DRR/VRET)" as one line,
    -- and onboarding item 22 cites "A1.20/D1.01-07" as a single request.
    PRIMARY KEY (register_ref, doc_type_code)
);

-- Exactly one canonical ref per document type. This is the structural guard
-- against the duplicate problem returning: a second canonical ref for an
-- existing code cannot be inserted, so the register's double-counting cannot
-- silently reappear through a later edit.
CREATE UNIQUE INDEX IF NOT EXISTS document_type_ref_canonical_uq
    ON platform_ref.document_type_ref (doc_type_code)
    WHERE is_canonical;

CREATE INDEX IF NOT EXISTS document_type_ref_code_idx
    ON platform_ref.document_type_ref (doc_type_code);

COMMENT ON TABLE platform_ref.document_type_ref IS
    'Doc 4 register refs resolved to document types. Every ref in the register '
    'appears here, including aliases and out-of-scope corpus rows, so no ref '
    'the CA team quotes is unresolvable.';


REVOKE ALL ON platform_ref.document_type FROM PUBLIC;
REVOKE ALL ON platform_ref.document_type_ref FROM PUBLIC;

-- Granted to the group, once. Never per tenant — concurrent fan-out would
-- contend on the ACL row (ARCHITECTURE.md 5.8).
GRANT SELECT ON platform_ref.document_type     TO platform_ref_reader;
GRANT SELECT ON platform_ref.document_type_ref TO platform_ref_reader;
