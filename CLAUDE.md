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
| `docs/adr/` | Architecture decision records — structural decisions from cross-repo requests (new roles, new schemas, contract shape). Read `0001`/`0002` before touching anything `recon_engine`/reconciliation-schema related |
| `docs/review/` | Open questions sent to a domain SME or another team, and their team's feedback with what was/wasn't actioned — read before assuming an inafin-reconciliation-engine contract is finished |

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

**Green as of 2026-09-03 (seventeenth session): 674 passed / 2 skipped / 0
failed**, `make lint` + `make typecheck` clean, `make migrate` zero drift —
all against the live shared cluster, never a reset. Schema release **`v8` is
CURRENT**.

Isolation, Bronze, the registry, Silver A1–A7, dispatch, the API, the
manifest publisher, connectors, Category B (GSTR_2B/3B), the platform/
onboarding tables, the schema catalogue + field constraints + schema
releases/pinning, local auth, and 4-of-7 archetype-7 table extractors are
**all built and working — do not re-propose any of this.** The full
area-by-area inventory, plus the load-bearing rules that came out of
building it (registry-driven dispatch, one-table-per-type, never-invent-a-
vocabulary, JSON-as-source-of-record for GSTN returns, migrations-never-
edited, supersede-keys-off-the-index), moved to **`SESSION-HISTORY.md`**
("Snapshot moved from CLAUDE.md on 2026-09-03") to keep this file short.
Read it before assuming something below is unbuilt.

**New this session (seventeenth): `inafin-reconciliation-engine` is a real,
active consumer, not a proposal.** It is a NEW service (not `inafinplatform/
v2`, which is legacy) with its own per-tenant schema/role
(`t_<slug>_reconciliation` / `t_<slug>_recon_engine`, shared migrations
`060`–`062`) and 4 of 7 requested Silver reader contracts
(`v1_rcm_payroll_tds_evidence`, `v1_rcm_director_evidence`,
`v1_rcm_purchase_candidate`, `v1_rcm_registration_history` — tenant
migrations `036`/`037`). The engine already holds `CREATE` on its own
schema and has created 8 real tables there. See "Next session starts here"
below for what's still open with them, and `docs/adr/0001`/`0002` +
`docs/review/` for the full decision trail — **do not re-propose Priority 0
or re-litigate the schema-vs-Gold question**, it is built and in active use.

## Next session starts here

Everything in "Built and working" above is done — check there first. What is
genuinely open, in priority order:

**inafin-reconciliation-engine (the newest active thread — read
`docs/adr/0001`/`0002` and `docs/review/` before touching any of this):**

0. **Fixtures still owed to the engine team.** They asked for real data on
   Acme AND Globex demonstrating: a same-GSTIN correction that changes the
   legal registration end date ("late correction"), and one that doesn't.
   Real data today only covers "open" (Active) and "cancelled/suspended
   from day one" — not a genuine two-step correction. Seed these through
   the real `GstinRegisterExtractor` pipeline (synthetic page text is fine,
   real PDF bytes are not required — `to_silver` only hashes them), not raw
   SQL inserts — see `tests/conformance/test_extraction_archetypes_6_7_4_8.py`
   for the pattern and the real specimen text this repo already has for
   `GSTIN_REGISTER` (`reference/A1-A7Documents/A2.07_GSTIN_Register.pdf`).
0b. **TD-RCM-003 (related-party evidence)** — still blocked on
    `RELATED_PARTY_REGISTER`'s table extraction, which needs a
    `pdfplumber`-based rewrite (see item 19 below), not a design decision.
0c. **TD-RCM-006 (foreign-payment enrichment)** — blocked on the domain
    SME's answer in `docs/review/sme-question-td-rcm-006-foreign-payment-
    evidence.md` (which document carries `place_of_supply`/
    `consideration_status`, plus a real specimen — schema alone won't
    unblock it).
0d. **DIR-12 / Forensic-mode question** — `docs/review/sme-question-dir12-
    director-evidence.md` is still open; Phase 1 is proceeding on the
    client-list-only assumption regardless (`docs/adr/0002`), this is a
    confirmation, not a blocker.
0e. **`recipient_is_business_entity`** (`v1_rcm_registration_history`) is
    `NULL` — no source states it, and this repo won't encode a tax-law
    derivation rule without SME sign-off. Same status as 0c: needs domain
    input, not code.

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
18. **`KMP_LIST`'s `as_of_date` gap** blocks its already-built, already-
    tested table content from ever reaching Silver — see "Archetype-7 table
    content" above. `GSTIN_REGISTER`'s twin gap was closed this session (a
    mislabel, not a missing fact); `KMP_LIST`'s is genuine (no date at all,
    only a fiscal-year label) and needs a real product decision (accept a
    different as-of-date source? relax the bitemporal key?), not a code fix.
19. **`RELATED_PARTY_REGISTER`'s table content** is the one archetype-7
    table-shaped type with no extractor at all (sixteenth session) — its
    last two columns are open-ended free prose with no delimiter, and the
    flattened-PDF-text approach `tablevalue.py` uses cannot reliably
    reconstruct row boundaries there. A PDF-layout-aware approach (real
    column/cell boundaries, e.g. `pdfplumber`'s table extraction, not
    `pypdf`'s flattened text) is the likely unblock, not a better regex.

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
