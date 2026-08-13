-- Shared migration 021 — API tenant/customer boundary and least privilege.
--
-- The Go tenant API enters every tenant request through t_<slug>_recon using
-- SET LOCAL ROLE. app_login remains NOINHERIT and must never hold ambient
-- privileges on a tenant schema.

CREATE TABLE IF NOT EXISTS platform_ref.tenant_customer (
    tenant_id uuid NOT NULL REFERENCES platform_ref.tenant(tenant_id),
    customer_id uuid NOT NULL REFERENCES platform_ref.onboarding_customer(customer_id),
    is_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL,
    modified_at timestamptz NOT NULL DEFAULT now(),
    modified_by text NOT NULL,
    PRIMARY KEY (tenant_id, customer_id),
    UNIQUE (customer_id)
);

CREATE INDEX IF NOT EXISTS tenant_customer_authorization_idx
    ON platform_ref.tenant_customer (tenant_id, customer_id) WHERE is_enabled;

-- Tenant runtime roles inherit this deliberately small platform read model.
-- Authorization itself runs before SET LOCAL ROLE as app_login; operational
-- requests need only customer resolution, document metadata, and company/GSTIN.
GRANT SELECT ON platform_ref.tenant_customer, platform_ref.onboarding_customer,
  platform_ref.gst_registration, platform_ref.document_type
TO app_login, platform_ref_reader;

-- Permission vocabulary is a shared contract between role administration,
-- route policies, and deployment provisioning. It is safe to re-run.
INSERT INTO platform_ref.permission (permission_code, display_name, description, created_by, modified_by)
VALUES
  ('connectors.manage', 'Manage connectors', 'Create and update tenant connector configurations.', 'migration-021', 'migration-021'),
  ('datahub.manage', 'Manage Data Hub', 'Upload and inspect tenant data sources.', 'migration-021', 'migration-021'),
  ('commandcenter.view', 'View command center', 'Read tenant command-center summaries.', 'migration-021', 'migration-021'),
  ('gst.view', 'View GST workspace', 'Read GST workspace records and evidence.', 'migration-021', 'migration-021'),
  ('gst.decide', 'Decide GST workspace', 'Create GST decisions, evidence, and comments.', 'migration-021', 'migration-021'),
  ('ca.review', 'Review CA notices', 'Read notices and create/revise response drafts.', 'migration-021', 'migration-021'),
  ('ca.approve', 'Approve CA responses', 'Approve final CA response drafts.', 'migration-021', 'migration-021'),
  ('admin.users', 'Manage users', 'Manage tenant users and their roles.', 'migration-021', 'migration-021'),
  ('admin.roles', 'Manage roles', 'Manage tenant administration roles.', 'migration-021', 'migration-021'),
  ('admin.contacts', 'Manage contacts', 'Manage tenant contacts.', 'migration-021', 'migration-021'),
  ('admin.notifications', 'Manage notification channels', 'Manage tenant notification channels.', 'migration-021', 'migration-021'),
  ('admin.escalation', 'Manage escalation rules', 'Manage tenant escalation rules.', 'migration-021', 'migration-021'),
  ('admin.deboarding', 'Manage deboarding', 'Manage tenant deboarding cases.', 'migration-021', 'migration-021')
ON CONFLICT (permission_code) DO UPDATE
SET display_name = EXCLUDED.display_name, description = EXCLUDED.description,
    is_enabled = true, modified_by = EXCLUDED.modified_by, modified_at = now();

-- 020 temporarily granted tenant objects directly to app_login. Remove that
-- ambient access and rely on the per-tenant role matrix from 001 instead.
DO $$
DECLARE schema_name text;
BEGIN
  FOR schema_name IN
    SELECT nspname FROM pg_namespace WHERE nspname ~ '^t_[a-z0-9_]+_(bronze|silver|gold)$'
  LOOP
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM app_login', schema_name);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM app_login', schema_name);
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM app_login', schema_name);
  END LOOP;
END $$;
