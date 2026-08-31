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
| **`SESSION-HISTORY.md`** | Sessions 1–10 verbatim — how everything below was built and verified, what was tried and rejected. Archived out of this file 2026-08-24 to keep it small |
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
make ci                      # ruff + mypy + 569 tests + isolation + static gates
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

## Current state

**Green as of 2026-08-24 (fourteenth session): 602 passed / 2 skipped / 0
failed**, `make lint` + `make typecheck` clean, `make migrate` zero drift —
all against the live shared cluster, never a reset.

Full narrative of how everything below was built and verified is in
**`SESSION-HISTORY.md`** (sessions 1–10, archived verbatim from this file).
Read it before proposing anything that looks like new work in an area listed
here — almost all of it is built.

### Built and working — do not re-propose any of this

| Area | What exists |
|---|---|
| Isolation | schema-per-tenant, GRANT-only, `app_login` NOINHERIT + `SET LOCAL ROLE`; templated tenant migrations (`src/migrate/runner.py`) |
| Bronze | insert-only `artefact_ledger`, intake gate (`src/bronze/filecheck.py`, `scan.py` + `VirusScanPort`), MinIO object store with Object Lock |
| Registry | `platform_ref.document_type`, 125 in-scope types; `field_contract`, `extraction_spec`, `table_name`, `dispatch_mechanism` are all **registry DATA**, not code branches |
| Silver A1 | all 24 A1 registers load — `sales_register.py` (header/line) + 23 spec-driven via `RegisterSpec`/`RegisterLoader`, kept honest by `test_register_specs.py`'s DDL gate |
| Silver A2–A7 | `src/extraction/` — 7 archetype bases, registry-driven specs, all 50 specimens in `reference/A1-A7Documents/`; typed tables for archetypes 3/4/6/7/8 |
| PDF text | `PypdfReader` + `FallbackPdfTextReader` + real `PaddleOcrReader` (optional `ocr` extra, verified against real weights) |
| Dispatch | `src/dispatch/router.py`'s `dispatch_load` — 5 mechanisms: `PDF_EXTRACTION`, `SALES_REGISTER`, `REGISTER_LOADER`, `ARCHETYPE1_PROMOTE`, `GSTN_JSON_PROMOTE` |
| API | `src/api/` REST upload/trigger/status + GraphQL reads; `POST /artefacts/{id}/trigger` dispatches synchronously in-process |
| Manifest | `SilverWriteResult.batch_id` → `src/dispatch/manifest.py` → `BatchPublisherPort`/`_publish_if_ready`, wired for all mechanisms, verified with a recording fake |
| Connectors | `src/connectors/` — `SourceConnectorPort`, working `LocalFixtureConnector`, 7 live adapter stubs, `factory.build_source_connector` keyed on `Settings.source_data_mode` |
| Category B | `GSTR_2B` (tenant `026`, shared `033`) and `GSTR_3B` (tenant `027`, shared `034`) built end to end and **both now verified against the live DB** |
| Platform | shared `024`–`032` identity/onboarding/catalog/grants, reconciled to `inafin-api`'s real contract; `API-CONTRACT.md` is the published boundary |
| Schema catalogue | `platform_ref.document_type_schema`/`document_type_field` (shared `035`/`036`) — the tenant-facing export contract for all 125 in-scope types, derived by `src/catalogue/document_schema.py`, never hand-written |
| Schema releases | `schema_release`/`schema_artifact` (shared `039`) + the `<prefix>-platform` bucket; `scripts/publish_schema_release.py` publishes a release, `v1` is CURRENT |
| Schema pinning | `t_<slug>_bronze.schema_pin` (tenant `028`) — the release a tenant was handed, pinned on first upload by `src/catalogue/pin.py`, with a frozen copy in their own bucket |

### Load-bearing rules that came out of that work

- **Which mechanism handles a `doc_type_code` is registry data.** Adding a
  type under an existing mechanism is a CSV row, never a new `if/elif` on
  `doc_type_code`. A genuinely new mechanism costs one branch, once.
- **One table per registry document type** (`TYPED-TABLES-PLAN.md` §8).
  Collapse only where the shape is provably identical *and* the types always
  co-occur. **A shared `filed_return` header across GSTR types is already
  rejected** — each return type gets its own independent table set.
