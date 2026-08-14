-- =============================================================================
-- Shared migration 031 — api_principal gains `issuer`.
--
-- Requested by inafin-api (docs/tenant-data-api-compatibility.sql, a hand-off
-- spec, not an executable migration of theirs — per the seventh session's
-- ownership model, API-CONTRACT.md). The API validates an OIDC principal by
-- the pair (issuer, subject); migration 025 stored only external_subject.
--
-- Additive and non-destructive on purpose. The backfill/NOT NULL/unique-index
-- steps inafin-api's spec sketched are deliberately NOT included here — there
-- is no identity-provider source of truth in this workspace to backfill
-- `issuer` from yet. Do this in a follow-up migration once that source is
-- available, in this order: backfill every existing row, THEN NOT NULL, THEN
-- the unique index — never skip straight to the constraint on a column that
-- may still hold NULLs.
-- =============================================================================

ALTER TABLE platform_ref.api_principal
    ADD COLUMN IF NOT EXISTS issuer text;
