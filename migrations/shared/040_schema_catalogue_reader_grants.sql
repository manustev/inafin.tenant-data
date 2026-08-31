-- =============================================================================
-- Shared migration 040 — tenant roles read the schema catalogue.
--
-- Migration 037 granted the catalogue to app_login, for the PRE-tenant case:
-- the portal showing document types during onboarding. This is the other half
-- — the INSIDE-a-tenant case. `BronzeIngestionService.receive` pins the
-- current release to the tenant on their first upload of a type (tenant
-- migration 028), and it does that inside a `SET LOCAL ROLE t_<slug>_ingest`
-- transaction, so the ingest role itself has to be able to read which release
-- is CURRENT and where its bytes are.
--
-- Granted to `platform_ref_reader`, NOT to each tenant role: that group is
-- exactly the mechanism 001_app.sql created for this, and its header states
-- why (granting platform_ref per tenant makes every provision contend on the
-- same pg_namespace ACL row — invariant 8). Membership is already handed to
-- every tenant role by app.apply_tenant_grants, so this file needs no
-- per-tenant fan-out and no change to provisioning.
--
-- SELECT only. A tenant role reads which schema it was given; publishing a
-- release is this repo's job, through a superuser-run script.
-- =============================================================================

GRANT SELECT ON
    platform_ref.document_type_schema,
    platform_ref.document_type_field,
    platform_ref.schema_release,
    platform_ref.schema_artifact
    TO platform_ref_reader;
