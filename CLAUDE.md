# inafin-tenant-data — working notes

Pipeline 1 of the INAFIN tenant data layer: Bronze → Silver, schema-per-tenant
isolation, and the batch manifest that hands work to `inafinplatform/v2`.

**Read these before proposing anything.** They are the design of record and they
already contain the reasoning for decisions that look surprising:

| File | What it settles |
|---|---|
| **`TYPED-TABLES-PLAN.md`** | **Read first.** Agreed 2026-08-06. Overrules ARCHITECTURE.md §6 for `STRUCTURED` types: one typed table per document type, not archetype tables with a `jsonb` bag |
| `ARCHITECTURE.md` | The whole design. §6 archetypes — **superseded, see above**. §9 findings, §11 open decisions |
| `TODO.md` | Deploy blockers, open gaps, decisions made and *rejected* |
| `README.md` | Isolation model, invariants that must not be undone |
| `registry/README.md` | Document Type Registry — 130 refs → 125 in-scope types |
| `API-CONTRACT.md` | What `inafin-api` may read/write, and how — the boundary that replaced its withdrawn migration proposals (2026-08-14, seventh session) |

## Non-negotiable invariants

Each of these was expensive to establish. Do not "fix" one without reading why.

1. **Kafka is a doorbell; `ingest_batch` is the queue of record.** Correctness
   must never depend on message delivery.
2. **Isolation is `GRANT`**, not a row predicate. No RLS, no `tenant_id` columns.
   `app_login` is NOINHERIT and holds nothing until `SET LOCAL ROLE`. **One
   named exception** (shared migration `030`, 2026-08-14): a fixed, narrow
   list of `platform_ref` control-plane tables (identity/access, customer
   onboarding — never `bronze`/`silver`/`gold`, tenant or otherwise) is
   granted to `app_login` ambiently, because authorizing a request has to
   resolve *which* tenant it belongs to before any role can be assumed. See
   `API-CONTRACT.md`. Do not extend this list anywhere but migration `030`
   itself, and never for tenant-schema data.
3. **`prepare_threshold=None`** — psycopg3 prepared statements do not survive
   PgBouncer transaction pooling. Fails only under load.
4. **`search_path = ''`** cluster-wide; all generated SQL fully qualified.
5. **`v1_` views are definer-rights.** Never add `security_invoker = true`.
6. **Bronze is INSERT-only.** Bronze records fact, Silver records judgement.
   Do not add a column to `artefact_ledger` that records a later conclusion.
   (Silver is *not* insert-only under the redesign — closing a superseded row is
   an UPDATE. That is judgement, so it does not weaken this invariant.)
7. **Dedup is per-tenant.** A global fingerprint registry is a cross-tenant leak
   (ARCHITECTURE.md §9 finding 1) — it contradicts the requirements doc on
   purpose.
8. **No shared-catalogue writes inside the per-tenant path** — concurrent
   fan-out contends on the ACL row.

## Decisions already made — do not re-propose

- **Engagement does not belong in Bronze or Silver.** It is an app-layer scope
  declaration. Rejected once already; reasoning in `TODO.md`.
- **Forensic Mode is deferred.** Only 8 in-scope types are Forensic-only, all
  CONDITIONAL. Build Operational.
- **Tenant migrations are templated, not Alembic** (`src/migrate/runner.py`).
- **The registry carries artefact types, not row types.** A row's character is
  `(doc_type, direction)`. The 5 Phase 1 values (`PURCHASE_INVOICE`, …) are
  retired but undeletable — Bronze is insert-only and references them.
  Decided 2026-08-04, shared migration 008.
- **ERP header aliasing is not registry data.** Tally and SAP name the same
  field differently, and that varies per *client*, not per document type. The
  registry declares the canonical contract; header mapping is the connector's.

## Workflow

```bash
make ci                      # ruff + mypy + 422 tests + isolation + static gates
make migrate                 # shared chain, all tenants, drift check
make provision SLUG=acme
uvicorn src.api.app:app      # the ingestion surface (REST + GraphQL)
```

- Migrations are **checksum-pinned**. Never edit an applied migration — add a new
  one. A new registry column becomes a new migration; `004` regenerates
  byte-identically.
- `registry/document_types.csv` is the source of truth; run
  `scripts/gen_registry_seed.py` after editing, commit both.
- **Never run `docker compose down -v` or otherwise wipe/recreate the whole
  database.** This Postgres is now a shared dev server across `inafin-api`,
  `inafin-portal`, and this repo (2026-08-14, eighth session) — a full reset
  destroys other repos' data, not just this repo's sandbox. Verify changes
  with targeted DDL (create/drop/alter individual tables, indexes, schemas)
  and by querying `information_schema`/`pg_catalog` against the existing
  cluster instead. If a genuinely clean-state proof is ever needed, that
  requires a separate, explicitly-scoped throwaway database — never this
  server.
- Prefer mutation-checking a new gate: break the thing, confirm exactly one test
  fails, restore. A suite nobody has seen fail is indistinguishable from
  `assert True`.

## Current state (2026-08-14, seventh session)

**The `inafin-api` migration collision from the sixth session is resolved —
rebuilt, not restored.** The sixth session moved `inafin-api`'s files
(`017`-`022` shared, `021`-`024` tenant) to
`/tmp/inafin_api_migrations_holding/` after finding they broke a clean
cluster and referenced tables that exist nowhere. This session's
conversation with `inafin-api`'s owner (see the seventh-session chat log if
you need the full back-and-forth) settled every open question:

