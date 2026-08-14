-- Tenant migration 021 — per-tenant ERP connector configuration.
-- Credentials are PGP-encrypted bytea. Plaintext credentials must never be persisted.
-- Rebuilt from inafin-api's withdrawn proposal — see CLAUDE.md's platform
-- migration chain entry. Unchanged from the original draft: already correctly
-- {{gold}}-scoped, isolated by schema like every other tenant table.

CREATE TABLE IF NOT EXISTS {{gold}}.connector_configuration (
    connector_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    connector_type text NOT NULL CHECK (connector_type IN ('SAP', 'ORACLE', 'TALLY', 'ZOHO', 'SFTP', 'CUSTOM')),
    connector_name text NOT NULL,
    connection_method text NOT NULL,
    encrypted_credentials bytea NOT NULL,
    status text NOT NULL DEFAULT 'DISCONNECTED' CHECK (status IN ('CONNECTED', 'DISCONNECTED', 'ERROR', 'SYNCING')),
    last_sync_at timestamptz,
    records_synced bigint NOT NULL DEFAULT 0 CHECK (records_synced >= 0),
    error_count integer NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL DEFAULT current_user,
    modified_at timestamptz NOT NULL DEFAULT now(),
    modified_by text NOT NULL DEFAULT current_user,
    UNIQUE (connector_type, connector_name)
);

COMMENT ON COLUMN {{gold}}.connector_configuration.encrypted_credentials IS
    'PGP ciphertext. Encrypt/decrypt only with the per-process CONNECTOR_ENCRYPTION_KEY; never expose in views or API responses.';
