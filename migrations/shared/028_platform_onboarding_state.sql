-- =============================================================================
-- Shared migration 028 — the onboarding wizard's own progress record.
--
-- Rebuilt from inafin-api's withdrawn proposal (migrations/platform/0004_
-- onboarding_state.sql) — see CLAUDE.md's platform migration chain entry.
--
-- One row per customer: which step the wizard is on and which steps are
-- already done. Deliberately separate from onboarding_customer.status (026),
-- which is the coarse lifecycle (DRAFT/SUBMITTED/APPROVED/...) — this table
-- is the fine-grained "where exactly in the flow" the UI needs to resume.
-- =============================================================================

CREATE TABLE IF NOT EXISTS platform_ref.onboarding_state (
    customer_id      uuid PRIMARY KEY
                         REFERENCES platform_ref.onboarding_customer (customer_id),
    current_step     text NOT NULL,
    completed_steps  text[] NOT NULL DEFAULT '{}',
    updated_at       timestamptz NOT NULL DEFAULT now(),
    updated_by       text NOT NULL
);

REVOKE ALL ON platform_ref.onboarding_state FROM PUBLIC;

GRANT SELECT ON platform_ref.onboarding_state TO platform_ref_reader;
