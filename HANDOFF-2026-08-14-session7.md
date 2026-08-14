# Session handoff — 2026-08-14, seventh session

**This file is a session record, not a design document.** The design of
record is `CLAUDE.md`, `TYPED-TABLES-PLAN.md`, `ARCHITECTURE.md`, `TODO.md`,
and — new this session — `API-CONTRACT.md`. Where this file and those
disagree, they win. **Read `CLAUDE.md`'s "Current state (seventh session)"
(both the main entry and its late-session addendum) and "Next session
starts here" first** — both updated at the end of this session.

Previous handoff: sixth session (Bronze→Silver dispatcher, OCR fallback) —
no separate `HANDOFF-*.md` was written for it; its detail lives entirely in
CLAUDE.md's "Current state (sixth session)".

---

## One-line state

The `inafin-api` migration collision discovered in the sixth session is
resolved: that withdrawn sequence is rebuilt as shared migrations `024`-`031`
and tenant migrations `021`-`025`, `API-CONTRACT.md` now defines the
boundary between the two repos, and one real hand-off from `inafin-api` was
already applied through it. **One open item blocks `inafin-api` actually
integrating**: the onboarding tables' column shapes were guessed, and
`inafin-api` says the guess is wrong. Also: this repo's Postgres is now a
permanently shared dev server — `docker compose down -v` must never be run
again, on this machine or any other pointed at the same instance.

`make lint`/`make typecheck` clean; full suite 542 passed / 2 skipped / the
same one pre-existing `test_isolation.py` ordering failure from the sixth
session (untouched, not caused by this session — see CLAUDE.md item `0a`).

---

## 1. The `inafin-api` migration chain — rebuilt, not restored

Full design and file-by-file detail is in CLAUDE.md's "Current state
(seventh session)" — not repeated here. Summary of what exists now:

- **Shared**: `024_crypto_schema.sql`, `025_platform_identity_access.sql`,
  `026_platform_onboarding_customer.sql`,
  `027_platform_onboarding_profile_and_artifacts.sql`,
  `028_platform_onboarding_state.sql`, `029_platform_catalog_hsn_sac.sql`,
  `030_platform_control_plane_grants.sql`, `031_api_principal_issuer.sql`
  (added later this session — §3 below).
- **Tenant**: `021_connector_configuration.sql`, `022_workspace_runtime.sql`,
  `023_datahub_upload_request.sql`, `024_datahub_upload_idempotency.sql`,
  `025_admin_operational_tables.sql`.
- `platform_ref.tenant` does not exist — `app.tenant_registry` is canonical,
  exposed narrowly via `app.v1_tenant_directory` (slug/tenant_id/status
  only, never `bucket`).
- The six admin tables (`admin_role`, `admin_user`, `admin_contact`,
  `notification_channel`, `escalation_rule`, `deboarding_case`) live in
  `{{gold}}`, isolated by schema/GRANT — no RLS, no `tenant_id` column.
- `app_login` holds a new, narrow, **named** ambient grant list
  (`platform_ref` control-plane tables only — migration `030`) as an
  explicit, documented exception to invariant #2. Verified by direct
  `information_schema.role_table_grants` query, not by inspection: zero
  privileges on any tenant `bronze`/`silver`/`gold` object, and the ambient
  list is *exactly* migration `030`'s named tables.
- New `API-CONTRACT.md` at the repo root is the versioned boundary —
  what `inafin-api` may touch, through which role, and the minimum
  migration version. Read it before making any schema decision that
  affects `inafin-api`.

**Not proven from a genuinely clean cluster.** Verification this session
was against the existing, already-migrated cluster only (`make migrate`
zero drift, full suite, direct grant inspection) — `docker compose down -v`
was offered and explicitly declined, and per §2 below, must never be
offered again.

---

## 2. Standing rule: this Postgres is now a shared dev server — never reset it

Steve is converting this repo's local Postgres container into **one shared
local dev server** used by `inafin-api`, `inafin-portal`, and this repo, to
stop ~10 separate per-repo Postgres containers from overwhelming the
machine. More databases are being added to the same server over time.

