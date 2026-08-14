# API contract — what `inafin-api` may read and write

`inafin-tenant-data` owns all database schema, migrations, grants,
provisioning, and platform reference data. `inafin-api` owns API code and
versioned database contracts only — **it must never place executable
migrations in this repo's `migrations/` directories, or apply migrations at
startup.** This document is the contract that replaces that arrangement.

Background: `inafin-api` had drafted `migrations/platform/0001`–`0004` and a
shared/tenant migration sequence numbered `017`–`022`/`021`–`024` as
proposals for transfer into this repo. They collided with this repo's own
numbering, referenced tables that didn't exist anywhere, and one file
(`022_api_admin_tenancy.sql`) introduced Row Level Security — a different
isolation model than this repo's GRANT/`SET LOCAL ROLE` boundary. That
sequence is **withdrawn**. This document describes what replaced it:
shared migrations `024`–`030` and tenant migrations `021`–`025`.

Changes crossing this boundary go: tenant-data ships an additive (or
coordinated breaking) migration first, this contract is updated to match,
then `inafin-api` adopts it. Obsolete compatibility paths are removed only
after adoption is confirmed.

## Minimum migration version

- Shared chain: `032_platform_onboarding_contract_reconciliation.sql`
- Tenant chain: `025_admin_operational_tables.sql` (every tenant)

`platform_ref.api_principal` now carries `issuer` (nullable, unbackfilled —
migration `031`, from `inafin-api`'s `docs/tenant-data-api-compatibility.sql`
hand-off). The `(issuer, external_subject)` uniqueness `inafin-api` needs is
NOT enforced yet — there is no identity-provider source of truth in this
workspace to backfill existing rows from. Do not rely on that pair being
unique until a follow-up migration adds the backfill, `NOT NULL`, and the
index, in that order.

### Onboarding tables — reconciled to `inafin-api`'s real write contract (migration `032`)

Migrations `026`–`028` were built from table names alone and guessed columns.
`inafin-api`'s hand-off spec said the guess was "materially different" from
its actual write contract and named the required shape for every table.
Migration `032` adopts that shape verbatim — read the migration file itself
for full column lists; the load-bearing points:

- **`onboarding_customer`** gained `customer_code` (`UNIQUE NOT NULL`) and
  `is_enabled`; `status` was renamed `onboarding_status` and its vocabulary
  changed to `DRAFT | IN_PROGRESS | COMPLETED | CANCELLED` (previously
  `DRAFT | IN_PROGRESS | SUBMITTED | APPROVED | REJECTED` — do not rely on the
  old values). Altered in place, not dropped/recreated — `tenant_customer`
  and every tenant's `deboarding_case.customer_id` FK to it and both survive
  this migration unmodified.
- **`principal_customer_access.access_level`** vocabulary is now
  `READ | WRITE | ADMIN` (previously `OWNER | COLLABORATOR | VIEWER`).
- **`customer_profile`** absorbed `pan`/`cin`/address-shaped fields that used
  to live on `onboarding_business_profile`; `onboarding_business_profile`
  itself is now operating-model fields (`activities`, `business_model`,
  `b2b_mix`, `locations_count`, `annual_turnover_band`,
  `monthly_invoice_volume_band`) — the two tables do not share the columns
  026 originally gave them.
- **`gst_registration`**: `state_code` → `state`, `status` →
  `registration_status`, plus new `registration_type` and `is_enabled`. No
  `CHECK` vocabulary on `registration_type`/`registration_status` —
  `inafin-api` has not enumerated one; free text until it does.
- **`customer_document`**: rebuilt around `inafin-api`'s own document
  workflow — `document_name` (free text), `category`
  (`COMPANY | GST | BUSINESS`), `requirement`
  (`Required | Recommended | Optional`), `document_status`
  (`Uploaded | Missing | Verifying`). The registry-keyed `doc_type_code`
  column from the old shape is kept as an **optional, nullable** bridge to
  `platform_ref.document_type` — not part of this contract, never required,
  safe to leave `NULL` on every insert. `data_requirement` carries the same
  optional `doc_type_code` bridge for the same reason.
- **`onboarding_state.state`** is a single opaque `jsonb` document — the old
  `current_step`/`completed_steps[]` typed columns are gone. Treat this as
  persisted wizard state, not a status summary.
- **`smart_question_run`** gained `input_fingerprint`, `recommender_type`,
  `recommender_version`, `failure_reason`; `status` vocabulary is now
  `GENERATED | FAILED | SUPERSEDED`. Exactly one `GENERATED` row per customer
  is enforced by a partial unique index on `customer_id` — a second
  `INSERT ... status = 'GENERATED'` for the same customer will fail the
  index, not silently succeed; supersede the prior run's status first.
