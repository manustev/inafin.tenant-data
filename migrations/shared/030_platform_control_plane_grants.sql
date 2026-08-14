-- =============================================================================
-- Shared migration 030 — app_login's ambient control-plane access.
--
-- THE INVARIANT-#2 EXCEPTION (CLAUDE.md). Everywhere else in this codebase,
-- app_login is NOINHERIT and holds nothing until `SET LOCAL ROLE t_<slug>_*`
-- — access to tenant data never arrives any other way. That still holds.
--
-- But identity resolution and customer onboarding (migrations 025-028) have
-- to run BEFORE a tenant is even known: inafin-api authorizes a request by
-- reading platform_ref.tenant_customer / onboarding_customer / document_type
-- to work out WHICH tenant a caller belongs to, and only then can it
-- `SET LOCAL ROLE`. Something has to be ambiently readable (and, for the
-- onboarding tables the API writes through directly, ambiently writable) for
-- that first step to be possible at all.
--
-- This migration is that something, and ONLY that something:
--   - every grant here targets a named platform_ref control-plane table or
--     the narrow view below — never bronze/silver/gold, tenant or otherwise.
--   - app.apply_tenant_grants' REVOKE-then-GRANT baseline (001_app.sql) is
--     unaffected by this file and continues to strip any tenant-schema grant
--     app_login should never have held, by mistake or otherwise.
--   - if this list needs to grow, grow it here, in one place that is easy to
--     find, diff, and audit — never as an ad hoc GRANT bolted onto an
--     unrelated migration.
-- =============================================================================

-- A narrow read of app.tenant_registry. `status` matters for onboarding UX
-- (is this tenant ready), `bucket` does not and stays hidden — 001_app.sql's
-- own comment on tenant_registry ("the list of tenants is itself disclosure")
-- is the reason this is a view and not a grant on the base table.
CREATE OR REPLACE VIEW app.v1_tenant_directory AS
SELECT slug, tenant_id, status
FROM app.tenant_registry;

COMMENT ON VIEW app.v1_tenant_directory IS
    'The only tenant_registry read app_login may hold ambiently. Definer '
    'rights (default) on purpose, same reasoning as every tenant v1_ view.';

GRANT SELECT ON app.v1_tenant_directory TO app_login;

-- Read-only lookups: identity/role resolution, and the picklists inafin-api
-- shows during onboarding (document types, HSN/SAC codes).
GRANT SELECT ON
    platform_ref.document_type,
    platform_ref.hsn_master,
    platform_ref.sac_master,
    platform_ref.api_principal,
    platform_ref.principal_tenant_role,
    platform_ref.role,
    platform_ref.role_permission,
    platform_ref.permission,
    platform_ref.principal_customer_access,
    platform_ref.tenant_customer
    TO app_login;

-- Written directly by the onboarding API, ahead of any tenant being known.
GRANT SELECT, INSERT, UPDATE ON
    platform_ref.onboarding_customer,
    platform_ref.customer_profile,
    platform_ref.onboarding_business_profile,
    platform_ref.onboarding_product_service,
    platform_ref.gst_registration,
    platform_ref.customer_document,
    platform_ref.data_requirement,
    platform_ref.onboarding_state,
    platform_ref.smart_question_run,
    platform_ref.smart_question,
    platform_ref.smart_question_response
    TO app_login;