**Consequence, permanent, not just "ask first": never run
`docker compose down -v` or any other full database wipe/recreate against
this Postgres again.** A reset here doesn't just clear this repo's own
sandbox anymore — it destroys `inafin-api`'s and `inafin-portal`'s data too,
the same failure mode that already cost the sixth session real,
unrecoverable `inafin-api` migration state once. `CLAUDE.md`'s Workflow
section is updated to say this directly, replacing the old "verify from a
clean cluster" instruction. From now on, verify with targeted DDL
(create/drop/alter individual objects) and `information_schema`/`pg_catalog`
queries against the live cluster — which is exactly how migrations `024`-
`031` were verified this session.

How the schemas map to consumers, per Steve's own description — useful
context for any future schema-placement decision:

- `platform_ref` — read by `inafin-portal` for lookup/picklist data
  (document types, HSN/SAC, onboarding/identity).
- Tenant `bronze`/`silver` — ERP-uploaded data, batch details, processed-file
  status, read by `inafin-api`.
- Tenant `gold` — dashboard data.
- **HSN/SAC master data currently lives on a separate DB server** Steve
  plans to migrate into `platform_ref.hsn_master`/`sac_master` (shared
  migration `029`, seeded empty and waiting for exactly this).

---

## 3. `inafin-api`'s first real hand-off spec — applied

`inafin-api/docs/tenant-data-api-compatibility.sql` arrived this session —
**a documented pattern going forward**: a spec file lives in `inafin-api`'s
own repo, is applied through *this* repo's migration runner as a normal
numbered migration, and is never an executable migration `inafin-api` runs
itself. The file itself says so explicitly in its header.

Its one real statement: `platform_ref.api_principal` needed `issuer`, since
the API validates an OIDC principal by the pair `(issuer, subject)` and
migration `025` only stored `external_subject`. Applied as
**`031_api_principal_issuer.sql`** — additive, `issuer` nullable and
deliberately unbackfilled (no identity-provider source of truth exists in
this workspace yet). The file's own commented-out follow-up (backfill →
`NOT NULL` → unique index on `(issuer, external_subject)`) is **not** done;
`API-CONTRACT.md` now says so explicitly so `inafin-api` doesn't assume that
uniqueness holds yet.

Verified: `make migrate` (zero drift), full suite (542 passed, same one
pre-existing failure), `ruff`, `mypy` — all clean.

---

## 4. Open, blocking: the onboarding tables were guessed, and the guess is wrong

The same hand-off file's comment block says, plainly:

> The following current `platform_ref` tables have materially different
> column shapes from the API's onboarding write contract. Do not apply
> ad-hoc ALTERs merely to make individual endpoints compile. Tenant-data
> must either adopt the API contract defined by `migrations/platform/0002`
> through `0004`, or publish a versioned replacement write contract and API
> adapter for each group: `onboarding_customer`, `customer_profile`,
> `onboarding_business_profile`, `onboarding_product_service`,
> `gst_registration`, `customer_document`, `data_requirement`,
> `onboarding_state`, `smart_question_run`, `smart_question`,
> `smart_question_response`.

This is a direct consequence of how migrations `026`-`028` were built last
session: Steve's message named which tables to rebuild
(`onboarding_customer` and friends) but not their actual columns — the real
withdrawn `inafin-api` files (`migrations/platform/0002_onboarding_smart_
questions.sql` through `0004_onboarding_state.sql`) were never read, only
described by table name. I designed reasonable-looking columns from first
principles. `inafin-api` is now saying, correctly, that reasonable-looking
isn't the same as correct — their real API code was written against a
specific shape, and this repo's guess doesn't match it.

**Do not "fix" this with another single-session guess or an ad hoc `ALTER`.**
The confirmed six admin tables (`{{gold}}`, no RLS) are NOT affected — the
spec file explicitly confirms those already satisfy the required location
and access pattern.

**Next session starts here, item `00`**: get the actual content of
`inafin-api`'s `migrations/platform/0002_onboarding_smart_questions.sql`
through `0004_onboarding_state.sql` (the withdrawn files themselves — same
place `0001_identity_access.sql` came from, which is how `025` got built
correctly) and reconcile deliberately: either adopt their column shapes
directly in a new migration, or design a genuinely versioned replacement
contract with `inafin-api`'s explicit sign-off on adapting to it. This
blocks `inafin-api` actually integrating the onboarding flow — treat it as
the first thing to resolve.

