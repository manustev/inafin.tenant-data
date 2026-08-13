-- =============================================================================
-- Tenant migration 016 — Archetype 6: the proceeding event.
--
-- streamed-marinating-gray.md's new-table sign-off. Follows
-- migrations/tenant/006_entitlement_instrument.sql's DDL idiom exactly: a
-- composite FK pinning the type to its archetype, _vt-pinned vocabulary
-- columns, bitemporal supersession, batch/bronze lineage, a partial unique
-- index on the current version, a v1_ definer-rights view.
--
-- Two document types land here this session: A7.02 RBI_COMPOUNDING_ORDER (the
-- canonical case — archetype 6 in the registry from the start) and A5.17
-- ITC_04_ACKNOWLEDGEMENT (registry-corrected 5 -> 6 by shared migration 014;
-- see that migration's comment for why). Both are the same shape: a
-- point-in-time record that something was filed, acknowledged, or settled with
-- an authority — not an entitlement (archetype 3, which confers standing
-- going forward) and not a re-polled return (the true archetype-5 shape).
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{silver}}.proceeding_event (
    event_id            uuid PRIMARY KEY,
    entity_id            uuid NOT NULL,

    event_type           text NOT NULL,
    event_archetype      smallint NOT NULL DEFAULT 6
        CHECK (event_archetype = 6),

    authority             text NOT NULL,
    authority_vt          text NOT NULL DEFAULT 'Proceeding_Authority'
        CHECK (authority_vt = 'Proceeding_Authority'),

    -- The compounding application number, the ITC-04 ARN, ... — whatever the
    -- issuing authority's own filing/order reference is. Required, same as
    -- entitlement_instrument.instrument_number: a proceeding with no
    -- reference number cannot be distinguished from a resubmission of itself.
    reference_number     text NOT NULL,

    -- The date the proceeding concluded/was filed — not "as of" (that is
    -- entity_master_record's axis), a proceeding is a single event in time.
    event_date            date NOT NULL,

    -- Nullable: a filing acknowledgement (ITC-04) has no amount; a
    -- compounding order does (the amount paid to close the contravention).
    amount                numeric(14, 2),

    status                text NOT NULL DEFAULT 'CLOSED',
    status_vt             text NOT NULL DEFAULT 'Proceeding_Status'
        CHECK (status_vt = 'Proceeding_Status'),

    -- Whatever else the event carries that doesn't earn a first-class column
    -- (contravention description, quarter dispatched/returned counts, ...).
    details               jsonb NOT NULL DEFAULT '{}'::jsonb,

    supersedes_event_id  uuid
        REFERENCES {{silver}}.proceeding_event (event_id),

    recorded_at           timestamptz NOT NULL DEFAULT now(),
    superseded_at         timestamptz,

    batch_id              uuid NOT NULL REFERENCES {{silver}}.ingest_batch (batch_id),
    bronze_ingest_id      uuid NOT NULL,

    FOREIGN KEY (event_type, event_archetype)
        REFERENCES platform_ref.document_type (doc_type_code, archetype),
    FOREIGN KEY (authority_vt, authority)
        REFERENCES platform_ref.universal_master (value_type, value),
    FOREIGN KEY (status_vt, status)
        REFERENCES platform_ref.universal_master (value_type, value)
);

CREATE INDEX IF NOT EXISTS proceeding_event_lookup_idx
    ON {{silver}}.proceeding_event (entity_id, event_type, event_date)
    WHERE superseded_at IS NULL;

-- One current version per (entity, type, reference_number) — the resubmission
-- guard, same reasoning as entitlement_instrument_current_uq.
CREATE UNIQUE INDEX IF NOT EXISTS proceeding_event_current_uq
    ON {{silver}}.proceeding_event (entity_id, event_type, reference_number)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{silver}}.proceeding_event IS
    'Archetype 6 (streamed-marinating-gray.md). A point-in-time record that '
    'something was filed with, acknowledged by, or settled with an authority — '
    'not an ongoing entitlement (archetype 3) and not a re-polled return '
    '(archetype 5).';

CREATE OR REPLACE VIEW {{silver}}.v1_proceeding_event AS
SELECT
    event_id,
    entity_id,
    event_type,
    authority,
    reference_number,
    event_date,
    amount,
    status,
    details,
    supersedes_event_id,
    recorded_at,
    superseded_at,
    batch_id,
    bronze_ingest_id
FROM {{silver}}.proceeding_event;
