-- =============================================================================
-- Tenant migration 006 — Archetype 3: the entitlement instrument.
--
-- Thirty-three document types, one table. Every zero-rating, deemed-export and
-- scheme check in A8/A10 reduces to:
--
--     Was an active instrument of type T, covering HSN H, held by entity E
--     on date D?
--
-- This is also the D5.02 two-part check the Source Document Register calls out:
-- (1) targeted notification in corpus (C2.05, held in inafin-gst-corpus);
-- (2) the entity's entitlement instrument establishing their coverage. This
-- table is the second half.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.entitlement_instrument (
    instrument_id        uuid PRIMARY KEY,
    entity_id            uuid NOT NULL,

    -- Type is a REGISTRY row, and only an archetype-3 one. The archetype is
    -- pinned to the constant 3 so the composite FK enforces membership — the
    -- same CHECK-pinned pattern as the _vt columns, applied to the registry
    -- rather than to universal_master. Storing a GSTR-1 here is not a bug to be
    -- caught in review; it is a foreign key violation.
    instrument_type      text NOT NULL,
    instrument_archetype smallint NOT NULL DEFAULT 3
        CHECK (instrument_archetype = 3),

    issuing_authority    text NOT NULL,
    issuing_authority_vt text NOT NULL DEFAULT 'Issuing_Authority'
        CHECK (issuing_authority_vt = 'Issuing_Authority'),

    instrument_number    text NOT NULL,

    -- The query that matters. Business validity, not system time: an export
    -- invoice dated 2025-06-01 needs the LUT that was valid ON that date, which
    -- is not necessarily the one we hold today.
    valid_from           date NOT NULL,
    valid_to             date NOT NULL DEFAULT DATE '9999-12-31',

    -- Independent of the window — see the seed note in shared 007.
    status               text NOT NULL DEFAULT 'ACTIVE',
    status_vt            text NOT NULL DEFAULT 'Instrument_Status'
        CHECK (status_vt = 'Instrument_Status'),

    -- HSN coverage is a FIRST-CLASS COLUMN, not a key inside `scope`, because
    -- it is the indexed query axis.
    --
    -- HSN is hierarchical: an instrument scoped to '8471' covers supply of
    -- '84713010'. A LIKE-prefix test cannot use an index, so the containment is
    -- inverted — the caller expands the queried HSN into its own prefixes
    -- (84, 8471, 847130, 84713010) and this array is tested for overlap, which
    -- GIN answers directly. See src/reader/entitlement_reader.py.
    --
    -- NULL means UNRESTRICTED, and that is the common case: a LUT covers all
    -- exports, an IEC covers all trade. NULL and '{}' must not be conflated —
    -- an empty array would mean "covers nothing", which no instrument does.
    scope_hsn            text[] CHECK (scope_hsn IS NULL OR cardinality(scope_hsn) > 0),

    -- Everything else the instrument scopes: services, premises, ports,
    -- permitted inputs. JSONB because it differs per instrument type and is
    -- filtered after the indexed predicates have already cut the candidate set.
    scope                jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- What the holder owes in return: NFE target, export obligation quantum,
    -- bond amount. Kept separate from scope because obligations are tracked to
    -- discharge (EODC) while scope is static for the instrument's life.
    obligation           jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- Renewal chains. One LUT per financial year per GSTIN, each superseding
    -- the last; the prior instrument stays queryable because invoices dated
    -- inside its window still depend on it.
    supersedes_instrument_id uuid
        REFERENCES {{silver}}.entitlement_instrument (instrument_id),

    -- Bitemporal system time (ARCHITECTURE.md 8). A correction closes the prior
    -- version and appends; nothing is updated in place. This is what answers
    -- "what did we believe on the date we released that report".
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    superseded_at        timestamptz,

    batch_id             uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id     uuid NOT NULL,

    CONSTRAINT entitlement_instrument_window CHECK (valid_to > valid_from),

    FOREIGN KEY (instrument_type, instrument_archetype)
        REFERENCES platform_ref.document_type (doc_type_code, archetype),
    FOREIGN KEY (issuing_authority_vt, issuing_authority)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (status_vt, status)
        REFERENCES platform_ref.universal_master (value_type, value)
);

-- The entitlement lookup. Partial on the current version: a superseded row is
-- never the answer to "what do we believe now", and excluding it keeps the
-- index the size of the live instrument set rather than of its whole history.
CREATE INDEX IF NOT EXISTS entitlement_instrument_lookup_idx
    ON {{silver}}.entitlement_instrument
       (entity_id, instrument_type, valid_from, valid_to)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS entitlement_instrument_hsn_idx
    ON {{silver}}.entitlement_instrument USING GIN (scope_hsn)
    WHERE superseded_at IS NULL;

-- One current version per instrument. A correction supersedes the prior row and
-- inserts a new one, so this cannot fire on a legitimate amendment — but it does
-- fire if two ingests of the same document both land as current, which is the
-- resubmission failure mode (TODO.md).
CREATE UNIQUE INDEX IF NOT EXISTS entitlement_instrument_current_uq
    ON {{silver}}.entitlement_instrument
       (entity_id, instrument_type, instrument_number)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.entitlement_instrument IS
    'Archetype 3 (ARCHITECTURE.md 6). Thirty-three document types collapse here. '
    'Adding the thirty-fourth entitlement instrument must be a registry row, not '
    'a table — if it needs a schema change, the archetype abstraction has failed.';


-- --- Read contract ----------------------------------------------------------
-- Definer rights. Do NOT add security_invoker = true: the view executes as its
-- owner, which is exactly what lets recon read it while holding no privilege on
-- any Silver base table (ARCHITECTURE.md 2.4).
CREATE OR REPLACE VIEW {{silver}}.v1_entitlement_instrument AS
SELECT
    instrument_id,
    entity_id,
    instrument_type,
    issuing_authority,
    instrument_number,
    valid_from,
    valid_to,
    status,
    scope_hsn,
    scope,
    obligation,
    supersedes_instrument_id,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id
FROM {{silver}}.entitlement_instrument;
