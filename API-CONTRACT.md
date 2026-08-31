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

- Shared chain: `040_schema_catalogue_reader_grants.sql`
- Tenant chain: `029_drop_v1_schema_pin_view.sql` (every tenant)

> **READ THIS IF YOU ARE UPGRADING FROM BELOW SHARED `038`.** Every ambient
> `platform_ref` grant migration `030` declared — reads AND the onboarding
> writes — **has never worked**. `app_login` held the table privileges but was
> never granted `USAGE ON SCHEMA platform_ref`, and a table privilege is
> unreachable without it:
>
> ```
> SELECT count(*) FROM platform_ref.document_type;
> ERROR:  permission denied for schema platform_ref
> ```
>
> This was true from 2026-08-14 (when `030` was applied) until shared migration
> `038` on 2026-08-24, and it was invisible to the verification done at the
> time because that queried `information_schema.role_table_grants` — which
> correctly showed every GRANT as present. A recorded privilege is not a usable
> one. `app.v1_tenant_directory` worked throughout (schema `app` grants USAGE
> to PUBLIC), which is why tenant resolution appeared to function while
> everything after step one did not.
>
> If `inafin-api` has been working around this — connecting as a superuser,
> caching a picklist, or catching the error — that workaround can now be
> removed. If it has been failing, shared `038` is the fix and no `inafin-api`
> change is required.

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

## Uploading ERP files — the Bronze intake contract

A THIRD access pattern, distinct from the two above. Patterns 1 and 2 are
both direct Postgres connections (`app_login` ambient, or `SET LOCAL ROLE
t_<slug>_recon`); this one is an **HTTP call to a separate service** —
`uvicorn src.api.app:app`, this repo's own REST ingestion surface. If
`inafin-api`'s upload feature is going to let a tenant hand over ERP files
(sales/purchase registers, GST returns, any of the 125 in-scope document
types), it is a CLIENT of this API — the same relationship `inafin-portal`
has, not a peer writing to the same storage.

### There is no Bronze folder-structure contract to write to directly

**Do not write files into a tenant's Bronze bucket, and do not insert rows
into `artefact_ledger`, from `inafin-api`.** The object key shape Bronze
actually uses today —

```
bronze/{received_at:%Y/%m/%d}/{ingest_id}.{extension}
```

— is `BronzeIngestionService.receive`'s own internal implementation detail
(`src/bronze/service.py`). It is not versioned, not published, and not a
contract anyone outside this repo may depend on or reproduce. A file placed
directly at a key like this, with no `artefact_ledger` row pointing at it, is
invisible to every downstream consumer — `GET /artefacts/{id}/status`,
`dispatch_load`, Silver promotion, the customer-facing "did my upload
succeed" query — none of them discover an object by listing the bucket; all
of them go through the ledger.

Writing directly would also skip every gate this repo has built specifically
to prevent bad data from becoming an artefact at all:

| Gate | What it catches | Where |
|---|---|---|
| File-shape check | empty file, oversized, disallowed extension | `src/bronze/filecheck.py` |
| **Document-type check** | `document_type` is not a real, in-scope registry code (fixed 2026-08-24 — previously ANY string silently defaulted to a retired code) | `BronzeIngestionService._check_document_type_in_scope` |
| Hash/dedup | identical bytes re-uploaded by the same tenant | `src/bronze/service.py` |
| Virus scan | malware, if scanning is enabled for the deployment | `src/bronze/scan.py` |
| Schema pin | records which published schema release this tenant was handed for this type, automatically | `src/catalogue/pin.py` |

A direct write bypasses all five, silently. The only supported way to get a
file into Bronze is the endpoints below.

### The endpoints

All under `POST/GET /artefacts` (`src/api/routes_ingest.py`). Auth is a
bearer token resolved to a tenant slug (`Settings.api_tenant_tokens` —
`StaticTokenAuth`, see `src/api/auth.py`; still a placeholder per
ARCHITECTURE.md 5.6, not yet the signed Keycloak claim, but already the only
way a tenant is identified — never a path parameter or request field).
**This token is a separate credential from anything `inafin-api` uses for
Postgres** — provisioning it for `inafin-api`'s deployment is a prerequisite
this document flags, not something either side can infer from the other.

**`POST /artefacts`** — one file.

- Form fields: `entity_id` (uuid, required), `document_type` (string,
  **required, no default** — a caller must declare a real registry code; see
  the schema-catalogue section below for how to source valid codes),
  `file` (multipart).
