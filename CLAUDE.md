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
| **`SESSION-HISTORY.md`** | The verbatim per-session narrative — what was built, verified, tried, and rejected, sessions 1–15. This file only keeps durable rules and the current backlog; read the history before proposing anything that looks like new work in an area the table below lists as built |
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
   Silver is *not* insert-only — closing a superseded row is an UPDATE, and
   that is judgement, so it does not weaken this invariant. `schema_pin`
   (tenant `028`) is the same shape one layer up: insert-only by GRANT (the
   ingest role holds no UPDATE/DELETE on it), so a roll-forward to a new
   schema release is always a new row, never an edit of the old one.
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
- **Do NOT build Archetype 2** (`register_snapshot`/`register_line`) — withdrawn.
  New PERIODIC register types are additional entries in the existing flat
  `RegisterSpec`/`RegisterLoader` mechanism the 23 Group A1 registers already
  use, not a second generic table pair. Full reasoning in `SESSION-HISTORY.md`.
- **A column's own constraint (a domain, a CHECK vocabulary, a numeric bound)
  is never hand-restated in Python.** `src/silver/registers/spec.py`'s `Kind`
  stays coarse on purpose, and the published schema catalogue's constraint
  columns (`document_type_field`, migration `041`) are DERIVED from
  `pg_constraint`/`pg_type`, never declared — see "Load-bearing rules" below.

## Workflow

```bash
make ci                      # ruff + mypy + tests + isolation + static gates
make migrate                 # shared chain, all tenants, drift check
make provision SLUG=acme
uvicorn src.api.app:app      # the ingestion surface (REST + GraphQL)
```

- Migrations are **checksum-pinned**. Never edit an applied migration — add a new
  one. A new registry column becomes a new migration; `004` regenerates
  byte-identically.
- `registry/document_types.csv` is the source of truth; run
  `scripts/gen_registry_seed.py` after editing, commit both. Same discipline
  applies to `scripts/gen_field_constraints.py` (migration `042`) — it needs a
  LIVE cluster to derive from (there is no offline rendering of
  `pg_constraint`), and it refuses to silently rewrite an applied migration.
- **Never run `docker compose down -v` or otherwise wipe/recreate the whole
  database.** This Postgres is a shared dev server across `inafin-api`,
  `inafin-portal`, and this repo (2026-08-14). A full reset destroys other
  repos' data, not just this repo's sandbox. Verify changes with targeted DDL
  (create/drop/alter individual tables, indexes, schemas) and by querying
  `information_schema`/`pg_catalog` against the existing cluster instead. If a
  genuinely clean-state proof is ever needed, that requires a separate,
  explicitly-scoped throwaway database — never this server.
- Prefer mutation-checking a new gate: break the thing, confirm exactly one test
  fails, restore. A suite nobody has seen fail is indistinguishable from
  `assert True`. This has found real defects almost every session it's been
  applied — see `SESSION-HISTORY.md` for specific examples if you want proof
  it's worth the extra step.

## Current state

**Green as of 2026-09-01 (fifteenth session): 630 passed / 2 skipped / 0
failed**, `make lint` + `make typecheck` clean, `make migrate` zero drift —
all against the live shared cluster, never a reset.

### Built and working — do not re-propose any of this

