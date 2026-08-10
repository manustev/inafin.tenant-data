-- =============================================================================
-- Tenant migration 015 — record a Bronze->Silver load request as a fact.
--
-- Part of the ingestion surface (HANDOFF-2026-08-07.md "Then: the ingestion
-- surface", CLAUDE.md "Next session starts here" item 2). This session builds
-- REST upload/status and a REST trigger endpoint, but the trigger endpoint is
-- a STUB: it records that someone asked for an artefact to be loaded, and
-- does NOT call any loader. There is still no doc_type_code -> loader
-- dispatcher (register spec vs promote.py's archetype-1 path vs
-- SalesRegisterLoader vs a future entitlement path — no registry column
-- names which one handles a given type). See TODO.md.
--
-- WHY THIS IS BRONZE, NOT SILVER. "A load was requested, for this doc_type,
-- at this time" is a fact about what was asked, not a judgement about
-- whether it happened or what it produced — invariant 6. It is therefore
-- INSERT-only, same privilege shape as artefact_ledger (005/006: ingest gets
-- SELECT + INSERT, nothing else), which app.apply_tenant_grants grants
-- automatically because it walks every table in the bronze schema — no
-- explicit GRANT needed in this file.
--
-- No FK to ingest_batch: a batch does not exist yet at the point a trigger is
-- recorded (that is the entire reason this is a stub — nothing has decided
-- which loader would produce one). The FK to artefact_ledger is what ties
-- this to a real, already-received artefact.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{bronze}}.load_trigger (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingest_id     uuid NOT NULL REFERENCES {{bronze}}.artefact_ledger (ingest_id),
    doc_type_code text NOT NULL,
    requested_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS load_trigger_ingest_idx
    ON {{bronze}}.load_trigger (ingest_id);

COMMENT ON TABLE {{bronze}}.load_trigger IS
    'A request to load a Bronze artefact into Silver, recorded as a fact. '
    'No loader runs as a result of a row landing here yet - see TODO.md.';