- `201` → `UploadResponse { ingest_id, bucket, object_key, size_bytes, deduplicated }`.
- `422` → the file or the declared type was refused (`IntakeRejected`'s
  message verbatim in `detail` — e.g. `"document_type 'FOO' is not a
  recognised document type"`, `"file extension '.exe' is not accepted"`).

**`POST /artefacts/batch`** — several files, ONE `document_type` and
`entity_id` for all of them.

- Same form fields as above, plus `files` (multiple parts, same field name,
  up to 100 per request).
- Always `200` → `BatchUploadResponse { accepted_count, rejected_count, items[] }`.
  Each `BatchUploadItem` carries its own `ok`/`error` — **one bad file in the
  batch never fails the files next to it**; the HTTP status cannot represent
  a mix, so the body always must be inspected.
- **Deliberately single-type per call.** `document_type` is one field, not a
  parallel array keyed to `files` — if a tenant is dropping several document
  types at once, `inafin-api` makes one `/artefacts/batch` call per type, not
  one call with mismatched arrays.
- `422` only for zero files, or more than 100.

**`POST /artefacts/{ingest_id}/trigger`** — start Bronze→Silver processing
for an artefact already uploaded.

- Body (`TriggerRequest`): `doc_type_code` (required — must match what the
  artefact was uploaded as), `period_start`/`period_end`/`gstin` (required
  only by some dispatch mechanisms — a PDF-shaped type needs none of them,
  a register/GSTN-return type needs all three; an omitted field a mechanism
  DOES need is a `422`, not a silent no-op).
- `202` → `TriggerResponse { trigger_id, ingest_id, doc_type_code, requested_at, status, mechanism, batch_id }`.
  `status` is one of `ACCEPTED | PARTIAL | QUARANTINED | UNROUTED` —
  `UNROUTED` means this type has no loader built yet (a real, honest
  outcome, not an error — GSTR-1 and 37 other in-scope types are like this
  today); `QUARANTINED` means the document's own content failed Silver's
  validation. Both are durably recorded even though nothing promoted.
- `404` if `ingest_id` does not belong to the calling tenant (isolation by
  `SET LOCAL ROLE`/FK, not a `WHERE` clause `inafin-api` could get wrong).
- `422` if a required dispatch field is missing, or the file's extension has
  no recognised content format.

