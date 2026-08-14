-- =============================================================================
-- Shared migration 024 — a general-purpose pgcrypto schema.
--
-- Needed by two independent consumers: platform identity/access rows
-- (migration 025) and tenant connector/workspace tables (tenant migrations
-- 021-025) both use crypto.gen_random_uuid() for their primary keys, and
-- connector_configuration additionally uses crypto.pgp_sym_encrypt/decrypt
-- for at-rest credential encryption. Kept as its own schema, not folded into
-- platform_ref, so a future REVOKE ALL on platform_ref (Phase 2's read-only
-- replica plan, ARCHITECTURE.md 4) never has to carve out an exception for a
-- function grant.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS crypto AUTHORIZATION tenant_migrate;
REVOKE ALL ON SCHEMA crypto FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA crypto;
