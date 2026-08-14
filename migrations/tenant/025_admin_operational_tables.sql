-- Tenant migration 025 — administration control-plane data: roles, users,
-- contacts, escalation rules, notification channels, deboarding cases.
--
-- Rebuilt from inafin-api's withdrawn proposal (shared migrations 018/022).
-- The original draft put these in platform_ref and isolated them with a
-- tenant_id column plus ROW LEVEL SECURITY — a second isolation model next
-- to this repo's GRANT/SET LOCAL ROLE boundary, and never reconciled with it
-- (021 in the same withdrawn batch had already moved away from RLS for
-- everything else). There is no current requirement for a cross-tenant
-- admin-console query, so these tables move here instead: schema-per-tenant,
-- isolated by GRANT like every other piece of tenant-owned data, with no new
-- grant code needed at all — app.apply_tenant_grants (001_app.sql, step 6)
-- already sweeps every non-`__` {{gold}} table into the standard recon/
-- support grant. See CLAUDE.md's platform migration chain entry and
-- API-CONTRACT.md for what inafin-api must change to consume this.

CREATE TABLE IF NOT EXISTS {{gold}}.admin_role (
  role_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), role_name text NOT NULL UNIQUE, description text NOT NULL,
  permissions text[] NOT NULL DEFAULT '{}', is_system boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {{gold}}.admin_user (
  user_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), name text NOT NULL, email text NOT NULL UNIQUE, phone text,
  role_id uuid NOT NULL REFERENCES {{gold}}.admin_role(role_id), status text NOT NULL CHECK(status IN ('Active','Invited','Suspended')),
  last_active_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {{gold}}.escalation_rule (
  escalation_rule_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), name text NOT NULL, trigger_name text NOT NULL,
  risk_levels text[] NOT NULL DEFAULT '{}', levels jsonb NOT NULL DEFAULT '[]', active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {{gold}}.notification_channel (
  notification_channel_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), channel text NOT NULL CHECK(channel IN ('Email','SMS','Webhook','In-App')),
  enabled boolean NOT NULL DEFAULT true, target text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {{gold}}.admin_contact (
  contact_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
  name text NOT NULL, role text NOT NULL, email text NOT NULL UNIQUE, phone text NOT NULL,
  phone_verified boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);

-- customer_id references platform_ref.onboarding_customer (026) — a
-- cross-schema FK within the same database, the same pattern
-- entitlement_instrument already uses against platform_ref.document_type.
CREATE TABLE IF NOT EXISTS {{gold}}.deboarding_case (
  deboarding_case_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
  customer_id uuid NOT NULL REFERENCES platform_ref.onboarding_customer(customer_id),
  customer_name text NOT NULL, reason text NOT NULL, requested_by text NOT NULL, requested_at timestamptz NOT NULL DEFAULT now(), target_close_date date,
  stage text NOT NULL CHECK(stage IN ('Requested','Data Export','Pending Approvals','Final Reconciliation','Access Revoked','Closed','Cancelled')),
  checklist jsonb NOT NULL DEFAULT '[]', notes text, cancelled_reason text
);