**`GET /artefacts/{ingest_id}/status`** — the customer-facing "did my upload
succeed" read. `StatusResponse { bronze_ingest_id, status, document_type,
batch_id, row_count, accepted_count, rejected_count, quarantine_reason,
rejections[] }` — `rejections` carries per-ROW detail (`source_line`,
`column_name`, `message`) for a `PARTIAL` register upload, the row-level
equivalent of `/artefacts/batch`'s per-FILE detail.

### Sourcing valid `document_type` codes and expected columns

**Never hardcode the list of valid `document_type` values or a type's
expected columns in `inafin-api`.** Both are published, generated data —
read them from `platform_ref.document_type_schema` /
`document_type_field` (the schema catalogue, see "Serving schemas and
samples to tenants" below) the same way the portal's "download schema"
feature does. That catalogue is exactly what keeps `inafin-api`'s upload UI
and its own pre-flight validation in sync with what `/artefacts` will
actually accept, without either side needing a code change when a new type
ships. A `document_type` that passes `inafin-api`'s own validation but fails
`/artefacts`' `422` means the two have drifted — that is the bug the
catalogue exists to prevent.

### One thing `inafin-api` never has to set

`received_from` — which door an artefact came through — is set **server-side**
by the route itself (`"PORTAL"` for both endpoints above) and is not a field
either endpoint accepts. Nothing for `inafin-api` to pass or get right here.

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
| `document_type_schema` | Per document type: `schema_kind` (TABULAR/DOCUMENT/JSON/UNSPECIFIED — how a tenant supplies it) and `provenance` (DERIVED/DECLARED/PENDING — how trustworthy the field list is). One row for all 125 in-scope types. |
| `document_type_field` | The tenant-facing field list: `ordinal`, `field_name`, `scope` (ROW/HEADER/LINE/DOCUMENT), `data_type`, `required`, `source_label`. **These are export columns, not Silver columns** — `batch_id`, `row_hash` and the bitemporal tail are deliberately absent. |
| `schema_release` | Published releases (`v1`, `v2`, …) and their status. Exactly one is `CURRENT`. |
| `schema_artifact` | One row per published file: `kind` (SCHEMA/SAMPLE), `object_bucket`, `object_key`, `object_version_id`, `sha256`. The bytes live in the platform bucket. |

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

## Serving schemas and samples to tenants

The portal's "download the schema / see a sample" flow reads the four tables
above plus the platform object-store bucket (`<prefix>-platform`, S3 versioning
enabled, no Object Lock). `inafin-api` fetches or presigns by
`schema_artifact.object_key`; it does not need Postgres access to the bytes.

Two things to get right:

1. **Serve a tenant their PINNED release, not `CURRENT`.** On a tenant's first
   upload of a document type, the release then `CURRENT` is pinned to them and
   a byte-identical copy is frozen in their own bucket
   (`t_<slug>_bronze.schema_pin`, tenant migration `028`; the bytes at
   `schema/<release>/<doc_type_code>.json` in the tenant bucket). A tenant
   mid-integration must keep seeing the contract they downloaded — that is the
   entire purpose. `CURRENT` is what a tenant with no pin yet gets.

2. **`schema_pin` is INSERT-only and the current pin is the LATEST row per
   `doc_type_code`** (`ORDER BY pinned_at DESC, id DESC`). Rolling a tenant
   forward to `v2` is a new row, never an update; the `v1` row stays true
   forever. There is no view for this — see tenant migration `029` for why —
   so the tie-break is yours to get right, and `pinned_at` alone is not enough
   (two pins in one transaction share it).

Publishing a release is this repo's job (`scripts/publish_schema_release.py`).
`inafin-api` never writes any of these tables — all four are `SELECT` only.

### Concrete query pattern

**No Bronze folder is involved in serving these files.** They are not tenant
data and never pass through `artefact_ledger`, `ingest_batch`, or anything
under a tenant's `bronze/` object prefix. There are two different reads,
against two different privilege scopes, and the fallback order matters:

**1. Ambient lookup — does this tenant already have a pin?**
`schema_pin` lives in `t_<slug>_bronze`, a real tenant schema, so this is
**not** an ambient `app_login` read like the platform_ref tables above — it
requires `SET LOCAL ROLE t_<slug>_ingest` (or `_support`) inside the
transaction, exactly like every other tenant-schema read in this codebase.
That means the caller must already have resolved which tenant it is (via
`tenant_customer`/`principal_customer_access`, the ambient reads) *before*
this step:

```sql
-- inside a transaction that has already done SET LOCAL ROLE t_<slug>_ingest
SELECT DISTINCT ON (doc_type_code)
       doc_type_code, release_version, object_bucket, object_key
  FROM t_<slug>_bronze.schema_pin
 WHERE doc_type_code = 'PURCHASE_REGISTER'
 ORDER BY doc_type_code, pinned_at DESC, id DESC;
```

A row means the tenant is pinned — serve `object_bucket`/`object_key` from
**their own tenant bucket** (`inafin-tenant-<slug>`) at
`schema/<release_version>/<doc_type_code>.json`. No row means they have never
uploaded that type yet; fall through to step 2.

**2. Ambient lookup — the platform's CURRENT release.**
This one IS a plain `app_login` ambient read, no tenant role needed —
`document_type_schema`/`schema_release`/`schema_artifact` are platform-wide,
identical for every tenant:

```sql
SELECT a.object_bucket, a.object_key, a.content_format,
       s.schema_kind, s.provenance
  FROM platform_ref.schema_artifact a
  JOIN platform_ref.schema_release r ON r.version = a.release_version
  JOIN platform_ref.document_type_schema s ON s.doc_type_code = a.doc_type_code
 WHERE a.doc_type_code = 'PURCHASE_REGISTER'
   AND a.kind = 'SCHEMA'
   AND r.status = 'CURRENT';
```

This row's `object_bucket`/`object_key` point at the **shared platform
bucket** (`<prefix>-platform`), not any tenant bucket — that distinction is
what makes step 1 vs step 2 matter: the pinned copy and the current copy are
never the same object once a new release ships.

**3. Fetch or presign.** Either way, the last step is an S3 `GetObject` (or a
presigned URL) against whichever `(bucket, key)` step 1 or step 2 returned.
`inafin-api` needs its own S3/MinIO credential for this — read-only, scoped to
both the platform bucket and (via the tenant's own credential, however
`inafin-api` already reaches tenant buckets for Bronze downloads today) each
tenant bucket's `schema/` prefix. No new credential shape is being introduced;
this reuses whatever access `inafin-api` already has to read a tenant's own
objects.

**Same pattern for samples** — swap `kind = 'SCHEMA'` for `kind = 'SAMPLE'` in
step 2. Samples are never pinned (only schemas are, since a sample is a
reference example, not a contract a tenant's export must match), so step 1
does not apply — samples are always served from the platform bucket at
whatever release is `CURRENT`.

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
