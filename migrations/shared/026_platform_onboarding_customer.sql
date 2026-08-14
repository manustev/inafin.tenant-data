-- =============================================================================
-- Shared migration 026 — customer onboarding: the customer record itself,
-- who may act on it, and the Smart Questions flow that qualifies it.
--
-- Rebuilt from inafin-api's withdrawn proposal (migrations/platform/0002_
-- onboarding_smart_questions.sql). tenant_customer is folded in here rather
-- than kept with the identity/access tables (025) because it FKs directly to
-- onboarding_customer, defined in this file — see CLAUDE.md's platform
-- migration chain entry for the withdrawal history.
--
-- Control-plane data, same reasoning as 025: a customer is onboarded, and a
-- tenant_customer row resolves which tenant they belong to, before any
-- tenant role is assumed. See migration 030 for app_login's grants here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS platform_ref.onboarding_customer (
    customer_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    legal_name  text NOT NULL,
    status      text NOT NULL DEFAULT 'DRAFT'
                    CHECK (status IN ('DRAFT', 'IN_PROGRESS', 'SUBMITTED', 'APPROVED', 'REJECTED')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    modified_at timestamptz NOT NULL DEFAULT now(),
    modified_by text NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_ref.principal_customer_access (
    principal_id  uuid NOT NULL REFERENCES platform_ref.api_principal (principal_id),
    customer_id   uuid NOT NULL REFERENCES platform_ref.onboarding_customer (customer_id),
    access_level  text NOT NULL CHECK (access_level IN ('OWNER', 'COLLABORATOR', 'VIEWER')),
    granted_at    timestamptz NOT NULL DEFAULT now(),
    granted_by    text NOT NULL,
    PRIMARY KEY (principal_id, customer_id)
);

CREATE TABLE IF NOT EXISTS platform_ref.onboarding_business_profile (
    customer_id        uuid PRIMARY KEY
                            REFERENCES platform_ref.onboarding_customer (customer_id),
    business_type       text NOT NULL,
    incorporation_date  date,
    pan                 text,
    cin                 text,
    registered_address   jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text NOT NULL,
    modified_at         timestamptz NOT NULL DEFAULT now(),
    modified_by         text NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_ref.onboarding_product_service (
    product_service_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id         uuid NOT NULL
                            REFERENCES platform_ref.onboarding_customer (customer_id),
    name                text NOT NULL,
    hsn_or_sac_code     text,
    description         text NOT NULL DEFAULT '',
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text NOT NULL
);

CREATE INDEX IF NOT EXISTS onboarding_product_service_customer_idx
    ON platform_ref.onboarding_product_service (customer_id);

CREATE TABLE IF NOT EXISTS platform_ref.smart_question_run (
    run_id       uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id  uuid NOT NULL
                     REFERENCES platform_ref.onboarding_customer (customer_id),
    status       text NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED')),
    started_at   timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS platform_ref.smart_question (
    question_id   uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    run_id        uuid NOT NULL REFERENCES platform_ref.smart_question_run (run_id),
    question_code text NOT NULL,
    prompt        text NOT NULL,
    sequence      integer NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS platform_ref.smart_question_response (
    question_id     uuid PRIMARY KEY
                        REFERENCES platform_ref.smart_question (question_id),
    response_value  jsonb NOT NULL,
    answered_at     timestamptz NOT NULL DEFAULT now(),
    answered_by     text NOT NULL
);

-- Customer -> tenant resolution. This is what decides which tenant schema a
-- request may touch, so it has to be reachable BEFORE `SET LOCAL ROLE`. The
-- withdrawn migration referenced platform_ref.tenant here; app.tenant_registry
-- is the unified, canonical replacement (see 025's header).
CREATE TABLE IF NOT EXISTS platform_ref.tenant_customer (
    tenant_id   uuid NOT NULL REFERENCES app.tenant_registry (tenant_id),
    customer_id uuid NOT NULL REFERENCES platform_ref.onboarding_customer (customer_id),
    is_enabled  boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    modified_at timestamptz NOT NULL DEFAULT now(),
    modified_by text NOT NULL,
    PRIMARY KEY (tenant_id, customer_id),
    UNIQUE (customer_id)
);

CREATE INDEX IF NOT EXISTS tenant_customer_authorization_idx
    ON platform_ref.tenant_customer (tenant_id, customer_id) WHERE is_enabled;

REVOKE ALL ON platform_ref.onboarding_customer FROM PUBLIC;
REVOKE ALL ON platform_ref.principal_customer_access FROM PUBLIC;
REVOKE ALL ON platform_ref.onboarding_business_profile FROM PUBLIC;
REVOKE ALL ON platform_ref.onboarding_product_service FROM PUBLIC;
REVOKE ALL ON platform_ref.smart_question_run FROM PUBLIC;
REVOKE ALL ON platform_ref.smart_question FROM PUBLIC;
REVOKE ALL ON platform_ref.smart_question_response FROM PUBLIC;
REVOKE ALL ON platform_ref.tenant_customer FROM PUBLIC;

GRANT SELECT ON platform_ref.onboarding_customer, platform_ref.principal_customer_access,
    platform_ref.onboarding_business_profile, platform_ref.onboarding_product_service,
    platform_ref.smart_question_run, platform_ref.smart_question,
    platform_ref.smart_question_response, platform_ref.tenant_customer
    TO platform_ref_reader;
