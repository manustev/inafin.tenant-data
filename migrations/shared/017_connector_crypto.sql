-- Shared migration 017 — cryptographic primitive required by tenant connector secrets.
-- The encryption key is never stored in PostgreSQL; the API supplies it per transaction.

CREATE SCHEMA IF NOT EXISTS crypto AUTHORIZATION tenant_migrate;
REVOKE ALL ON SCHEMA crypto FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA crypto;
