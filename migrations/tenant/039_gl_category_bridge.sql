-- =============================================================================
-- Tenant migration 039 — GL-to-REF-02 category bridge, and the read contract
-- inafin-reconciliation-engine gets on it.
--
-- Same shape and reasoning as tenant migration 038 (tenant_setting): this is
-- client-configured mapping data (which GL code maps to which GST REF-02
-- reporting category, under what matching rule), not a fact extracted from
-- an ingested document. There is no source document to read this from and
-- no registry document type it could belong to — a client's own chart of
-- accounts is neither Bronze-ingested nor Silver-derived, the same reason
-- tenant_setting lives in Gold rather than Silver. Written via t_<slug>_recon,
-- same as every other Gold table portal/api maintains.
--
-- Grant: no new shared migration needed. Shared migration 063 already grants
-- t_<slug>_recon_engine SELECT on any Gold view matching v1_reconciliation_%,
-- and this view's name (v1_reconciliation_category_bridge) matches that
-- allowlist already — the same self-extending behavior the v1_rcm_% Silver
-- allowlist gives new RCM contracts.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{gold}}.gl_category_bridge (
    bridge_id          uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),

    entity_id          uuid,
    gstin              platform_ref.gstin,

    gl_code            text NOT NULL,
    ref02_category_code text NOT NULL,

    match_mode         text NOT NULL
        CHECK (match_mode IN ('GL_EXACT', 'EXACT_PHRASE', 'TOKEN_SET')),
    -- Only meaningful for EXACT_PHRASE / TOKEN_SET modes; NULL for GL_EXACT,
    -- where gl_code alone is the match key.
    normalized_narration_pattern text,
    inclusion_terms    text[],
    exclusion_terms    text[],

    -- Tie-break order when more than one row could match the same
    -- transaction (e.g. an entity-level rule and a tenant-wide rule for the
    -- same gl_code) — lower priority value wins. Which row actually applies
    -- is the engine's own resolution, same as tenant_setting's scope
    -- precedence; this repo only exposes the ordering key, not the logic.
    priority           integer NOT NULL DEFAULT 100,

    approval_state     text NOT NULL DEFAULT 'PENDING'
        CHECK (approval_state IN ('PENDING', 'APPROVED', 'REJECTED')),

    effective_from     date NOT NULL,
    effective_to       date,

    bridge_version     integer NOT NULL DEFAULT 1 CHECK (bridge_version >= 1),

    -- Who/what set this mapping — a portal user action id, an API call id.
    -- Required: an unattributed category mapping is not auditable.
    source_reference   text NOT NULL,

    recorded_at        timestamptz NOT NULL DEFAULT now(),
    superseded_at      timestamptz,
    supersedes_row_id  uuid REFERENCES {{gold}}.gl_category_bridge (bridge_id),
    created_by         text NOT NULL DEFAULT current_user,

    CONSTRAINT gl_category_bridge_window CHECK (effective_to IS NULL OR effective_to > effective_from),

    -- EXACT_PHRASE and TOKEN_SET need a pattern to match against; GL_EXACT
    -- must not carry one — gl_code is already the whole match key, and a
    -- stray pattern would silently narrow a rule that claims to be exact.
    CONSTRAINT gl_category_bridge_pattern_matches_mode CHECK (
        (match_mode = 'GL_EXACT' AND normalized_narration_pattern IS NULL)
        OR (match_mode IN ('EXACT_PHRASE', 'TOKEN_SET') AND normalized_narration_pattern IS NOT NULL)
    )
);

-- One current row per (gl_code, scope, match_mode, pattern) — scope is
-- (entity_id, gstin), either or both of which may be NULL. Same COALESCE-
-- to-sentinel trick as tenant_setting: NULL <> NULL under a unique index
-- would otherwise allow duplicate tenant-wide rules for the same gl_code.
--
-- The pattern is part of the key on purpose: for EXACT_PHRASE/TOKEN_SET,
-- more than one non-overlapping pattern can legitimately target the same
-- gl_code + scope (e.g. 'bank charges' and 'forex conversion' both mapped
-- under the same GL account to different REF-02 categories) — that overlap,
-- when it happens, is exactly the ambiguous-match case the engine resolves
-- via priority, not something this table should refuse to store.
CREATE UNIQUE INDEX IF NOT EXISTS gl_category_bridge_current_scope_uq
    ON {{gold}}.gl_category_bridge (
        gl_code,
        match_mode,
        COALESCE(normalized_narration_pattern, ''),
        COALESCE(entity_id, '00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(gstin, '')
    )
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS gl_category_bridge_lookup_idx
    ON {{gold}}.gl_category_bridge (gl_code, entity_id)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE {{gold}}.gl_category_bridge IS
    'Client-configured GL-code-to-REF-02-category mapping (2026-09-04) — '
    'portal/api-authored, not derived from an ingested document. Requested '
    'by inafin-reconciliation-engine as a governed bridge for GL-to-REF-02 '
    'classification.';


-- -----------------------------------------------------------------------------
-- The read contract for inafin-reconciliation-engine. Gold-hosted, same
-- deliberate exception as v1_reconciliation_tenant_setting (docs/adr/0001
-- follow-up) — already covered by shared migration 063's v1_reconciliation_%
-- allowlist, no new grant migration required.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{gold}}.v1_reconciliation_category_bridge AS
SELECT
    bridge_id,
    entity_id,
    gstin,
    gl_code,
    ref02_category_code,
    match_mode,
    normalized_narration_pattern,
    inclusion_terms,
    exclusion_terms,
    priority,
    effective_from,
    effective_to,
    approval_state,
    bridge_version,
    source_reference,
    recorded_at,
    superseded_at
FROM {{gold}}.gl_category_bridge;

COMMENT ON VIEW {{gold}}.v1_reconciliation_category_bridge IS
    'Full history, not just the current value — resolving tenant-wide vs '
    'entity vs GSTIN override, and which version applied on a given '
    'transaction date, is the engine''s own precedence logic, not '
    'pre-filtered here.';