- Those files were always meant as **proposals for transfer into this repo**
  (`inafin-api`'s own `migrations/platform/0001`-`0004`), not an independent
  chain — `hsn_master`/`sac_master` were referenced but created nowhere and
  needed to be owned here.
- `022_api_admin_tenancy.sql`'s RLS/`tenant_id` design for the six admin
  tables is **withdrawn**, not reconciled. They now live in `{{gold}}` like
  every other tenant-owned table — isolated by schema/GRANT, not a row
  predicate.
- `platform_ref.tenant` and `app.tenant_registry` are the same concept;
  `app.tenant_registry` wins.
- **Ownership model, settled**: this repo owns all schema, migrations,
  grants, provisioning, and platform reference data. `inafin-api` owns API
  code only and consumes a published contract (`API-CONTRACT.md`, new this
  session) — it must never place executable migrations in this repo's
  directories again.
- The old `017`-`022`/`021`-`024` sequence is **formally withdrawn**.

**What replaced it**: shared migrations `024`-`030`, tenant migrations
`021`-`025`. `024_crypto_schema.sql` (pgcrypto, general-purpose now, not
connector-specific), `025_platform_identity_access.sql` (`api_principal`,
`role`, `permission`, `role_permission`, `principal_tenant_role` — the last
FK'd to `app.tenant_registry(tenant_id)`, not a recreated
`platform_ref.tenant`), `026_platform_onboarding_customer.sql`
(`onboarding_customer` and friends, plus `platform_ref.tenant_customer`, the
customer→tenant resolution table), `027_platform_onboarding_profile_and_
artifacts.sql` (`customer_profile`, `gst_registration`, `customer_document`,
`data_requirement` — `customer_document`/`data_requirement` key off the
existing `platform_ref.document_type` registry, so the onboarding document
picklist and the ingestion registry are one list, not two),
`028_platform_onboarding_state.sql` (`onboarding_state`),
`029_platform_catalog_hsn_sac.sql` (`hsn_master`/`sac_master`, DDL only,
**seeded empty** — no real code list exists in this workspace, stated
plainly rather than invented; see `TODO.md`), `030_platform_control_plane_
grants.sql` (the invariant-#2 exception, see above — `app.v1_tenant_
directory` view plus the exact `app_login` grant list, all in one file so
it stays auditable).

Tenant `021`-`024` (`connector_configuration`, `workspace_runtime`,
`datahub_upload_request` + idempotency) are unchanged from `inafin-api`'s
draft — already correctly `{{gold}}`-scoped. Tenant `025_admin_operational_
tables.sql` is the rebuilt six admin tables, RLS/`tenant_id` entirely
omitted; `deboarding_case.customer_id` FKs cross-schema to `platform_ref
.onboarding_customer`, the same pattern `entitlement_instrument` already
uses against `platform_ref.document_type`.

**Verified, not just written.** `make migrate` applied all 7 shared + 5
tenant migrations cleanly against the running (not reset) cluster, zero
drift; `make lint`/`make typecheck` clean; full suite 541 passed / 2
skipped / the same one pre-existing `test_isolation.py` ordering failure
from the sixth session (unrelated, not touched). Confirmed by direct SQL
query against `information_schema.role_table_grants` — not by inspection —
that `app_login` holds **zero** privileges on any `t_*_bronze/silver/gold`
object, and its full ambient grant list is *exactly* migration `030`'s
named tables, nothing more. New isolation test
`test_cross_tenant_admin_table_read_is_denied` proves `app.apply_tenant_
grants`'s generic gold-table sweep actually reached the new `admin_role`
table (no hand-written grant needed for it), not just that the mechanism
should work by construction. **A full `docker compose down -v` clean-cluster
verification was offered and explicitly declined this session** — the
above is real verification against the existing cluster, but nobody has
yet proven this sequence applies from empty. Do that before the next
session claims this done from a truly clean state.

**`inafin-api`'s own code changes are outstanding, in that repo, not this
one**: drop the RLS admin path and route it through `SET LOCAL ROLE
t_<slug>_recon`; migrate authorization queries off `platform_ref.tenant`
onto `app.v1_tenant_directory`; move admin SQL onto the new `{{gold}}`
tables. `API-CONTRACT.md` is written so that work has an exact list to
implement against.

**Later in the same session, three more things**, full detail in
`HANDOFF-2026-08-14-session7.md`:

1. **Standing rule, permanent**: this repo's Postgres is now a shared local
   dev server across `inafin-api`, `inafin-portal`, and this repo — never
   run `docker compose down -v` or any other full reset again. The
   Workflow section above is updated; verification from here on is
   targeted DDL plus `information_schema`/`pg_catalog` queries against the
   live cluster, never a wipe.
2. **`inafin-api` sent a real hand-off spec**
   (`inafin-api/docs/tenant-data-api-compatibility.sql` — a documented
   pattern going forward: a spec file in *their* repo, applied through
   *our* migration runner, never an executable migration of theirs). One
   real change in it: `platform_ref.api_principal` needed `issuer` for
   their OIDC (issuer, subject) principal lookup. Applied as shared
   migration `031_api_principal_issuer.sql` — additive, `issuer` nullable
   and unbackfilled on purpose, since no identity-provider source of truth
   exists in this workspace to backfill from yet. Verified: `make migrate`
   zero drift, full suite 542 passed / same one pre-existing failure,
   lint/typecheck clean.
3. **That same spec file surfaced a real problem this session's guesswork
   caused**: migrations `026`-`028` (`onboarding_customer` and friends)
   were designed from table names only — the user's message named which
   tables to rebuild but not their columns, since the actual withdrawn
   `inafin-api` files (`migrations/platform/0002`-`0004`) were never read.
   `inafin-api`'s spec says plainly: our shapes are "materially different"
   from their onboarding write contract, and NOT to patch this with ad hoc
   ALTERs. **Not resolved this session** — see "Next session starts here."

## Current state (2026-08-14, sixth session)

**Items #1-3 from the fifth session's "Next session starts here" are done:
the general Bronze→Silver dispatcher, `POST /artefacts/{id}/trigger` wired to
it synchronously, and a real OCR fallback.** `make lint`/`make typecheck`/
`make gates` all green; full suite 541 passed / 2 skipped (OCR, opt-in) / 1
pre-existing unrelated failure (see "Discovered this session" below) from a
clean `docker compose down -v` cluster running ONLY this repo's own migration
chain (see the `inafin-api` finding below for why "only this repo's own
chain" is a real caveat right now, not a formality).

**Dispatch routing is registry data, the same idiom as `field_contract`/
`extraction_spec`.** New column `platform_ref.document_type.dispatch_mechanism`
(shared migration `023_dispatch_mechanism.sql` — NOT `017`, see the numbering
collision below), one of `PDF_EXTRACTION | SALES_REGISTER | REGISTER_LOADER |
ARCHETYPE1_PROMOTE` or empty (not upload-triggered / no extractor built yet).
Backfilled in `registry/document_types.csv` by actually querying the code
each mechanism depends on (`SPEC_BY_DOC_TYPE`, `REGISTER_DOCUMENT_EXTRACTORS`,
`extraction_spec` presence, `_RETIRED_ARCHETYPE_1_TYPES`) rather than guessed
— counts self-check exactly: 50 `PDF_EXTRACTION` (== the 50 specimen PDFs),
29 `REGISTER_LOADER`, 5 `ARCHETYPE1_PROMOTE`, 1 `SALES_REGISTER`, 40 empty.
`scripts/gen_registry_seed.py`'s new `_check_dispatch_mechanism` cross-checks
every populated cell against that same code at generation time — a value that
doesn't actually resolve cannot be committed. **Adding a document type under
an EXISTING mechanism is now a CSV row, not a code change**, closing the loop
`extraction_spec` started. Adding a genuinely new mechanism still needs one
new branch in `src/dispatch/router.py`, once — unavoidable, but a one-time
cost per mechanism, not per document type.

**A real, latent bug found and fixed along the way**: `CREDIT_DEBIT_NOTE_REGISTER`
and `HO_COMMON_INPUT_SERVICE_INVOICE` are archetype 1 in the CSV AND already
have working `RegisterSpec` entries (`src/silver/registers/catalog.py`,
built in the second/third sessions) — meaning BOTH `promote_transaction_documents`
and `RegisterLoader` would have accepted either type before this session,
with nothing stopping a caller from double-writing through the wrong one.
`src/silver/promote.py`'s `_RETIRED_ARCHETYPE_1_TYPES` (previously just
`{"PURCHASE_REGISTER"}`) now also lists these two, so the archetype-1 path
refuses them defensively — `dispatch_mechanism=REGISTER_LOADER` for both is
the single source of truth now. No test exercised this collision before,
so nothing broke; it was just wrong.

**New module `src/dispatch/`**: `router.py`'s `dispatch_load(ctx, pool, *,
ingest_id, entity_id, doc_type_code, data, content_format, period_start=None,
period_end=None, gstin=None, store=None, reader=None, ...)` resolves
`dispatch_mechanism` and calls the right one of `run_extraction` /
`SalesRegisterLoader.load` / `RegisterLoader.load` / `promote_transaction_documents`,
returning a normalised `DispatchOutcome(mechanism, batch_id, written, status,
detail)` — `status` reuses `SilverReader.artefact_outcome`'s vocabulary
(`ACCEPTED`/`PARTIAL`/`QUARANTINED`). `content_format.py`'s
`infer_content_format` reuses `filecheck.py`'s (now-public) `extension_of`.
`BronzeIngestionService` gained `ledger_entry()` (metadata read-back —
`entity_id`, `original_filename` — alongside the existing `fetch()` for
bytes) since the trigger route needs both and `TriggerRequest` only ever
carried `doc_type_code`.

**`POST /artefacts/{id}/trigger` now actually dispatches**, synchronously,
in-process, after recording `load_trigger` as before. `TriggerRequest` gained
optional `period_start`/`period_end`/`gstin` (required only by mechanisms
that need them — PDF needs none); `TriggerResponse` gained a real `status`,
`mechanism`, `batch_id` instead of the constant `"recorded"`. This is a
**deliberate revision** of `src/api/app.py`'s prior docstring ("Bronze->Silver
promotion is deliberately NOT here and never will be") — re-read as HANDOFF-
2026-08-07.md's actual argument (never a SECOND remote HTTP hop for the
loader), calling `dispatch_load` in-process from the same request handler
does not violate it; the docstring was updated to say so explicitly. Tests:
`tests/conformance/test_api_ingest.py` now exercises all four mechanisms plus
`UNROUTED` (no mechanism) and a 422 for a missing required field, each
through a real `TestClient` HTTP call — not just the dispatcher directly.

**OCR fallback, built AND verified against real weights, not just the seam.**
`src/extraction/reader.py`: `PdfText` gained `source: Literal["native","ocr"]`;
new `FallbackPdfTextReader(primary, secondary=None)` implements `PdfTextPort`
itself — tries `primary`, falls through to `secondary` only if `primary` found
no native text, returns `has_native_text=False` only if BOTH fail. This
resolves `HANDOFF-2026-08-11.md`'s open "what does `NoTextLayer` mean once
OCR exists" question: it means "no native layer AND OCR unavailable-or-also-
failed", and nothing in `dispatch.py` had to change since it already accepted
a `reader:` parameter. `src/extraction/ocr.py`'s `PaddleOcrReader` is the real
secondary adapter — renders pages via `pypdfium2` (pure wheel, no system
Poppler), OCRs via `PaddleOCR`. Lazy-imports `paddleocr`/`pypdfium2` inside
`__init__`/`extract()`, so importing the module costs nothing if the optional
group isn't installed. New `pyproject.toml` `[project.optional-dependencies]
ocr` group (`paddleocr`, `paddlepaddle`, `pypdfium2`) — NOT core, so
`make ci`'s default install stays offline-safe; `Settings.ocr_enabled` (default
`False`) gates whether `app.py`'s lifespan constructs the real adapter at all.

**The verification the design notes said was missing, actually run**:
`tests/fixtures/ocr/lut_scanned_synthetic.pdf` — page 1 of the real LUT
specimen, rasterized via `pypdfium2` and re-saved as an image-only PDF (no
text layer, confirmed). `INAFIN_RUN_OCR_TESTS=1` (opt-in, since it downloads
real model weights — skipped by default, same reasoning a live ClamAV
integration test would need) ran `tests/conformance/test_ocr_reader.py`
against real `paddleocr==3.7.0`/`paddlepaddle==3.3.1`: **it recovered the
known LUT ARN, `AD2704240123456`, from the pixels alone**, `source == "ocr"`,
`has_native_text is True`. This is the first time this workspace's OCR design
has been checked against anything at all. `test_pdf_text_fallback.py` (always
runs, no real OCR) covers the composition rule itself against fakes.

**Discovered this session, neither caused by nor fixed by it — flagged, not
silently worked around:**

- **A migration numbering collision with `inafin-api`.** `migrations/shared/`
  and `migrations/tenant/` are shared, apparently by convention, with a
  separate repo (`inafin-api`, the backend for `inafin-portal`) — files
  `017_connector_crypto.sql` through `022_api_admin_tenancy.sql` (shared) and
  `021_connector_configuration.sql` through `024_datahub_upload_idempotency.sql`
  (tenant) are `inafin-api`'s, committed, and were NOT reflected anywhere in
  this file's history before today. My first attempt at this session's own
  migration collided at number `017`; it is now `023_dispatch_mechanism.sql`.
- **`inafin-api`'s migration chain does not apply to a clean cluster.**
  `018_platform_administration.sql` references `platform_ref.onboarding_customer`,
  which nothing in this git repo creates — `docker compose down -v` + migrate
  fails on it, confirmed with `inafin-api`'s files both present and (to
  isolate) temporarily removed. The working theory: the ORIGINAL local dev
  Postgres volume (before this session reset it) had that table from
  `inafin-api`'s own separate deploy process having run against the same
  shared database previously — state this repo's own tooling cannot
  reconstruct alone. **This repo's test suite provisions tenants by running
  its own migration runner against whatever is in `migrations/shared/`,
  so `inafin-api`'s files being present breaks every test run, not just a
  full reset.** For the remainder of this session, `inafin-api`'s 10 files
  were moved to `/tmp/inafin_api_migrations_holding/` (NOT deleted, NOT
  committed — a working-tree-only relocation) so this repo's own suite could
  run and be verified. **They are not restored as of this handoff** — restore
  them from that path, or wherever they're safely held, once `onboarding_customer`
  is available again (`inafin-api`'s own process, presumably), and coordinate
  with whoever owns that repo about why the two chains share a directory and
  a numbering space at all.
- **A pre-existing, unrelated, order-dependent test bug.** `tests/conformance/
  test_isolation.py::test_support_is_read_only` asserts `tenant_a`'s
  `transaction_document` has exactly 1 row — true only if
  `tests/conformance/test_extraction_transaction.py` (written in the fourth
  session) has not already run and written 2 more rows under the same shared
  `tenant_a.entity_id`. Confirmed via `git log` that both files predate this
  session; not touched. Fails whenever the full suite runs (alphabetical
  collection order puts `test_extraction_transaction.py` first), so this is
  not new, just newly visible from a clean cluster.

## Current state (2026-08-11, fifth session)

**Corrective refactor, requested after review of the fourth session's work.**
The repo owner looked at the 527-test extraction batch (below) and was
legitimately angry: `entitlement_types.py`, `entity_master_types.py`,
`proceeding_event_types.py`, `financial_statement_types.py` and half of
`transaction_types.py` hardcoded each document type's extraction rule (which
PDF label maps to which field, its type, the issuing authority) as a
`ClassVar`-only Python subclass — 36 of them, plus a hardcoded 11-type tuple
in `narrative_contract_types.py`. That is exactly the defect ARCHITECTURE.md
§6 exists to prevent, and exactly the problem this repo already solved once
for Archetype 1/2 via `platform_ref.document_type.field_contract`
(`src/silver/contract.py`). This session applied the identical fix to the
label:value extraction rule. **`make ci` is green from a clean cluster — 531
tests** (up from 527; +4: 3 new generation-time validation tests, 1 new
archetype-dispatch test — no test was deleted, the same 47-specimen
assertions still run, now against registry-loaded specs).

**New mechanism, mirroring `field_contract` exactly:**
- `src/extraction/spec.py` (new) — `ExtractionSpec`/`parse_extraction_spec`,
  the grammar (`authority=GSTN;fields=instrument_number:text!:"LUT ARN",...`),
  raising `SpecError` on anything malformed. Field types reuse
  `labelvalue.py`'s `FIELD_TYPES` vocabulary, not reinvented.
- `registry/document_types.csv` gets a new `extraction_spec` column,
  populated for the 47 previously-hardcoded types (24 entitlement + 2
  proceeding-event + 7 entity-master + 1 financial-statement + 11
  narrative-contract, empty `fields=` on purpose + 2 transaction) — a
  faithful transcription of what the deleted classes already declared, not
  new judgment calls about field mappings.
- `scripts/gen_registry_seed.py` — new `_write_extraction_spec`/
  `_check_extraction_specs`, emitting shared migration
  `016_extraction_spec.sql` (next free number), validated through
  `parse_extraction_spec` at generation time — same guarantee
  `field_contract`/migration `009` already has. Regenerating is
  byte-identical on a second run; **004/005/009 were NOT touched** —
  re-running the generator reproduced pre-existing, already-uncommitted CSV
  drift in those files (`PURCHASE_REGISTER`'s note text, `ITC_04_
  ACKNOWLEDGEMENT`'s archetype) from the fourth session's work; that drift
  was reverted (`git checkout`) before this session's diff was finalized —
  those three migrations are applied and pinned and this refactor does not
  touch them.
- `src/extraction/base.py` — the 7 archetype base classes' `__init__` now
  take `doc_type_code`/`label_spec`/`authority` (uniform across all 6
  label:value-shaped classes, including `TransactionDocumentExtractor`) as
  constructor arguments instead of subclass `ClassVar`s. `extract()`/
  `to_silver()` bodies are otherwise unchanged. `EntitlementExtractor`'s
  separate `instrument_type` ClassVar is gone — every sampled subclass set it
  identical to `doc_type_code`, confirmed before deleting, so `to_silver` now
  uses `self.doc_type_code` directly. `RegisterDocumentExtractor` (archetype
  2) is explicitly UNCHANGED — still `ClassVar`-driven, `# type: ignore[misc]`
  noting the deliberate exception where it overrides the ABC's now-instance
  `doc_type_code` attribute.
- `src/extraction/registry.py` — `EXTRACTOR_BY_DOC_TYPE` (import-time dict of
  classes) replaced by `build_extractor_registry(pool, ctx, *, store=None)`
  (runtime, returns constructed instances): `SELECT doc_type_code, archetype,
  extraction_spec FROM platform_ref.document_type WHERE in_scope AND
  archetype IN (1,3,4,6,7,8)` — same query shape, same connection idiom
  `src/silver/promote.py`'s `_load_spec` already uses for `field_contract`.
  Archetype 1's two PDF types dispatch through a small fixed dict
  (`transaction_types.TRANSACTION_DOCUMENT_EXTRACTOR_CLASS_BY_DOC_TYPE`) since
  `_csv_row` is real per-type business logic, not label:value config.
  Archetype 2 merges in `register_types.REGISTER_DOCUMENT_EXTRACTORS`
  unchanged — genuinely bespoke per-type table parsing, explicitly out of
  this refactor's scope (a payroll table and an FX table share no columns,
  so there is no grammar to move to data).
- `src/extraction/dispatch.py` — `run_extraction` now builds (or accepts a
  pre-built) registry via `build_extractor_registry` instead of importing the
  static dict.
- The 5 hardcoded files (`entitlement_types.py`, `entity_master_types.py`,
  `proceeding_event_types.py`, `financial_statement_types.py`,
  `narrative_contract_types.py`) are shrunk to module docstrings only — the
  real narrative content (why `SEZ_ANNUAL_PERFORMANCE_REPORT` has no clean
  label:value date, which entity-master types land as `Partial`, ...) is kept,
  not deleted, since it explains real tier-1 limitations on real specimens.
- `tests/conformance/test_extraction_*.py` no longer import concrete
  extractor classes — a new `extractor_registry` fixture
  (`tests/conftest.py`, wraps `build_extractor_registry` against a real
  seeded tenant) is what every test pulls an extractor from, so the tests
  exercise the actual runtime data path. New
  `tests/conformance/test_gen_registry_seed.py` (3 tests): a malformed
  `extraction_spec` cell is rejected at generation time, an out-of-scope row
  carrying one is rejected, and the real CSV still generates cleanly —
  mutation-checked live during this session (corrupted a real cell in a
  temp-path copy, confirmed generation refused and wrote nothing, confirmed
  the real CSV alone still passes).

**Acceptance test, run and confirmed, not just asserted**: with the app
stack live (`make reset`), a throwaway `UPDATE platform_ref.document_type SET
extraction_spec = ... WHERE doc_type_code = 'GST_PRACTITIONER_AUTHORISATION'`
(an archetype-3 row that ships with an empty `extraction_spec` cell — nobody
has sampled that PDF) made `build_extractor_registry` serve a working
`EntitlementExtractor` for it on the very next call, no process restart, and
it correctly extracted a synthetic specimen's `instrument_number`/
`valid_from`. Restoring the cell to `''` made it disappear from the registry
again. Zero Python files were touched at any point — this is the concrete
form of "adding the 40th entitlement type is a registry-row change." (The
throwaway test used for this was written, run, and then deleted — it was
never meant to be part of the committed suite.)

**Deviations from the plan, both narrow:**
- `authority` is a constructor parameter on all 5 label:value archetype base
  classes (`EntitlementExtractor` through `NarrativeContractExtractor`), not
  only 3/6 — kept uniform so `build_extractor_registry` can call every base
  class through one `Protocol`-typed dict entry (`_ArchetypeExtractorCtor` in
  `registry.py`) rather than special-casing which two classes accept it.
  Classes that don't use it (`4/7/8`) just ignore the argument.
- `RegisterDocumentExtractor.doc_type_code` needed one `# type: ignore[misc]`
  per leaf subclass (mypy: overriding an instance attribute with a
  `ClassVar`, since the ABC's `doc_type_code` is now typed as an instance
  attribute for the other six archetype bases) — noted inline as the
  deliberate archetype-2 exception, not silenced without explanation.

---

## Current state (2026-08-11, fourth session)

**`make ci` is green from a clean cluster (`docker compose down -v`) — 527
tests** (up from 422). Built the plan in `TYPED-TABLES-PLAN.md`'s sibling doc
for this session, `streamed-marinating-gray.md` ("A2–A7 extraction adapters —
Bronze→Silver, with MinIO Silver copies") — **all 8 phases complete**, not a
partial session. New package `src/extraction/`, 5 new tenant migrations
(`016`–`020`), 2 new shared migrations (`014`–`015`), 4 new Silver tables, 3
new `RegisterSpec` entries, and extractors for all 50 sampled document types
in `reference/A1-A7Documents/`.

**Phase 1 — the framework** (`src/extraction/`): `reader.py` (`PdfTextPort` +
`PypdfReader`, `pypdf` now a real `pyproject.toml` dependency, not the prior
session's throwaway install), `labelvalue.py` (the shared `Label: Value`
grammar — `Extracted(fields) | Partial(fields, missing, failed) | NoTextLayer`,
reusing `src/silver/contract.py`'s `ATTR_TYPES` vocabulary plus one addition,
`date_range`, for `"X to Y"` validity lines), `base.py` (`DocumentExtractor`
ABC + one `to_silver()` hook per archetype), `registry.py`
(`EXTRACTOR_BY_DOC_TYPE`), `dispatch.py` (`run_extraction`, not wired to any
HTTP route — see priority 1 below). `"pdf"` added to
`src/bronze/filecheck.py`'s `ALLOWED_EXTENSIONS`
(`test_disallowed_extension_is_rejected` now uses `.exe` as its negative
example instead).

**Phase 2 — four new tenant tables**, migrations `016`–`019`, each following
`006_entitlement_instrument.sql`'s idiom (composite FK pinning type to
archetype, `_vt`-pinned vocabulary, bitemporal supersession, `v1_` view):
`proceeding_event` (archetype 6), `entity_master_record` (archetype 7),
`financial_statement_extract` (archetype 4, every business column nullable
except `auditor`), `narrative_contract` (archetype 8, **every business
column nullable** — the specimens are prose, not label:value; a row with
empty `key_terms` is the correct successful outcome, not a failure). Shared
`014` seeds their vocabulary AND corrects `ITC_04_ACKNOWLEDGEMENT`'s registry
archetype 5→6 (a document-issued proceeding, not a Stream-A polled return —
`registry/document_types.csv`'s cell is updated to match but
`scripts/gen_registry_seed.py` must NOT be re-run over it; migration `004` is
applied and pinned). One service module per table
(`src/silver/proceeding_event.py`, `entity_master.py`, `financial_statement.py`,
`narrative_contract.py`), mirroring `entitlement.py`'s record/supersede shape.

**Phase 3 — the 24 Archetype 3 extractors** (`src/extraction/
entitlement_types.py`) — the registry's `archetype` column is authoritative
for the count, not the plan's rough prose estimate ("SEZ×6" was actually 5).
22 of 24 specimens extract cleanly; 2 land as a named, asserted `Partial`
(`SEZ_ANNUAL_PERFORMANCE_REPORT`, `CUSTOMS_DUTY_EXEMPTION_CERTIFICATE` — no
clean `Label: Value` date in either specimen). `EntitlementService.record`
called unchanged.

**Phase 4 — archetype 6/7/4/8** (`proceeding_event_types.py`,
`entity_master_types.py`, `financial_statement_types.py`,
`narrative_contract_types.py`): RBI_COMPOUNDING_ORDER + ITC_04_ACKNOWLEDGEMENT
(1 Extracted, 1 named Partial — the ITC-04 specimen is a per-quarter table,
not a header); 7 entity-master types (3 Extracted, 4 named Partial — several
specimens have a `reference_number` but no clean `as_of_date` label); the one
financial-statement specimen (Extracted, sparse — only `auditor` binds); all
11 narrative-contract types (all `Extracted(fields={})` by design — see
migration `019`'s header). Mutation-checked all four new tables' primary DDL
gate (the composite archetype FK for three of them, the CONTENT-key unique
index for `narrative_contract`) — each broke exactly the one test naming it,
restored clean.

**Phase 5 — archetype 1** (`transaction_types.py`): `GtaConsignmentNoteExtractor`
(A4.08) extracts cleanly and promotes a real `transaction_document` row
through `SilverPromotionService.promote_transaction_documents` UNCHANGED.
`BillOfEntryExtractor` (A6.01) legitimately lands as a named
`Partial(missing=("taxable_value", "igst_amount"))` — those two figures sit in
a colon-less "Particular / Amount" table pypdf flattens without a `Label:`
delimiter, the same accepted tier-1 limitation named throughout this batch.

**Phase 6 — archetype 2** (`register_types.py` + tenant migration `020` +
shared migration `015` + 3 new `RegisterSpec` entries in
`src/silver/registers/catalog.py`): `PAYROLL_TDS_REGISTER` (5 rows parsed out
of a genuinely reflowed pypdf table — row boundaries found via each row's own
stable token, e.g. `EMP0231`/`CONS0012`), `FIRC_BRC_REGISTER` (4 rows,
including the one with no FIRC number yet — `firc_no` is nullable by design,
not a parse failure), `FORM_15CA_15CB` (a **degenerate one-row register** —
its specimen is `Label: Value` shaped like archetypes 3/6/7, not a table, so
it reuses `parse_label_value` rather than a bespoke regex parser). One real
correction made mid-session: `payroll_tds_register.classification` was
originally `CHECK (... IN ('Employee','Contractor'))`, but the real specimen's
own column carries a third value, `'Professional'` — the CHECK was dropped
(free text, like `note_type` is a real fixed enum but this genuinely is not)
before the migration was ever left applied-and-pinned across a session
boundary; `docker compose down -v` + re-migrate picked up the correction
cleanly. `entity_gstin` for all three new register types is a fixed class
constant (`27AABCM4521F1Z5`) — a real deployment would resolve it from the
entity's registration record, which has no lookup in this codebase yet;
named as a simplification, not hidden. `test_register_specs.py`'s existing
DDL gate covers the 3 new specs automatically (it iterates `SPECS`), so no
new spec-vs-DDL test had to be hand-written.

**Phase 7 — MinIO Silver copy**: `ObjectStorePort.put_silver` +
`S3ObjectStore.put_silver` (`src/provisioning/objectstore.py`), key
`silver/{doc_type}/{date}/{ingest_id}.pdf`, same Object Lock/COMPLIANCE
retention as `put_bronze`. Wired as an optional `store: ObjectStorePort |
None = None` constructor parameter on every one of the 7 archetype base
classes in `src/extraction/base.py`, called via one shared `_put_silver_copy`
helper right after each successful (non-quarantined) write — best-effort,
same "log and swallow" pattern `promote.py`'s post-commit Kafka publish
already uses, not raised on a storage hiccup. `test_extraction_silver_copy.py`
verifies a real `silver/...` key lands in the dev MinIO cluster and is
byte-identical to the source PDF, alongside a `bronze/...` key for the same
tenant bucket.

**Phase 8 — verification**: `docker compose down -v` → re-migrate (shared
`001`–`015`, both tenants `001`–`020`) → `make ci` green, twice (once mid-session
after phase 6's DDL correction, once final after phase 7). 527 tests, 0
lint/mypy findings, isolation and static gates clean.

**Design deviations from `streamed-marinating-gray.md`, all noted in-line
where they matter:**
- The plan's "25 types" for archetype 3 is actually 24 per the registry's own
  `archetype` column — used the registry, not the plan's prose count.
- `ITC_04_ACKNOWLEDGEMENT` needed an actual registry correction (archetype
  5→6, shared migration `014`) to satisfy `proceeding_event`'s composite FK —
  the plan named this type as belonging there "pragmatically" but didn't
  specify how the archetype-5-in-the-CSV vs. archetype-6-in-the-table
  contradiction would be resolved.
- `entity_master_record`/`narrative_contract` business columns are more
  NULLABLE than migration `006`'s idiom strictly implies, because several
  real specimens genuinely have no clean second fact to bind — stated
  explicitly in each migration's header rather than forcing every column to
  mirror `entitlement_instrument`'s all-required shape.
- `narrative_contract` and `financial_statement_extract` use a CONTENT key
  (`bronze_ingest_id`) instead of a NATURAL key — no specimen in either
  archetype carries a document-level reference number the way an instrument's
  ARN or a proceeding's application number does.

**Not built, and explicitly out of scope per the plan**: OCR, a live LLM
escalation tier, an `extraction_audit` table, the general Bronze→Silver
dispatcher (`POST /artefacts/{id}/trigger` is still a stub — `run_extraction`
in `src/extraction/dispatch.py` is called directly by tests only, same as
`RegisterLoader`/`EntitlementService` always have been). See "Next session
starts here" below.

---

## Current state (2026-08-11, third session)

**Read `HANDOFF-2026-08-11.md` for this session's detail.** No code changed
this session — it was analysis and scoping only, on two fronts:

1. **A gap analysis of a proposed Portal→Bronze→Silver injection flow**
   against the current implementation, requested explicitly as "no
   implementation, just a plan." The one real conflict found: the proposed
   status state machine (`RECEIVED → ... → COMPLETED`, a single mutable
   status field) is exactly the shape tenant migration `005_bronze_insert_only.sql`
   deliberately removed from `artefact_ledger`, for invariant 6 (Bronze
   records fact, Silver records judgement). Today "status" is computed on
   read (`SilverReader.artefact_outcome`), not stored. **Do not implement the
   proposed flow literally without resolving this with Steve first** — it
   would undo a deliberate decision, not add a feature. Other gaps found
   (no dispatcher — already tracked; two IDs not one; no multi-file upload;
   no registry check at intake) are ordinary gaps, not invariant conflicts.
2. **The Archetype 3 extraction blocker is lifted.** Steve placed 50 real
   sample documents in `reference/A1-A7Documents/` (A2.01–A7.05). Inspected
   with `pypdf` (not yet a real dependency — throwaway check): every file has
   a native text layer, none need OCR to read. A PDF-text-reader→PaddleOCR-
   fallback design was discussed and mapped onto the existing 2026-08-06
   tiered-extraction design (`NoTextLayer` becomes "OCR also failed," not a
   new concept) — see `HANDOFF-2026-08-11.md` for the open questions (OCR
   provenance weighting, what fixture validates the OCR path since none of
   the 50 samples exercise it). **Nothing built yet** — this is scoping only,
   Archetype 3 build has not started.

---

## Current state (2026-08-10, second session)

**Read `HANDOFF-2026-08-10.md` for the first session's detail; this section is
the durable summary, now covering both sessions run today.** `make ci` is
green from a clean cluster — **422 tests** (up from 410).

**The ingestion surface is now built** — `src/api/` — REST upload/trigger/status
+ GraphQL reads, the item that was "Next session starts here" item 2. Scoped
deliberately narrow, three decisions made explicitly before building:

- **Upload and status are real**, wrapping `BronzeIngestionService.receive`
  and `SilverReader.artefact_outcome` unchanged. **Trigger is a stub** —
  `POST /artefacts/{id}/trigger` records intent in a new INSERT-only table
  (tenant migration `015_load_trigger.sql`) and calls no loader. There is
  still no `doc_type_code -> loader` dispatcher (register spec vs
  `promote.py`'s archetype-1 path vs `SalesRegisterLoader` vs a future
  entitlement path — nothing in the registry names which one handles a
  type). New `TODO.md` entry, "Ingestion surface — what's built and what's
  still missing".
- **Auth is a pluggable placeholder**, not real security. `src/api/auth.py`'s
  `AuthPort` Protocol (same idiom as `VirusScanPort`) has one adapter,
  `StaticTokenAuth` — a static bearer-token → tenant-slug map from
  `Settings.api_tenant_tokens`. ARCHITECTURE.md 5.6 wants a signed Keycloak
  JWT claim; that is unbuilt. The tenant slug is still never a
  caller-supplied parameter — it only ever comes from the resolved token —
  which a mutation check confirmed: breaking `StaticTokenAuth.resolve` to
  ignore which token was presented failed both the unknown-token-401 test
  and the cross-tenant isolation test, restored clean.
- **GraphQL was built now, not deferred** — `strawberry-graphql`, mounted at
  `/graphql`, resolvers wrapping `SilverReader`/`EntitlementReader` with no
  new read logic invented. `Role.RECON` already IS "reader over `v1_` views
  only, nothing else in Silver" (confirmed against
  `migrations/shared/001_app.sql` step 5) — no new Postgres role was needed.

Verified with a real running server, not just `TestClient`: `uvicorn
src.api.app:app`, curl upload → status → trigger → 401-without-auth →
GraphQL query, all against a live cluster.

**#3 on the old "Next session" list (JSON adapter) turned out to already be
done** — NDJSON, added last session, IS the JSON support the design calls
for ("JSON resolved to NDJSON"); confirmed with the user, no new code needed.

**#6 (`NULLS NOT DISTINCT`) is also closed this session** — tenant migration
`014_nulls_not_distinct.sql` adds it to the three natural-key indexes that
carry a nullable `supplier_gstin` column (`purchase_register`,
`creditor_ageing_report`, `common_input_service_invoice`). Mutation-checked:
dropping the clause fails exactly
`test_the_index_rejects_two_live_nulls_the_lookup_would_have_matched`,
restoring fixes it. Along the way, the gate test itself had the same
"commits its probe row on failure" bug the earlier session's `_probe_rejects`
docstring already warned about — caught and fixed before it shipped, same
unconditional-`Rollback` pattern.

---

### First session's summary (`HANDOFF-2026-08-10.md`)

`make ci` was green from a clean cluster — **410 tests**.
The repo was put under git this session (`main`, one `initial commit`, clean tree).

**TYPED-TABLES-PLAN.md §10 is now fully built, steps 1–5 all done.** Step 4
(the last one) landed this session: the archetype-1 write path for
`PURCHASE_REGISTER` is retired — `v1_purchase_invoice` reads from
`purchase_register`, `promote.py` refuses to promote that type through
`transaction_document` (`_RETIRED_ARCHETYPE_1_TYPES`). **`v1_purchase_invoice_line`
was dropped** (no line-level data exists for that type in the reference
schema) — this is a breaking v2 contract change awaiting sign-off, not yet
raised with anyone downstream. `transaction_document` still holds the other
10 archetype-1 types unchanged.

Also built this session, driven by an explicit design review and two hard
requirements (row-level not file-level rejection; JSON/CSV support, JSON
resolved to NDJSON):

- **Row-level rejection** for the register loader path — `rejected_row`
  (tenant migration `012`), one row per rejection, FK'd to `ingest_batch`.
  FATAL failures (bad encoding, missing column, zero rows) still quarantine
  the whole artefact via `quarantined_artefact`, unchanged; everything else
  promotes what's valid and records the rest.
- **NDJSON parsing** alongside CSV, sharing one validation rule (`_parse_row`)
  so a fix in one format is a fix in both.
- **Bronze intake gate** (`src/bronze/scan.py`, `src/bronze/filecheck.py`) —
  file-shape checks plus a swappable virus-scan `Protocol`
  (`VirusScanPort`), because Steve may adopt a commercial or cloud-native
  scanner later and wants scanning toggleable per pipeline. `NullScanner`
  (off, default) and `ClamAVScanner` (real, adapter-tested against a fake
  clamd) are the two adapters today; no live ClamAV container was added to
  `docker-compose.yml`.
- **`SilverReader.artefact_outcome`** (tenant migration `013`) — the
  customer-facing "did my upload succeed" query: `PENDING` /
  `QUARANTINED` / `ACCEPTED` / `PARTIAL`, with per-row rejection detail on
  `PARTIAL`. This closes the original motivating gap (a report saying an
  upload succeeded or failed and why).

Built earlier, still true: isolation foundation, Bronze→Silver, v2 handoff
contract, Document Type Registry (125 in-scope types), Bronze insert-only,
**Archetype 3 `entitlement_instrument`** (33 types, one table), **Archetype 1
`transaction_document` + `transaction_line`** (now 10 types, one table pair —
`PURCHASE_REGISTER` moved off it this session).

**Typed-tables redesign, steps 1–3 of `TYPED-TABLES-PLAN.md` §10 are done**
(149 tests green from a clean cluster; the 23 new tables have DDL gates only):

- shared `010` — `platform_ref` domains (`gstin`, `money_inr`, `qty`,
  `tax_rate`) and `document_type.table_name`
- `migrations/tenant_ext/<slug>/` — per-tenant column extensions, checksum-pinned,
  `drift_report()` is now per tenant. §7 is confirmed and built, not just proposed
- tenant `008`/`009` + shared `011`/`012` + `src/silver/sales_register.py` —
  **`A1.01 SALES_REGISTER` end to end**, reconciled 2026-08-07 against
  `reference/inafin_a1_schema.sql`. The reference pattern, **awaiting review**
- tenant `010` + shared `013` — **the other 23 Group A1 registers**, DDL + `v1_`
  views + registry `table_name`. All 24 reference tables now exist in every
  tenant's Silver schema.

**A1 is now LOADER-COMPLETE** (2026-08-07). `src/silver/registers/` loads all 23
flat types; `src/silver/sales_register.py` still owns A1.01, which is the only
type with a header/line split. 374 tests green from a clean cluster.

The 23 are **spec-driven, not 23 copies of `sales_register.py`**:

- `registers/spec.py` — `RegisterSpec`: table, period column (`tax_period` or
  `fy`), key kind, the unique index's columns, business columns in DDL order.
- `registers/catalog.py` — the 23 specs, transcribed from tenant migration 010.
- `registers/loader.py` — one parser and one upsert, driven by the spec.

**The risk that buys, and what pays for it.** A spec can drift from its DDL, and
nothing would fail until a real client file arrived. So the spec is not trusted:
`tests/conformance/test_register_specs.py` reads `information_schema` and
`pg_index` on a live tenant schema and asserts, per type, the column set, the
column *order* (row_hash depends on it), the required flags, the kinds, the
period column and the unique index's columns. **The table is the source of truth;
the spec is a projection of it.** Do not "simplify" that gate away.

Two key strategies, both in the DDL and both now exercised: NATURAL (the document
identifies itself — a correction supersedes) and CONTENT (it does not — a
correction lands *beside* the original). The second is a real limitation of those
registers, and `test_a_changed_row_supersedes_...` is where it is visible rather
than merely documented.

Three mutation checks were run and each failed exactly one test: a wrong
`required` flag, a key that drifts from its index, and `=` instead of
`IS NOT DISTINCT FROM` on a nullable key column. That last one found a real gap —
**a unique index containing a nullable column does not enforce itself**; the
loader is correct, the index is not. `NULLS NOT DISTINCT` migration is in
`TODO.md`.

Not built: extraction adapters (PDF → `InstrumentRecord`), `inafinplatform/v2`
wiring, the six deploy blockers in `TODO.md`.

**The archetype work above has been unwound for `PURCHASE_REGISTER`, the one
`STRUCTURED`-typed member of archetype 1** (step 4, 2026-08-10) — see
`TYPED-TABLES-PLAN.md` §9 for what survives, what is replaced, and
`HANDOFF-2026-08-10.md` for the detail. No production data exists (all six
deploy blockers open, including B5), so this was new migrations against empty
tenant schemas. `transaction_document` still holds the other 10 archetype-1
types unchanged; only `PURCHASE_REGISTER` moved.

`README.md` §"Where the schema actually lives" lists every schema, table and view
with the psql to re-derive it, plus the naming and column conventions — read that
instead of inferring conventions from whichever migration you happen to open
first. It still describes the archetype tables.

## Next session starts here

**The dispatcher, trigger wiring, and OCR fallback are built as of 2026-08-14
(sixth session).** Read the "Current state (sixth session)" entry above
first — do not re-propose any part of `src/dispatch/`, the
`dispatch_mechanism` registry column, `POST /artefacts/{id}/trigger`'s real
dispatch, or `FallbackPdfTextReader`/`PaddleOcrReader`. **The load-bearing
rule going forward, mirroring `extraction_spec`'s: which of the four
mechanisms handles a `doc_type_code` is `platform_ref.document_type
.dispatch_mechanism` DATA — never a new if/elif branch keyed on `doc_type_code`.**
Adding the next document type under an existing mechanism is a CSV row.
`src/extraction/` itself (fifth session) is unchanged.

**The `inafin-api` migration situation from the sixth session is resolved
as of the seventh session** — read "Current state (seventh session)" above
first, including its late-session addendum. Do not re-propose restoring
`/tmp/inafin_api_migrations_holding/`; that sequence is formally withdrawn
and superseded by shared migrations `024`-`031` / tenant `021`-`025`.

**Read `HANDOFF-2026-08-14-session7.md` before doing anything else this
session** — it has the full detail and the exact open question below.

00. **Reconcile `platform_ref.onboarding_customer` and the other onboarding
    tables (migrations `026`-`028`) against `inafin-api`'s real write
    contract.** These were built from table names only, guessed at the
    column level — `inafin-api`'s hand-off spec
    (`inafin-api/docs/tenant-data-api-compatibility.sql`) says the shapes
    are "materially different" and explicitly says not to patch this with
    ad hoc `ALTER`s. **Get the actual content of `inafin-api`'s
    `migrations/platform/0002_onboarding_smart_questions.sql` through
    `0004_onboarding_state.sql`** (the withdrawn files themselves, not just
    table names) and either adopt their column shapes directly, or design a
    deliberate versioned replacement contract with `inafin-api` adapting to
    it — not another single-session guess. This blocks `inafin-api` actually
    integrating the onboarding flow.
0. **Never run `docker compose down -v` or reset this Postgres, ever** —
   permanent standing rule as of the seventh session (this server is now
   shared with `inafin-api`/`inafin-portal`). The old item 0 here used to
   ask for a clean-cluster proof; that verification method no longer
   exists. Verify with targeted DDL and catalog queries against the live
   cluster only, as `024`-`031` already were.
0b. **`inafin-api`'s own code changes are outstanding, in that repo**: drop
    the RLS admin path in favour of `SET LOCAL ROLE t_<slug>_recon`, migrate
    off `platform_ref.tenant` onto `app.v1_tenant_directory`, move admin SQL
    onto the new `{{gold}}` tables. `API-CONTRACT.md` has the exact list.
    Not something this repo can do — flag it, don't attempt it.
0a. **Fix, or get sign-off to leave, `test_isolation.py::test_support_is_read_only`'s
    order-dependence on `test_extraction_transaction.py`** (pre-existing,
    both from the fourth session — see "Discovered this session" above).
    Simplest real fix: `test_extraction_transaction.py`'s two archetype-1 PDF
    extraction tests should use a fresh `entity_id` (`uuid.uuid4()`) instead
    of `tenant_a.entity_id`, the same convention `test_transaction.py`'s
    `_promote` helper and this session's `test_api_ingest.py` both already
    follow, with the same reasoning stated in both places.

Priority order for what's actually open, next:

1. **A live LLM escalation tier.** `Partial` outcomes quarantine; they do not
   auto-retry through an LLM. Design agreed 2026-08-06 in `TODO.md`, never
   built, still stubbed-in-CI only per that design.
2. **`extraction_audit` / per-field provenance.** Deferred per
   `streamed-marinating-gray.md`'s explicit scope — `_quarantine`'s `reason`
   text is the only record of why a PDF extraction failed. Matters more now
   that OCR-sourced text exists (`PdfText.source == "ocr"`) and is more
   lossy than native text — this table is where that provenance would live,
   and where an OCR `Partial` escalating to the LLM tier (item 1) at a lower
   bar would be decided.
3. **The three known tier-1 gaps in the fifth session's extractors** — not
   bugs, but real coverage gaps a future session might want to close if
   better specimens arrive: `BillOfEntryExtractor` cannot bind
   `taxable_value`/`igst_amount` (colon-less amount table);
   `SEZ_ANNUAL_PERFORMANCE_REPORT`/`CUSTOMS_DUTY_EXEMPTION_CERTIFICATE` can't
   bind `valid_from`; 4 of 7 entity-master types can't bind `as_of_date`. All
   are asserted as named `Partial`s in the test suite, not silently accepted.
4. **v2 sign-off on the dropped `v1_purchase_invoice_line` view** (unchanged
   from the third session — see `TYPED-TABLES-PLAN.md` step 4).
5. **Real auth for the ingestion surface.** `src/api/auth.py`'s
   `StaticTokenAuth` is still a placeholder. Unchanged from prior sessions.
6. **`entitlement_instrument`** (tenant `006`, 33 HYBRID types) and **the 63
   HYBRID types generally** — unchanged from prior sessions.
7. **Excel adapter for the loader path.** Unchanged; CSV/NDJSON/PDF all work
   now.
8. **The six deploy blockers in `TODO.md`** (B1–B6). None have moved.
9. **A live ClamAV container for local dev.** Unchanged.
10. **A worker process consuming `load_trigger`, independent of the API
    request path.** `dispatch_load` (sixth session) is now called
    synchronously from `POST /trigger`; a worker that instead polls
    `load_trigger` and calls the same `dispatch_load` function would be an
    alternate caller, not new logic — useful if trigger latency ever needs
    to move off the request path, not currently blocking anything.
11. **A real GSTIN lookup for the trigger request.** `TriggerRequest.gstin`
    is still a caller-supplied field (sixth session) rather than resolved
    from the entity's own registration record, which has no lookup in this
    codebase yet — the same simplification the fourth session named for the
    three PDF-register extractors' `entity_gstin` class constant.

**The reference schema is `reference/inafin_a1_schema.sql`** (received
2026-08-07). It covers A1.01–A1.24 and is the column inventory the typed-tables
work was built against — still worth reading before adding any new A1-adjacent
table. §10 of `TYPED-TABLES-PLAN.md` records what changed against it and,
more importantly, **what our design keeps because the reference has no
substitute** (Bronze lineage, a real batch FK, bitemporality, the natural key,
schema-per-tenant isolation).

Column names for all A1 tables follow the reference (`qty`, `uom`, `cgst`, …).

### The mutation check is not a formality

Three real defects were found by it this session, none of which any passing test
showed: a negative gate that COMMITTED its probe row when the constraint it
guarded was dropped (so restoring the constraint then failed); an isolation test
that never re-migrated the second tenant, so the leak it claimed to detect was
unreachable; and a natural-key test that pinned the loader's lookup while leaving
the unique index free to drift. Break the thing, confirm exactly one test fails,
restore.

**Do NOT build Archetype 2** (`register_snapshot` + `register_line`). That was
the previous plan and it is withdrawn. **This still stands as of 2026-08-11**
— the fourth session added 3 new types to the EXISTING flat
`RegisterSpec`/`RegisterLoader` mechanism (tenant migration `020`:
`payroll_tds_register`, `firc_brc_register`, `form_15ca_15cb`), the same
mechanism the 23 Group A1 registers already use, not the withdrawn generic
archetype-2 table pair below. Two reasons the withdrawal itself still holds,
both worth knowing:

- `PERIODIC` is a cadence, not a shape. Archetype 2's 22 `STRUCTURED` types
  include `AMAZON_MTR`, `CREDITOR_AGEING_REPORT`, `STOCK_REGISTER` and
  `PAYROLL_TDS_REGISTER`, which share no columns. It was cut on the wrong axis.
- The claim in the old handoff that "every archetype-2 type is `PERIODIC`" is
  **false**. Of the 27 in Operational scope, 24 are `PERIODIC` and 3 are
  `CONTINUOUS` (`A7.04 FORM_15CA_15CB`, `B6.01 DGFT_EBRC`, `D2.01 FIRC`). All
  three are individual certificates, not period registers, and look
  mis-archetyped. Verify registry claims against the CSV; that one was wrong.

### Extraction adapters — BUILT 2026-08-11 (fourth session)

Was deferred on input availability, not priority, since 2026-08-06 ("no LUT
or EPCG sample exists anywhere in the workspace"). The blocker was lifted the
same day the input arrived (third session, inspection only) and the tier-1
deterministic parser was built the same day (fourth session) —
`src/extraction/`, see the "Current state (2026-08-11, fourth session)"
section above for the full build. **Do not re-read this subsection as "not
yet built"** — it is, for all 50 specimens in `reference/A1-A7Documents/`.

What is still true from the original asymmetry argument, and still the
operating principle for whatever comes next: structured ERP data is the case
where the export schema is a contract *we* specify, so a synthetic fixture
has real fidelity — mock an ERP CSV freely, never mock a document layout with
no ground truth. Applied one level deeper, OCR is still unbuilt for exactly
this reason: all 50 specimens have native text layers, so a PaddleOCR
fallback tier would still be built against zero real fixtures if written now
— see "Next session starts here" item 3 and `HANDOFF-2026-08-11.md`'s open
questions on that tier (provenance weighting, what `NoTextLayer` means once
OCR exists). The LLM escalation tier (item 4) is the same story: designed
2026-08-06, still stubbed, still not live.

### The per-type contract is registry data — SUPERSEDED

**This mechanism is being retired** (`TYPED-TABLES-PLAN.md` §1, §9). It is
described here because the code still exists and the first migrations have to
unwind it, not because it is the design to extend. The per-type contract becomes
DDL: typed columns, domains and CHECKs on the type's own table.

Archetype 1 is driven by `document_type.field_contract` — one cell per type,
grammar and parser in `src/silver/contract.py`:

```
direction=INWARD;counterparty=FOREIGN;doc=be_number:text!,port_code:text!
```

`counterparty` is the load-bearing one: `REQUIRED` (purchase register),
`OPTIONAL` (B2C sale — absence is a fact), `FOREIGN` (import BoE — a GSTIN
*present* is the error). Nothing in `validate.py` or `promote.py` names a
document type. Adding the twelfth must stay a CSV row plus a fixture; if it
needs a branch, stop and fix the archetype (ARCHITECTURE.md §6).

`scripts/gen_registry_seed.py` imports the parser, so a contract that would fail
at ingestion cannot be committed.

### Mock data: generated *and* hand-written, on purpose

Real client documents are not available yet. `scripts/gen_mock_erp.py` generates
contract-conformant exports for any archetype-1 type — but it reads the same
contract the validator enforces, **so a wrong contract produces a file that
validates perfectly**. `tests/fixtures/*_handwritten.csv` were typed against what
the document actually looks like, never generated. Do not regenerate one to make
a test pass; read `tests/fixtures/README.md` first.