- **`hsn_master`/`sac_master`** columns are now exactly `hsn_code` /
  `hsn_description`, `gst_rate`, `is_active` (`sac_code` / `sac_description`
  for the SAC table) — the old `effective_from`/`effective_to` validity
  window is gone. Still seeded empty; see `TODO.md`.

No table in this section changed which role or grant reaches it — the
ambient `app_login` access from migration `030` (read-only lookups vs.
`SELECT, INSERT, UPDATE` on the onboarding-write tables) is unchanged and
was reissued by `032` against the new column shapes.

## How `inafin-api` connects

Two distinct access patterns, and they must not be conflated:

1. **Control-plane / pre-authorization reads and writes** — resolving which
   tenant a caller belongs to, customer onboarding — go through `app_login`
   directly, **ambiently, with no `SET LOCAL ROLE`**. This is a narrow,
   deliberate exception (shared migration `030`) to this repo's usual rule
   that `app_login` holds nothing until a role is assumed. The exact table
   list below is exhaustive — `app_login` has no other ambient privilege
   anywhere, and `app.apply_tenant_grants`'s REVOKE-then-GRANT baseline
   strips anything else automatically.

2. **Tenant-scoped operational data** (connectors, workspace/GST/notice
   records, Data Hub uploads, the admin console) goes through
   `SET LOCAL ROLE t_<slug>_recon` — the same role and the same path every
   other tenant-scoped read/write in this system uses. **There is no
   separate admin path.** If the admin console ever needs a genuine
   cross-tenant query, that is an explicit new privileged capability to be
   designed, not an ambient session variable.

## Tables/views `inafin-api` may touch

### Ambient, via `app_login` (control-plane — `platform_ref` unless noted)

Read-only (`SELECT`):

| Object | Purpose |
|---|---|
| `app.v1_tenant_directory` | `slug, tenant_id, status` only — never the base `app.tenant_registry` table, and never `bucket`. |
| `document_type` | Onboarding document picklist; also the ingestion registry. |
| `hsn_master`, `sac_master` | Onboarding product/service code picklists. Seeded **empty** — no real code list exists yet; do not build against real data until it's populated. |
| `api_principal`, `principal_tenant_role`, `role`, `role_permission`, `permission` | Identity/authorization resolution. |
| `principal_customer_access`, `tenant_customer` | Which principal may act on which customer; which tenant a customer resolves to. |

Read/write (`SELECT, INSERT, UPDATE` — no `DELETE`):

| Object |
|---|
| `onboarding_customer` |
| `customer_profile` |
| `onboarding_business_profile` |
| `onboarding_product_service` |
| `gst_registration` |
| `customer_document` |
| `data_requirement` |
| `onboarding_state` |
| `smart_question_run`, `smart_question`, `smart_question_response` |

### Via `SET LOCAL ROLE t_<slug>_recon` (tenant-scoped — `{{gold}}` schema)

| Object | Migration |
|---|---|
| `connector_configuration` | tenant `021` |
| `workspace_assessment`, `workspace_rule_result`, `workspace_evidence`, `workspace_comment`, `workspace_decision`, `workspace_activity`, `external_verification`, `reconciliation_result`, `gst_document`, `gst_document_fact`, `gst_document_validation`, `gst_notice`, `gst_notice_paragraph`, `notice_response_draft`, `v1_command_center_data_quality`, `v1_command_center_notices`, `v1_command_center_actions` | tenant `022` |
| `datahub_upload_request` | tenant `023`–`024` |
| `admin_role`, `admin_user`, `admin_contact`, `notification_channel`, `escalation_rule`, `deboarding_case` | tenant `025` |

Standard tenant grants apply — `recon` gets `SELECT, INSERT, UPDATE, DELETE`
on gold base tables (`001_app.sql`, `apply_tenant_grants` step 6). No table
in this list needs, or has, a hand-written grant of its own.

## What `platform_ref.tenant` was, and why it's gone

`inafin-api`'s withdrawn draft had its own `platform_ref.tenant` table.
`app.tenant_registry` (`001_app.sql`) is the same concept — tenant identity,
slug, physical schema mapping — and already existed as the canonical,
provisioning-owned record. There is exactly one tenant registry now.
**`inafin-api` must migrate any FK or authorization query that referenced
`platform_ref.tenant` to `app.v1_tenant_directory` instead** (never the base
`app.tenant_registry` table — it holds `bucket`, which is not `inafin-api`'s
to read).

## Outstanding work on the `inafin-api` side

Not built here, and not this repo's to build:

- Remove the RLS/`tenant_id` admin path entirely; route admin requests
  through `SET LOCAL ROLE t_<slug>_recon` like every other tenant request.
- Migrate authorization queries and foreign keys off `platform_ref.tenant`
  onto `app.v1_tenant_directory`.
- Move admin SQL from the old `platform_ref.admin_*` shape onto the
  `{{gold}}`-scoped tables above.
