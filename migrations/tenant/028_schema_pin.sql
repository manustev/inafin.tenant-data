-- =============================================================================
-- Tenant migration 028 — which published schema release this tenant is on.
--
-- THE PROBLEM THIS SOLVES (agreed 2026-08-24). A tenant downloads the schema
-- for a document type, spends weeks mapping their ERP export to it, and then
-- uploads. If the platform publishes v2 in the meantime, their mapping must
-- not silently start being read against a contract they have never seen. So
-- the release in force at their FIRST upload of a type is pinned to them, a
-- copy of the exact schema file is written into their own bucket, and
-- inafin-api serves them THAT rather than whatever is currently CURRENT.
--
-- WHY THIS IS BRONZE, AND WHY IT DOES NOT WEAKEN INVARIANT 6. "At this moment,
-- for this document type, this tenant was handed exactly these bytes" is a
-- FACT about what happened — the same character as artefact_ledger's own rows,
-- and the reason this table is INSERT-only like everything else in Bronze.
--
-- ROLLING A TENANT FORWARD IS AN INSERT, NOT AN UPDATE. When v2 is rolled out
-- to a tenant, a NEW row lands for that document type; the v1 row stays,
-- because "they were on v1 until this date" remains true forever. The current
-- pin is therefore the LATEST row per doc_type_code, never a mutable column —
-- which is what lets this be a Bronze table at all. `v1_schema_pin` below is
-- that read, so no caller has to re-derive it and get the tie-break wrong.
--
-- The unique index is (doc_type_code, release_version): the same release is
-- never pinned twice for a type, but successive releases are expected.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{bronze}}.schema_pin (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doc_type_code   text NOT NULL,
    release_version text NOT NULL,

    -- The upload that caused the pin. NULL only for a deliberate roll-forward
    -- (an operator moving this tenant to v2), which is not caused by any
    -- artefact — the FK would be a lie there, and a NOT NULL would force one.
    pinned_by_ingest_id uuid REFERENCES {{bronze}}.artefact_ledger (ingest_id),

    -- The exact bytes handed over, and where the tenant's own copy lives.
    -- sha256 is recorded independently of the object so that "did the file
    -- they downloaded change" is answerable without trusting the store — the
    -- same reason artefact_ledger records content_hash.
    schema_sha256   bytea NOT NULL,
    object_bucket   text NOT NULL,
    object_key      text NOT NULL,

    pinned_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT schema_pin_release_uq UNIQUE (doc_type_code, release_version)
);

COMMENT ON TABLE {{bronze}}.schema_pin IS
    'Which published schema release this tenant is on, per document type. '
    'INSERT-only: a roll-forward is a new row, never an update. Current pin = '
    'v1_schema_pin.';

-- The current pin per document type. DISTINCT ON over (pinned_at, id) rather
-- than max(pinned_at) alone: two pins written in the same transaction share a
-- timestamp, and the identity column is the only total order available.
CREATE OR REPLACE VIEW {{bronze}}.v1_schema_pin AS
SELECT DISTINCT ON (doc_type_code)
    doc_type_code,
    release_version,
    pinned_by_ingest_id,
    schema_sha256,
    object_bucket,
    object_key,
    pinned_at
FROM {{bronze}}.schema_pin
ORDER BY doc_type_code, pinned_at DESC, id DESC;

COMMENT ON VIEW {{bronze}}.v1_schema_pin IS
    'The release each document type is currently pinned to for this tenant. '
    'Definer rights, same as every other v1_ view.';