---

## 5. Doc4 Source Document Register — coverage analysis (discussion only, no code)

Steve asked what's built vs. pending against
`reference/INAFIN_Recon_Doc4_Sec2_SourceDocRegister_v2.docx (1).pdf` §2 (the
166-ref Source Document Register), since the Portal+API build now lets an
Analyst upload any of Doc4's data categories into Bronze. Full numbers are
in the chat transcript; the durable facts, in case a future session needs
them again without re-deriving:

- Doc4 has 166 refs across four categories (A/B/C/D). This repo's registry
  (`registry/document_types.csv`) covers 130 of them — all of A/B/D.
  **Category C (37 refs — Acts, Rules, Circulars, case law, audit manuals)
  is entirely out of scope for this repo** — platform-resident legal corpus,
  belongs to `inafin-gst-corpus`, not Bronze/Silver here.
- Of 128 in-scope rows (130 minus 2 `CORPUS`-stream reference-data rows),
  **85 have a working `dispatch_mechanism` (Bronze→Silver is built)**: all
  of Category A except `A1.20` (Marketplace Settlement Report), plus 4
  Category B types (`IRN_IRP_REGISTER`, `EWAY_BILL_OUTWARD_REGISTER`,
  `ICEGATE_SHIPPING_BILL`, `ICEGATE_BILL_OF_ENTRY`) and 8 Category D types
  (the 7 marketplace/e-commerce reports + `SEZ_BILL_OF_ENTRY`) that this
  repo chose to also accept as an uploaded file even though Doc4 frames
  them as portal/API-retrieved.
- **43 are pending** — registry row exists (`doc_type_code`, `archetype`
  assigned), but no `dispatch_mechanism`: all of `B1`(GSTN returns),
  `B2`(registration/status), `B3`(notices/proceedings), `B6`(DGFT), plus
  `B5.02`, `D2`(FIRC/BRC), `D3.02`, `D4`(MCA/NCLT), `D5`(targeted
  entitlements). Every pending item is Doc4's own Stream A (portal/API) or
  Stream C (third-party) — not a document the client physically holds.
  Uploading one today lands safely in Bronze (no registry check at intake)
  but `/trigger` returns `status: "UNROUTED"`, not an error — the Portal
  needs to render that as "received, not yet processed," not a failure.

Also given this session, not yet acted on: a walkthrough of the exact
`POST /artefacts`, `POST /artefacts/{id}/trigger`, `GET /artefacts/{id}/status`
contract for `inafin-api` to integrate against (field names, required
`period_start`/`period_end`/`gstin` conditionality, allowed file extensions
— **no Excel/XLSX support yet**, `status` vocabulary handling). And one open
question raised but not resolved: **Doc4's `ref` codes (`A1.01`, …) are not
stored anywhere in the database** — only `doc_type_code` is
(`platform_ref.document_type`'s primary key). If the Portal wants to show
Doc4-flavored labels/refs, either add `ref` as a real column on
`platform_ref.document_type` (small follow-up migration, not done this
session — nobody asked for it yet) or `inafin-api` maintains its own static
copy of the mapping, which will drift. Needs a decision, not urgent.

---

## Verification run this session

```
make migrate     # 024-031 shared, 021-025 tenant, all tenants — zero drift
make lint         # ruff — all checks passed
make typecheck    # mypy — no issues, 62 source files
pytest tests/     # 542 passed, 2 skipped, 1 pre-existing unrelated failure
```

Plus a direct, non-destructive proof (not just code inspection) that
`app_login`'s ambient grant surface is exactly what migration `030` intends:

```sql
SELECT table_schema, table_name, string_agg(privilege_type, ',' ORDER BY privilege_type)
FROM information_schema.role_table_grants
WHERE grantee = 'app_login'
GROUP BY table_schema, table_name ORDER BY 1,2;
```

No `docker compose down -v` was run this session (see §2).