- **Never invent a vocabulary or a payload shape with no specimen.** Where
  ground truth is missing the parser *rejects* rather than guesses:
  `GSTR_2B`'s `cdnr` (all 24 specimens empty) and `GSTR_3B`'s `inter_sup`
  (all 31 empty) are quarantined, not modelled. Same reason `hsn_master`/
  `sac_master` ship seeded empty.
- **JSON is source of record for the nested GSTN returns** — GSTR-1/2B/3B's
  flat CSVs are confirmed lossy (2B's CSV covers only `b2b`, 228 of 258
  lines; 3B's is a 21-column summary of a 55-leaf-path JSON). CSV is
  sufficient only for genuinely flat types. Drives
  `LocalFixtureConnector`'s json > csv > pdf preference.
- **Applied migrations are never edited.** A correction is a new migration
  with a `DROP CONSTRAINT`/`ADD CONSTRAINT`, and the CSV row is updated by
  hand rather than by re-running `gen_registry_seed.py` over a pinned file.

### Fourteenth session (2026-08-24) — the manual local trigger (item #2 of 3)

**`tenantctl trigger` — a real CLI command, verified against the live
cluster, not just written.** `src/cli.py` gained `cmd_trigger` /
`_run_trigger`: `tenantctl trigger <slug> <ingest_id> <doc_type_code>
[--period-start] [--period-end] [--gstin]` dispatches one already-uploaded
artefact from a terminal — the actual "on local, manual trigger" the
three-item plan named. Manually verified end to end against the live
cluster: uploaded a real `PURCHASE_REGISTER` CSV, ran the CLI, watched it
write a real `ingest_batch` row and print `ACCEPTED (REGISTER_LOADER)
batch=<uuid>`, exit 0; a wrong `ingest_id` and a missing required field both
produce a clean one-line `ERROR:` and exit 1, no traceback.

**The route and the CLI cannot silently diverge, because they are now the
same function.** Before this session, `POST /artefacts/{id}/trigger`'s
entire "insert `load_trigger`, read the ledger, infer content format, fetch
bytes, call `dispatch_load`, fold `NoDispatchMechanismError`/
`ValidationRejected` into a recorded-not-dispatched outcome" sequence lived
only inside the route handler. Extracted to `src/dispatch/trigger.py`'s
`record_and_dispatch_trigger` — the route was refactored to call it (net
smaller: five now-unused imports dropped,
`MissingDispatchFieldError`/`UnknownExtractorError`/plain `ValueError` all
fold into one `except ValueError` since the first two are subclasses of the
third), and `cmd_trigger` calls the identical function. All 17 pre-existing
`test_api_ingest.py` trigger tests passed unchanged after the refactor —
proof the extraction changed nothing about the route's behavior.

**The module docstring states a real design rule, not just prose**: caller
mistakes (unknown `ingest_id`, a missing required field, an unrecognised
extension) RAISE, for each caller to map onto its own surface (HTTP
404/422, or a CLI exit code + stderr line); legitimate outcomes of a
well-formed trigger (`UNROUTED` — no loader built yet; `QUARANTINED` — the
document's own content failed validation) fold into
`TriggerOutcome.status`, never raise, because `load_trigger`'s row is real
and durable either way. Mutation-checked by swapping the fold for a
re-raise: failed exactly the 2 tests naming that split, plus the
pre-existing route test for the same case (`test_trigger_reports_unrouted_
for_a_type_with_no_dispatch_mechanism`) — 3 failures, all expected, nothing
else. Restored.

**New tests**: `tests/handoff/test_trigger_dispatch.py` (5 tests, calling
`record_and_dispatch_trigger` directly — the well-formed path, the two
RAISE shapes, the two FOLD shapes) and `tests/handoff/test_cli_trigger.py`
(4 tests, run as real subprocesses via `python -m src.cli trigger` against
the live cluster — success, unknown ingest_id, missing field, and a parity
test that the CLI's dispatch produces a real `ingest_batch` row queryable
the same way the HTTP route's would be).

**Scope decision, asked and answered**: the original #2 also named a
"scheduled job" for production use. Deliberately NOT built this session —
Steve confirmed stopping at the manual trigger. The design trap flagged when
#2 was first scoped still stands and is unchanged: `load_trigger` has no
status/claimed-at column BY DESIGN (Bronze is insert-only, invariant 6), so
a poller's "already processed" query has to be derived from whether a
matching `ingest_batch`/quarantine exists, never from a status flag added to
`load_trigger` itself. See backlog item 10 below — still where this picks
up.

**Verified**: `make lint`/`make typecheck` clean, full suite 602 passed / 2
skipped (up from 593). No migration — this session touched only Python
(`src/cli.py`, `src/dispatch/trigger.py`, `src/api/routes_ingest.py`).

### Thirteenth session (2026-08-24) — Bronze intake gaps (item #1 of 3)

Closed the three intake gaps flagged when the three-item plan (upload→Bronze;
trigger; schema/sample download — the last one is items #3, above) was first
scoped. #2 (trigger/scheduled job) is still open — this session did #1 only.

**A real gap, closed: nothing validated `document_type` before this
session.** `BronzeIngestionService.receive` defaulted `document_type` to
`"PURCHASE_INVOICE"` — one of the five retired Phase-1 codes that predate
`platform_ref.document_type` and are not even ROWS in it — and the only
guard was the ledger's FK against `platform_ref.universal_master`'s free
132-value vocabulary, not the 125-row in-scope registry. A tenant could
upload a file, get a 201, and the artefact would sit in Bronze forever,
undispatchable, discovered only much later if at all.

Fixed at the narrowest point that closes it for every caller, not just the
HTTP route: `document_type` lost its default (now a required keyword
argument) and `BronzeIngestionService.receive` gained
`_check_document_type_in_scope`, run right after `check_file` and before
hashing — a type that is not a row, or is a row with `in_scope = false`, is
refused with a distinct message for each case. A type that IS in-scope but
has no `dispatch_mechanism` yet (`GSTR_1` — Stream A, GSTN-API-polled, never
upload-triggered) is correctly ACCEPTED: this gate checks registry
membership, not dispatch readiness, and conflating the two would refuse
every one of the 38 UNSPECIFIED types the twelfth session's catalogue work
found.

Every existing caller that relied on the removed default was updated to
declare a real type explicitly (`tests/handoff/test_handoff.py`,
`test_outcome_gate.py`, `test_intake_gate.py` — matched to what each test's
payload actually represents, not defaulted to one code everywhere).

**Provenance**: the upload route now passes `received_from="PORTAL"`
explicitly rather than relying on the service's generic `"upload"` default —
worth doing now, before a second caller (a source connector pulling from
GSTN/ICEGATE, `src/connectors/`, still uncalled) starts writing through the
same service and this column becomes load-bearing for telling the two apart.

**`POST /artefacts/batch`, new.** A tenant drops several files of the SAME
`document_type` in one request; each file is processed independently through
the same `BronzeIngestionService.receive` — one bad file (wrong extension, an
unrecognised type) never fails the files around it. Always returns 200; the
body's `items` list carries the real per-file outcome, the same "status line
can't represent a mix, the body must" shape `StatusResponse.status`'s
PARTIAL/QUARANTINED vocabulary already uses one level down, at row grain.
**Deliberately not mixed-type**: `document_type` is one form field, not a
parallel array keyed to `files` — a parallel-array multipart shape is exactly
the kind of surface that silently misaligns (file 3 gets file 4's type) with
nothing but manual testing to catch it. A portal batching several document
types makes several calls, one per type. Bounded to `MAX_BATCH_FILES = 100`
so one request cannot hold a connection open indefinitely.

**Mutation-checked, three gates**: removing the document-type check call
failed the 3 targeted gate tests (plus 2 unrelated intake-gate tests that
share a literal payload byte-string with the now-unrejected upload — a real
but separate test fragility, not a production bug, left as-is since the
un-mutated suite is green); flipping the in-scope branch failed exactly
`test_an_out_of_scope_registry_row_is_refused`; removing
`received_from="PORTAL"` failed exactly the provenance test; removing the
batch route's per-file try/except failed exactly
`test_batch_upload_one_bad_file_does_not_fail_the_others`. All restored.

**Verified**: `make lint`/`make typecheck` clean, full suite 593 passed / 2
skipped (up from 582) — 4 new gate tests
(`tests/handoff/test_document_type_gate.py`) plus 6 new route-level tests
in `test_api_ingest.py` (unknown/missing type, provenance, 3 batch-route
tests). No migration needed — this session touched only Python.

**Not built this session**: nothing outstanding from the original three-gap
list — 1a (validation), 1b (multi-file), 1c (provenance) are all closed. #2
(trigger/scheduled job — a CLI dispatch command, auto-trigger on upload, and
the `load_trigger` worker-poller shape discussed but not built) is next.

### Twelfth session (2026-08-24) — the published schema catalogue

Built the whole of item #3 from the three-item plan (portal upload → Bronze;
trigger; **schema/sample download**). Steve's design, not the one originally
proposed: schema definitions live in `platform_ref`, the FILES live in MinIO
with the release in the object key, and a tenant is PINNED to the release they
downloaded at first upload so a later platform release cannot change the
contract under an integration in flight. `inafin-api` builds the read API on
top — it already has the grants, and `API-CONTRACT.md` now specifies it.

**The catalogue is derived, never written.** `src/catalogue/document_schema.py`
projects four existing sources of truth into one published shape and records
which one each row came from: `provenance` DERIVED (a `RegisterSpec` or
`sales_register.py`, both already gated against live DDL), DECLARED (a
`field_contract`/`extraction_spec` grammar cell, parsed by the same parser
ingestion uses), or PENDING (nothing published yet — an honest empty, so the
portal can say "sample only" instead of rendering a blank table). 125 types,
447 fields. `description` is nullable and deliberately unset: no column
comment exists anywhere in this schema, and writing 447 prose descriptions
from column names is the invented-content mistake this file already names once.

**A generator hazard was found by tripping over it, and is now guarded.**
Running `gen_registry_seed.py` silently rewrote applied, checksum-pinned
migrations `004` and `023` — both legitimately differ from a fresh generation
because later migrations (`014`, `033`) corrected them and the corrections were
written back into the CSV. The fifth session hit this too and caught it by eye.
`_emit` now refuses to rewrite any generated file whose content would change,
and `KNOWN_SEALED` names those two with their reasons, so a routine run stays
quiet about them while ANY other drift is a hard error.

**A REAL LATENT BUG, found by a gate rather than by reading — flag this to
`inafin-api`.** Migration `030`'s entire ambient `platform_ref` grant list —
every read AND the onboarding writes — **has never worked**. `app_login` held
the table privileges but was never granted `USAGE ON SCHEMA platform_ref`, so
every one of them was unreachable. True since 2026-08-14; fixed by shared
`038`. The seventh session's verification missed it because it queried
`information_schema.role_table_grants`, which correctly showed every GRANT as
present — **a recorded privilege is not a usable one**. `app.v1_tenant_
directory` worked throughout (schema `app` grants USAGE to PUBLIC), which is
exactly why tenant resolution appeared to function while everything after step
one did not. The gate that caught it opens a real bare `app_login` connection
and issues a real `SELECT`; that is the shape to copy.

**Other things worth knowing:**

- **`v1_schema_pin` was created and then withdrawn one migration later**
  (tenant `028` then `029`). `app.apply_tenant_grants` grants Bronze objects
  with `relkind = 'r'` — tables only; Silver has a second loop for `v1_` views,
  Bronze has none. A hand-written GRANT would not survive either, since step 1
  is `REVOKE ALL ON ALL TABLES` (views included), re-asserted every provision.
  Adding a Bronze view loop would change the isolation matrix for every tenant
  to serve one internal helper — not worth it. The DISTINCT ON now lives in
  `current_pin`, the single place callers already went through.
- **`ANOMALY_KEY.json` is never published.** It is the expected-results oracle
  for the Category B mock set (19 seeded anomalies); publishing it to the
  tenants whose data those anomalies exist to catch would hand over the answer
  sheet. `_EXCLUDED_FILENAMES` is the defence and
  `test_publish_schema_release.py` pins it — including a case that plants the
  oracle inside a ref folder, because the "it lives at the root" layout is an
  accident that a regenerated sample set could undo.
- **B4.03/B4.04 samples are correctly skipped** — they belong to
  `inafin-gst-corpus`, confirmed by the publisher reporting them as unmapped
  rather than silently dropping them.
- Steve chose to publish the reference samples as-is (193 files). They are
  synthetic, and the mock Category B set contains deliberate anomalies — that
  was a flagged, accepted call, not an oversight.

**Mutation-checked**: removing the `ensure_schema_pin` call failed exactly the
3 pin tests (the broken-store test correctly still passed); emptying
`_EXCLUDED_FILENAMES` failed exactly the 2 name-based oracle tests; corrupting
one published field name failed both catalogue gates independently; revoking
the `038` USAGE failed exactly the grant gate. All restored.

**Two test weaknesses found and fixed while writing these**: identical CSV
payloads meant "second upload" was being deduped at intake and never reached
the pinning code, so that test was passing for the wrong reason; and a test
asserting an UNSPECIFIED type pins nothing was wrong about the design, not the
code (all 125 in-scope types get a schema file — a PENDING one still tells a
tenant something useful).

**Still open on #3**: nothing built here serves the files over HTTP — that is
`inafin-api`'s half, specified in `API-CONTRACT.md`. Rolling a tenant forward
to a new release has no operator command yet (it is an INSERT into
`schema_pin`, deliberately). Field `description`s are unwritten and want a
human, not a generator.

### Eleventh session (2026-08-24) — what actually happened

The stack was down from the tenth session (Docker Desktop crash on the host,
all four containers `Exited (255)`). `docker compose up -d` brought it back;
the postgres container was *recreated* (image pull), **the named volume and
all data survived** — verified by listing `tenant_db`'s schemas before
touching anything.

Then the tenth session's unfinished business, item `0g`, in order:

1. `make migrate` applied shared `034` + tenant `027` to both tenants for the
   first time, zero drift.
2. `tests/conformance/test_gstn_returns_gstr3b.py` ran for the first time —
   8 passed. (The handoff said "12 tests"; it is 8. No test is missing, the
   count in the note was wrong.)
3. Full suite surfaced exactly one failure, the expected one:
   `test_register_specs.py::test_the_catalog_covers_every_a1_type_except_
   sales_register`. `GSTR_3B` needed adding to that test's named-exception
   set, the same fix `GSTR_2B` needed — its scope is A1 coverage, so a third
   named non-A1 exception is correct, not a widened assertion.
4. **Mutation-checked `GSTR_3B`'s two vocabulary gates, which found a real
   gap.** The ITC `ty` gate is covered (breaking it failed exactly
   `test_unrecognised_itc_type_is_rejected`). The Table 5 inward-supply `ty`
   gate was **not covered by anything** — breaking it failed zero tests. Added
   `test_unrecognised_inward_supply_type_is_rejected`, re-broke the gate,
   confirmed exactly that one test fails, restored.

**GSTR_3B is now verified, not merely written** — the caveat the tenth
session's handoff insisted on is discharged.
## Next session starts here

Everything in "Built and working" above is done — check there first. What is
genuinely open, in priority order:

**Category B (the active workstream):**

1. **GSTR_1 / GSTR_9 / GSTR_9C are not built.** Each needs its own table set
   (own migration, per §8), its own `src/silver/gstn_returns/<type>.py`
   parser (bespoke Python, not a spec grammar — decision D3), and a
   `_GSTN_RETURN_LOADERS` entry in `src/dispatch/router.py`.
   `gstr2b.py`/`gstr3b.py` are the pattern. **Read each type's real JSON
   first** — GSTR_3B already proved GSTR_2B's shape doesn't generalise;
   GSTR-1 is a grouped list *and* a flat summary in one payload
   (b2b/b2cl grouped, b2cs flat), and GSTR_9C's reconciliation tables look
   like neither.
2. **No orchestrator calls the connectors.** `src/connectors/` can `fetch()`
   any Category B ref in local-fixture mode, but nothing iterates
   `(tenant, doc_type_code, gstin, period)` → `fetch()` →
   `BronzeIngestionService.receive()` → `dispatch_load()`. Agreed shape: a
   synchronous "pull now" function/CLI first (upload-first, decision D2); a
   poller becomes a second caller of the same function later. Not started.
3. **The "cheap 14" flat B-types** (B2.02/03/06/07, B3.01–07, B6.02/03) have
   no loader. Recommended approach (a typed shared-archetype-table loader
   over `entity_master_record`/`proceeding_event`/`entitlement_instrument`,
   not a new PDF-extraction path) accepted in principle, **explicitly
   deferred by Steve**.
4. **CDNR parsing for GSTR_2B**, and `inter_sup` for GSTR_3B — blocked on a
   real specimen, not on design. The DB vocabulary already allows CDNR, so
   it is a parser change away, not a second migration.
5. **Live source credentials** for the seven adapters are unconfigured, as
   expected. Wiring a real GSP/ICEGATE/DGFT/IRP contract is a separate task
   per source system; nothing built so far depends on it.

`HANDOFF-2026-08-19-categoryB.md` is still the read-first document for this
workstream, with one correction: its D1/D2/D3 items are all **decided** (its
own text is stale on that). Trust this file over it.

**Longer-standing backlog, unmoved:**

6. **A live LLM escalation tier** — `Partial` outcomes quarantine, they do
   not auto-retry. Design agreed 2026-08-06 in `TODO.md`, still stubbed.
7. **`extraction_audit` / per-field provenance** — `_quarantine`'s `reason`
   text is the only record of why an extraction failed. Matters more now
   that OCR-sourced text exists (`PdfText.source == "ocr"`), and is where an
   OCR `Partial` escalating at a lower bar would be decided.
8. **Three known tier-1 extraction gaps**, all asserted as named `Partial`s,
   none silently accepted: `BillOfEntryExtractor` can't bind
   `taxable_value`/`igst_amount` (colon-less amount table);
   `SEZ_ANNUAL_PERFORMANCE_REPORT`/`CUSTOMS_DUTY_EXEMPTION_CERTIFICATE`
   can't bind `valid_from`; 4 of 7 entity-master types can't bind
   `as_of_date`. Need better specimens, not better code.
9. **v2 sign-off on the dropped `v1_purchase_invoice_line` view** — a
   breaking contract change never raised downstream (`TYPED-TABLES-PLAN.md`
   step 4).
10. **Real auth** — `src/api/auth.py`'s `StaticTokenAuth` is a placeholder;
    ARCHITECTURE.md 5.6 wants a signed Keycloak JWT claim.
11. **Excel adapter** for the loader path (CSV/NDJSON/PDF/JSON all work).
12. **The six deploy blockers in `TODO.md`** (B1–B6). None have moved.
13. **A scheduled worker/poller consuming `load_trigger`**, independent of
    both the request path and the fourteenth session's manual CLI trigger.
    Confirmed 2026-08-24: not built, by explicit choice — the manual trigger
    (`tenantctl trigger`) covers local/on-demand use; a poller is deferred
    until the real deployment shape (cron? long-running worker? tenant
    count?) is known. Would call `record_and_dispatch_trigger`
    (`src/dispatch/trigger.py`) exactly as both existing callers do — no new
    dispatch logic, only the scan-and-claim loop around it. **The one real
    design trap, unchanged since first flagged**: `load_trigger` has no
    status/claimed-at column BY DESIGN — Bronze is insert-only (invariant 6)
    — so a poller cannot claim work with a status flag the way a typical job
    queue would. "Already processed" has to be derived from whether a
    matching `ingest_batch` row (or a quarantine) exists for that trigger,
    which lives in Silver, not Bronze. Get this wrong and a poller
    double-dispatches.
14. **A real GSTIN lookup** — `TriggerRequest.gstin` is still caller-supplied
    rather than resolved from the entity's registration record, the same
    simplification the PDF register extractors' `entity_gstin` constant makes.
15. **A live ClamAV container** for local dev.
16. **One open question from `inafin-api`**: `customer_document`/
    `data_requirement`'s optional nullable `doc_type_code` bridge column was
    a unilateral call. Fine as-is; dropping it is a one-line migration if
    they ever say it's unwanted.

**Reference schema**: `reference/inafin_a1_schema.sql` covers A1.01–A1.24 and
is the column inventory the typed-tables work was built against — read it
before adding any A1-adjacent table. `TYPED-TABLES-PLAN.md` §10 records what
our design keeps that the reference has no substitute for (Bronze lineage, a
real batch FK, bitemporality, the natural key, schema-per-tenant isolation).
Column names for A1 tables follow the reference (`qty`, `uom`, `cgst`, …).
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

### Mock data: generated *and* hand-written, on purpose

Real client documents are not available yet. `scripts/gen_mock_erp.py` generates
contract-conformant exports for any archetype-1 type — but it reads the same
contract the validator enforces, **so a wrong contract produces a file that
validates perfectly**. `tests/fixtures/*_handwritten.csv` were typed against what
the document actually looks like, never generated. Do not regenerate one to make
a test pass; read `tests/fixtures/README.md` first.
