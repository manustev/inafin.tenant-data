-- =============================================================================
-- Shared migration 025 — platform identity and access: who can act, and with
-- which permissions, before any tenant role is ever assumed.
--
-- Rebuilt from inafin-api's withdrawn proposal (migrations/platform/0001_
-- identity_access.sql, see CLAUDE.md's "platform migration chain" entry for
-- the full withdrawal history) with one deliberate change: principal_tenant_
-- role.tenant_id references app.tenant_registry(tenant_id), NOT a recreated
-- platform_ref.tenant. app.tenant_registry (001_app.sql) is this repo's
-- canonical tenant record — provisioning, lifecycle, and physical schema
-- mapping all already live there. A second, independently-writable "tenant"
-- table is exactly the drift this rebuild exists to close.
--
-- These are control-plane facts, not tenant-isolated operational data — a
-- principal's role assignment has to be resolvable BEFORE `SET LOCAL ROLE
-- t_<slug>_*` decides which tenant schema a request may touch. See migration
-- 030 for the app_login grants this implies, and API-CONTRACT.md for what
-- inafin-api may read/write here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS platform_ref.api_principal (
    principal_id     uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    principal_type   text NOT NULL CHECK (principal_type IN ('USER', 'SERVICE')),
    external_subject text NOT NULL UNIQUE,
    display_name     text NOT NULL,
    status           text NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN ('ACTIVE', 'SUSPENDED')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL,
    modified_at      timestamptz NOT NULL DEFAULT now(),
    modified_by      text NOT NULL
);

COMMENT ON COLUMN platform_ref.api_principal.external_subject IS
    'The signed identity claim (Keycloak subject) this principal resolves '
    'from. ARCHITECTURE.md 5.6 — real JWT verification is inafin-api''s, not '
    'built here.';

CREATE TABLE IF NOT EXISTS platform_ref.role (
    role_id      uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    role_code    text NOT NULL UNIQUE,
    display_name text NOT NULL,
    description  text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   text NOT NULL,
    modified_at  timestamptz NOT NULL DEFAULT now(),
    modified_by  text NOT NULL
);

-- Shape matches the real INSERTs inafin-api's withdrawn migration 021 already
-- performed against this table — carried forward verbatim, not reinvented.
CREATE TABLE IF NOT EXISTS platform_ref.permission (
    permission_code text PRIMARY KEY,
    display_name    text NOT NULL,
    description     text NOT NULL,
    is_enabled      boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL,
    modified_at     timestamptz NOT NULL DEFAULT now(),
    modified_by     text NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_ref.role_permission (
    role_id         uuid NOT NULL REFERENCES platform_ref.role (role_id),
    permission_code text NOT NULL
                        REFERENCES platform_ref.permission (permission_code),
    PRIMARY KEY (role_id, permission_code)
);

-- A principal's role is always scoped to a tenant — there is no global
-- cross-tenant role. Wanting one is the "explicit, privileged control-plane
-- capability" the user named, not an ambient grant; unbuilt on purpose.
CREATE TABLE IF NOT EXISTS platform_ref.principal_tenant_role (
    principal_id uuid NOT NULL REFERENCES platform_ref.api_principal (principal_id),
    tenant_id    uuid NOT NULL REFERENCES app.tenant_registry (tenant_id),
    role_id      uuid NOT NULL REFERENCES platform_ref.role (role_id),
    granted_at   timestamptz NOT NULL DEFAULT now(),
    granted_by   text NOT NULL,
    PRIMARY KEY (principal_id, tenant_id, role_id)
);

CREATE INDEX IF NOT EXISTS principal_tenant_role_lookup_idx
    ON platform_ref.principal_tenant_role (principal_id, tenant_id);

REVOKE ALL ON platform_ref.api_principal FROM PUBLIC;
REVOKE ALL ON platform_ref.role FROM PUBLIC;
REVOKE ALL ON platform_ref.permission FROM PUBLIC;
REVOKE ALL ON platform_ref.role_permission FROM PUBLIC;
REVOKE ALL ON platform_ref.principal_tenant_role FROM PUBLIC;

-- Tenant roles reach these read-only through platform_ref_reader (001_app.sql).
-- app_login's own ambient access is granted separately in migration 030, with
-- the invariant-#2 exception documented there.
GRANT SELECT ON platform_ref.api_principal, platform_ref.role,
    platform_ref.permission, platform_ref.role_permission,
    platform_ref.principal_tenant_role
    TO platform_ref_reader;
