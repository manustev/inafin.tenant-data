-- =============================================================================
-- Tenant migration 005 — Bronze records fact; Silver records judgement.
--
-- Removes disposition from the artefact ledger. `status`, `promoted_batch_id`
-- and `error_detail` were conclusions we reached LATER about a file, stored on
-- the record of what the customer sent. That made Bronze mutable and gave it an
-- opinion. Bronze is now written once and never revised — shared migration 006
-- removes UPDATE and DELETE from every runtime role, so this is enforced by
-- privilege rather than discipline.
--
-- Nothing is lost, because none of it was Bronze's to know:
--
--   promoted?     ingest_batch.bronze_manifest_ref already points back at the
--                 artefact. The Bronze->Silver pointer was the redundant
--                 direction, and it was the one that forced the UPDATE.
--   quarantined?  moves to {{silver}}.quarantined_artefact below. Deciding a
--                 file is unusable requires parsing it, and parsing is Silver's
--                 job by definition — Bronze holds no business content.
--
-- The `Artefact_Status` values in platform_ref.universal_master are left seeded
-- but are now unreferenced. They are harmless, and dropping them would mean a
-- migration against shared reference data to delete a vocabulary we may want
-- back for the HITL disposition work (TODO.md).
-- =============================================================================

ALTER TABLE {{bronze}}.artefact_ledger
    DROP COLUMN IF EXISTS status,
    DROP COLUMN IF EXISTS status_vt,
    DROP COLUMN IF EXISTS promoted_batch_id,
    DROP COLUMN IF EXISTS error_detail;

-- Both indexes covered dropped columns and went with them; named here so the
-- intent is explicit rather than incidental.
DROP INDEX IF EXISTS {{bronze}}.artefact_ledger_status_idx;
DROP INDEX IF EXISTS {{bronze}}.artefact_ledger_batch_idx;


-- --- Quarantine, in Silver --------------------------------------------------
-- Quarantine, never delete (ARCHITECTURE.md 8). The artefact stays in Bronze
-- under Object Lock; this records why it could not be used. A rejected file is
-- still evidence of what the client sent, and in Forensic Mode that record is
-- itself submissible — which is precisely why the verdict must not overwrite
-- anything about the artefact.
CREATE TABLE IF NOT EXISTS {{silver}}.quarantined_artefact (
    -- One verdict per artefact. A re-attempt after a parser fix replaces the
    -- verdict rather than accumulating rows; the artefact itself is immutable
    -- and identical, so a history of our own retries belongs in logs.
    ingest_id        uuid PRIMARY KEY,
    entity_id        uuid NOT NULL,

    document_type    text NOT NULL,
    document_type_vt text NOT NULL DEFAULT 'Document_Type'
        CHECK (document_type_vt = 'Document_Type'),

    rejected_at      timestamptz NOT NULL DEFAULT now(),
    reason           text NOT NULL,
    ingest_run_id    uuid,

    -- No FK to {{bronze}}.artefact_ledger. Silver already references Bronze by
    -- value in ingest_batch.bronze_manifest_ref without one: a cross-layer
    -- constraint would make Bronze undroppable independently of Silver, and the
    -- layers are meant to be separable (a tenant graduating to its own database
    -- moves all three together, but the archival lifecycles differ).
    FOREIGN KEY (document_type_vt, document_type)
        REFERENCES platform_ref.universal_master (value_type, value)
);

CREATE INDEX IF NOT EXISTS quarantined_artefact_rejected_idx
    ON {{silver}}.quarantined_artefact (rejected_at);

COMMENT ON TABLE {{silver}}.quarantined_artefact IS
    'Artefacts that failed structural validation. The bytes remain in Bronze '
    'under Object Lock; this is the record of why they were not promoted.';