| Area | What exists |
|---|---|
| Isolation | schema-per-tenant, GRANT-only, `app_login` NOINHERIT + `SET LOCAL ROLE`; templated tenant migrations (`src/migrate/runner.py`) |
| Bronze | insert-only `artefact_ledger`, intake gate (`src/bronze/filecheck.py`, `scan.py` + `VirusScanPort`), MinIO object store with Object Lock |
| Registry | `platform_ref.document_type`, 125 in-scope types; `field_contract`, `extraction_spec`, `table_name`, `dispatch_mechanism` are all **registry DATA**, not code branches |
| Silver A1 | all 24 A1 registers load — `sales_register.py` (header/line) + 23 spec-driven via `RegisterSpec`/`RegisterLoader`, kept honest by `test_register_specs.py`'s DDL gate |
| Silver A2–A7 | `src/extraction/` — 7 archetype bases, registry-driven specs, all 50 specimens in `reference/A1-A7Documents/`; typed tables for archetypes 3/4/6/7/8. All five PDF archetype services now `record_or_supersede` — a re-dispatched artefact corrects its row (`src/silver/supersede.py`), it does not conflict |
| PDF text | `PypdfReader` + `FallbackPdfTextReader` + real `PaddleOcrReader` (optional `ocr` extra, verified against real weights) |
| Dispatch | `src/dispatch/router.py`'s `dispatch_load` — 5 mechanisms: `PDF_EXTRACTION`, `SALES_REGISTER`, `REGISTER_LOADER`, `ARCHETYPE1_PROMOTE`, `GSTN_JSON_PROMOTE` |
| Trigger errors | `src/dispatch/trigger.py` classifies DB failures into `UnknownArtefact` / `SilverConstraintViolation` (`kind` CONFLICT/INVALID); `POST /artefacts/{id}/trigger` and `tenantctl trigger` map to 404/409/422 — never an opaque 500 |
| API | `src/api/` REST upload/trigger/status + GraphQL reads; `POST /artefacts/{id}/trigger` dispatches synchronously in-process |
| Manifest | `SilverWriteResult.batch_id` → `src/dispatch/manifest.py` → `BatchPublisherPort`/`_publish_if_ready`, wired for all mechanisms, verified with a recording fake |
| Connectors | `src/connectors/` — `SourceConnectorPort`, working `LocalFixtureConnector`, 7 live adapter stubs, `factory.build_source_connector` keyed on `Settings.source_data_mode` |
| Category B | `GSTR_2B` (tenant `026`, shared `033`) and `GSTR_3B` (tenant `027`, shared `034`) built end to end and verified against the live DB |
| Platform | shared `024`–`032` identity/onboarding/catalog/grants, reconciled to `inafin-api`'s real contract; `API-CONTRACT.md` is the published boundary |
| Schema catalogue | `platform_ref.document_type_schema`/`document_type_field` (shared `035`/`036`) — the tenant-facing export contract for all 125 in-scope types, derived by `src/catalogue/document_schema.py`, never hand-written |
| Field constraints | `document_type_field`'s constraint columns + `document_type_rule` (shared `041`/`042`) — derived straight from live `pg_constraint`/`pg_type` by `src/catalogue/field_constraints.py`, gated against drift by `test_field_constraints.py` |
| Schema releases | `schema_release`/`schema_artifact` (shared `039`) + the `<prefix>-platform` bucket; `scripts/publish_schema_release.py` publishes a release. **`v2` is CURRENT** — carries the field-constraint block, `v1` kept but `SUPERSEDED` |
| Schema pinning | `t_<slug>_bronze.schema_pin` (tenant `028`) — pinned on first upload by `src/catalogue/pin.py`'s `ensure_schema_pin`; an operator rolls a tenant forward with `tenantctl reschema` (`roll_forward`/`roll_forward_all`) |
| Local auth | `AUTH_MODE=none` (`src/api/auth.py`'s `NoAuth`) for local dev only — `StaticTokenAuth` is still the non-`none` default and still a placeholder for real auth (backlog item 10) |

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
- **A "current row" lookup must key on the unique index's own columns, not
  "the business key" in the abstract** (`src/silver/supersede.py`). A lookup
  that disagrees with the index does not prevent a duplicate, it just moves
  where the duplicate appears. Closing a prior row without setting its
  `supersedes_*` pointer satisfies the index and silently breaks the
  bitemporal chain — a mutation check is what catches that, not a passing test.

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
   step 4). (Unrelated to the `v2` *schema release* above — same version
   number, two different things.)
10. **Real auth** — `src/api/auth.py`'s `StaticTokenAuth` is still a
    placeholder; ARCHITECTURE.md 5.6 wants a signed Keycloak JWT claim.
    `AUTH_MODE=none` (fifteenth session's context) is local-dev-only and
    does not touch this.
11. **Excel adapter** for the loader path (CSV/NDJSON/PDF/JSON all work).
12. **The six deploy blockers in `TODO.md`** (B1–B6). None have moved.
13. **A scheduled worker/poller consuming `load_trigger`**, independent of
    both the request path and `tenantctl trigger`'s manual CLI dispatch.
    Deferred until the real deployment shape (cron? long-running worker?
    tenant count?) is known. Would call `record_and_dispatch_trigger`
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
17. **Field `description`s** in the schema catalogue are unwritten and want a
    human, not a generator (twelfth session).

**Reference schema**: `reference/inafin_a1_schema.sql` covers A1.01–A1.24 and
is the column inventory the typed-tables work was built against — read it
before adding any A1-adjacent table. `TYPED-TABLES-PLAN.md` §10 records what
our design keeps that the reference has no substitute for (Bronze lineage, a
real batch FK, bitemporality, the natural key, schema-per-tenant isolation).
Column names for A1 tables follow the reference (`qty`, `uom`, `cgst`, …).

### Mock data: generated *and* hand-written, on purpose

Real client documents are not available yet. `scripts/gen_mock_erp.py` generates
contract-conformant exports for any archetype-1 type — but it reads the same
contract the validator enforces, **so a wrong contract produces a file that
validates perfectly**. `tests/fixtures/*_handwritten.csv` were typed against what
the document actually looks like, never generated. Do not regenerate one to make
a test pass; read `tests/fixtures/README.md` first.
