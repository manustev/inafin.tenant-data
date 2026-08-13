-- Shared migration 018 — Platform administration runtime facts.
CREATE TABLE IF NOT EXISTS platform_ref.admin_role (
  role_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), role_name text NOT NULL UNIQUE, description text NOT NULL,
  permissions text[] NOT NULL DEFAULT '{}', is_system boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS platform_ref.admin_user (
  user_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), name text NOT NULL, email text NOT NULL UNIQUE, phone text,
  role_id uuid NOT NULL REFERENCES platform_ref.admin_role(role_id), status text NOT NULL CHECK(status IN ('Active','Invited','Suspended')),
  last_active_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS platform_ref.escalation_rule (
  escalation_rule_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), name text NOT NULL, trigger_name text NOT NULL,
  risk_levels text[] NOT NULL DEFAULT '{}', levels jsonb NOT NULL DEFAULT '[]', active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS platform_ref.notification_channel (
  notification_channel_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), channel text NOT NULL CHECK(channel IN ('Email','SMS','Webhook','In-App')),
  enabled boolean NOT NULL DEFAULT true, target text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS platform_ref.deboarding_case (
  deboarding_case_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), customer_id uuid NOT NULL REFERENCES platform_ref.onboarding_customer(customer_id),
  customer_name text NOT NULL, reason text NOT NULL, requested_by text NOT NULL, requested_at timestamptz NOT NULL DEFAULT now(), target_close_date date,
  stage text NOT NULL CHECK(stage IN ('Requested','Data Export','Pending Approvals','Final Reconciliation','Access Revoked','Closed','Cancelled')),
  checklist jsonb NOT NULL DEFAULT '[]', notes text, cancelled_reason text
);
