-- =============================================================================
-- Shared migration 037 — app_login reads the schema catalogue.
--
-- THIS FILE AMENDS SHARED MIGRATION 030. Read 030 first: it states the
-- invariant-#2 exception (CLAUDE.md), why app_login holds ANY ambient grant at
-- all, and the rule that the list "grow here, in one place that is easy to
-- find, diff, and audit — never as an ad hoc GRANT bolted onto an unrelated
-- migration."
--
-- 030 cannot itself be edited: it is applied and checksum-pinned. So this file
-- IS that one place for the schema catalogue — a migration whose SOLE purpose
-- is extending the ambient list, named after 030 so the two sort together and
-- `ls migrations/shared/ | grep grants` finds both. That honours 030's intent
-- (never bolt a grant onto an unrelated migration) rather than its literal
-- wording, which the pinning rule makes impossible to satisfy.
--
-- WHY THIS QUALIFIES FOR THE EXCEPTION. Identical in character to the
-- document_type grant 030 already carries, and for the same reason: the portal
-- shows a tenant which document types exist and what schema each needs BEFORE
-- any tenant role can be assumed — during onboarding, when the caller may not
-- yet be attached to a tenant at all. There is no `SET LOCAL ROLE` available
-- to gate it behind.
--
-- WHAT IT IS NOT. Both tables are platform_ref reference data describing
-- DOCUMENT TYPES, identical for every tenant. Neither holds tenant data,
-- neither is in bronze/silver/gold, and neither is writable — SELECT only. A
-- tenant's OWN pinned schema version is a different thing entirely and lives
-- in their Bronze schema, reachable only through their own role.
-- =============================================================================

GRANT SELECT ON
    platform_ref.document_type_schema,
    platform_ref.document_type_field
    TO app_login;
