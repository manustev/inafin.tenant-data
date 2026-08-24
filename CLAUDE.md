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

**Green as of 2026-08-24 (eleventh session): 569 passed / 2 skipped / 0
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
13. **A worker consuming `load_trigger`** independent of the request path —
    an alternate caller of `dispatch_load`, not new logic. Not blocking.
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
