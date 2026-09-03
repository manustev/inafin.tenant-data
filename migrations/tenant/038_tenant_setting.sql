-- =============================================================================
-- Tenant migration 038 — a generic tenant-wide settings/preferences table in
-- Gold, plus the read contract inafin-reconciliation-engine gets on it.
--
-- Not scoped to reconciliation — this is meant for portal preferences, API
-- config, and recon settings alike ("just like platform settings", per the
-- request). Lives in Gold because Gold is already the serving layer for
-- portal/api-authored, tenant-scoped operational data (workspace_decision,
-- gst_document, ... — migration 022) — this is the same shape: human/admin-
-- configured, not derived from an ingested document, so Silver's "ingest
-- writes, everyone else reads" model does not fit it. Written via
-- t_<slug>_recon, the same role portal/api already use for every other
-- Gold write (no new role needed on that side).
--
-- Scope model: a setting_id can hold up to three concurrently active rows —
-- tenant-wide default (entity_id NULL, gstin NULL), an entity-level override
-- (entity_id set, gstin NULL), and a GSTIN-level override (both set). Which
-- one a consumer should apply is THEIR resolution logic (most specific
-- wins), not baked into this table or its view — same "expose the facts,
-- let the deterministic consumer explain the linkage" principle the request
-- doc itself asked for on the remuneration contracts.
--
-- Full history is exposed (superseded rows included), not just the current
-- value — a past transaction may need to be evaluated against whatever
-- tolerance was actually in effect on ITS date, not today's value.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{gold}}.tenant_setting (
    setting_row_id    uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),

    -- Dotted namespace, e.g. 'rcm.director_remuneration.amount_match_tolerance',
    -- 'portal.ui.default_language'. Free text on purpose — this table is not
    -- the registry, and a namespace convention is a documentation concern,
    -- not a CHECK constraint this repo should own for every future consumer.
    setting_id        text NOT NULL,

    entity_id         uuid,
    gstin             platform_ref.gstin,

    value_type        text NOT NULL
        CHECK (value_type IN ('NUMERIC', 'TEXT', 'BOOLEAN', 'JSON')),
    value_numeric     numeric,
    value_text        text,
    value_boolean     boolean,
    value_json        jsonb,
    unit              text,

    version           integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    approval_state    text NOT NULL DEFAULT 'PENDING'
        CHECK (approval_state IN ('PENDING', 'APPROVED', 'REJECTED')),
    approved_by       text,
    approved_at       timestamptz,

    effective_from    date NOT NULL,
    effective_to      date,

    -- Who/what set this value — a portal user action id, an API call id.
    -- Required: an unattributed setting change is not auditable.
    source_reference  text NOT NULL,

    recorded_at       timestamptz NOT NULL DEFAULT now(),
    superseded_at     timestamptz,
    supersedes_row_id uuid REFERENCES {{gold}}.tenant_setting (setting_row_id),
    created_by        text NOT NULL DEFAULT current_user,

    CONSTRAINT tenant_setting_window CHECK (effective_to IS NULL OR effective_to > effective_from),

    -- Exactly one value column populated, matching value_type — a NUMERIC
    -- setting with a stray value_text is a data-entry bug, not a valid row.
    CONSTRAINT tenant_setting_value_matches_type CHECK (
        (value_type = 'NUMERIC' AND value_numeric IS NOT NULL
            AND value_text IS NULL AND value_boolean IS NULL AND value_json IS NULL)
        OR (value_type = 'TEXT' AND value_text IS NOT NULL
            AND value_numeric IS NULL AND value_boolean IS NULL AND value_json IS NULL)
        OR (value_type = 'BOOLEAN' AND value_boolean IS NOT NULL
            AND value_numeric IS NULL AND value_text IS NULL AND value_json IS NULL)
        OR (value_type = 'JSON' AND value_json IS NOT NULL
            AND value_numeric IS NULL AND value_text IS NULL AND value_boolean IS NULL)
    )
);

-- One current row per (setting_id, scope) — scope is (entity_id, gstin),
-- either or both of which may be NULL. COALESCE to sentinel values because
-- NULL <> NULL under a unique index, which would otherwise allow duplicate
-- tenant-wide defaults for the same setting_id.
CREATE UNIQUE INDEX IF NOT EXISTS tenant_setting_current_scope_uq
    ON {{gold}}.tenant_setting (
        setting_id,
        COALESCE(entity_id, '00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(gstin, '')
    )
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS tenant_setting_lookup_idx
    ON {{gold}}.tenant_setting (setting_id, entity_id)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{gold}}.tenant_setting IS
    'Generic tenant-wide settings/preferences (2026-09-03) — portal, API, '
    'and recon consumers alike. Not reconciliation-specific despite the '
    'first requested key being an RCM tolerance.';


-- -----------------------------------------------------------------------------
-- The read contract for inafin-reconciliation-engine. Gold-hosted, unlike
-- every other v1_rcm_* contract (those are all Silver) — this is the FIRST
-- deliberate exception to docs/adr/0001's "recon_engine touches nothing in
-- Gold" rule, made because settings genuinely live in Gold (portal/api's
-- serving layer), not because the rule is being abandoned. inafinplatform/
-- v2's own Gold tables (fact_record, workspace_*, ...) remain completely
-- off-limits — see shared migration 063's grant, which is a NAMED allowlist
-- (v1_reconciliation_%), not a blanket Gold opening.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{gold}}.v1_reconciliation_tenant_setting AS
SELECT
    setting_row_id,
    setting_id,
    entity_id,
    gstin,
    value_type,
    value_numeric,
    value_text,
    value_boolean,
    value_json,
    unit,
    version,
    approval_state,
    approved_by,
    approved_at,
    effective_from,
    effective_to,
    source_reference,
    recorded_at,
    superseded_at
FROM {{gold}}.tenant_setting;

COMMENT ON VIEW {{gold}}.v1_reconciliation_tenant_setting IS
    'Full history, not just the current value — a past transaction may need '
    'evaluating against whatever setting was in effect on ITS date. Which '
    'row applies (tenant-wide vs entity vs GSTIN override, which version) '
    'is the consumer''s resolution, not pre-filtered here.';
