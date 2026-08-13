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

## Non-negotiable invariants

Each of these was expensive to establish. Do not "fix" one without reading why.

1. **Kafka is a doorbell; `ingest_batch` is the queue of record.** Correctness
   must never depend on message delivery.
2. **Isolation is `GRANT`**, not a row predicate. No RLS, no `tenant_id` columns.
   `app_login` is NOINHERIT and holds nothing until `SET LOCAL ROLE`.
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
- Verify from a clean cluster (`docker compose down -v`) before claiming done.
- Prefer mutation-checking a new gate: break the thing, confirm exactly one test
  fails, restore. A suite nobody has seen fail is indistinguishable from
  `assert True`.

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

**`TYPED-TABLES-PLAN.md` §10 is fully built — all five steps done as of
2026-08-10.** Do not re-propose any part of it. The ingestion surface
(`src/api/`) is now built too, REST + GraphQL — read this session's summary
above before touching anything below; `HANDOFF-2026-08-10.md` is the first
session's detail only.

Priority order for what's actually open:

1. **v2 sign-off on the dropped `v1_purchase_invoice_line` view.** Step 4
   removed it because the reference schema has no line-level data for
   `PURCHASE_REGISTER`. If `inafinplatform/v2` reads that view, this is a
   breaking change nobody downstream has agreed to yet — a conversation, not
   a code task, and it should happen before v2 integration work resumes.
2. **The Bronze→Silver dispatcher.** `POST /artefacts/{id}/trigger` is a
   stub — it records intent (`load_trigger`, tenant migration `015`) and
   calls no loader. Nothing in the registry names which loader class handles
   a `doc_type_code` (only `table_name`, a register-family signal, and
   `archetype`, a structural-family signal). This is real, undone design
   work, tracked in `TODO.md` under "Ingestion surface — what's built and
   what's still missing".
3. **Real auth for the ingestion surface.** `src/api/auth.py`'s
   `StaticTokenAuth` is a placeholder (static bearer-token → slug map) —
   ARCHITECTURE.md 5.6 wants a signed Keycloak JWT claim, verified at "the
   gateway". A second `AuthPort` adapter, not a route-handler rewrite.
4. **`entitlement_instrument`** (tenant `006`, 33 HYBRID types) — the
   typed-tables verdict for A1 does not automatically carry; the reference
   schema is A1-only and never touched this table. Still open.
5. **The 63 HYBRID types generally** — table-per-type may be overkill where
   the field set is three columns. Still open.
6. **Excel adapter for the loader path.** CSV and NDJSON both work; Excel is
   unscoped and unstarted, not currently requested.
7. **The six deploy blockers in `TODO.md`** (B1–B6). None have moved.
8. **A live ClamAV container for local dev** — the virus-scan adapter
   (`src/bronze/scan.py`) is proven against a fake-clamd protocol double
   only; `docker-compose.yml` has no real scanner service. Not requested yet.
9. **A worker that actually consumes `load_trigger`** (or watches Bronze
   directly) and calls the dispatcher from item 2 once it exists. The
   Bronze→Silver load itself still stays in-process psycopg, never behind an
   API — `HANDOFF-2026-08-07.md`, "Why the upsert must NOT go behind an API".

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
the previous plan and it is withdrawn. Two reasons, both worth knowing:

- `PERIODIC` is a cadence, not a shape. Archetype 2's 22 `STRUCTURED` types
  include `AMAZON_MTR`, `CREDITOR_AGEING_REPORT`, `STOCK_REGISTER` and
  `PAYROLL_TDS_REGISTER`, which share no columns. It was cut on the wrong axis.
- The claim in the old handoff that "every archetype-2 type is `PERIODIC`" is
  **false**. Of the 27 in Operational scope, 24 are `PERIODIC` and 3 are
  `CONTINUOUS` (`A7.04 FORM_15CA_15CB`, `B6.01 DGFT_EBRC`, `D2.01 FIRC`). All
  three are individual certificates, not period registers, and look
  mis-archetyped. Verify registry claims against the CSV; that one was wrong.

### Extraction adapters — blocker lifted 2026-08-11, not yet built

Was deferred on input availability, not priority, since 2026-08-06: no LUT or
EPCG sample existed anywhere in the workspace, and a tier-1 parser written
against a guessed layout is fiction that passes its own tests. **That input
now exists** — `reference/A1-A7Documents/` holds 50 sample PDFs (A2.01–A7.05),
placed by Steve 2026-08-11. All 50 have native text layers (confirmed via
`pypdf`, `HANDOFF-2026-08-11.md`), so a tier-1 deterministic parser is
buildable against real fixtures rather than a guess. The asymmetry argument
below still applies one level deeper for OCR specifically: these are
Steve-generated specimens, not real DGFT/GST-portal originals, and none of
them lack a text layer — so a PaddleOCR fallback tier would still be built
against zero real fixtures if written now. See `HANDOFF-2026-08-11.md` for
the open design questions on that tier. **Not yet built** — this session was
inspection and design discussion only; start here next session rather than
re-establishing that the blocker is gone.

Original asymmetry argument, still the operating principle: structured ERP
data is the case where the export schema is a contract *we* specify, so a
synthetic fixture has real fidelity — mock an ERP CSV freely, never mock a
document layout with no ground truth. Design agreed 2026-08-06 for extraction
generally (tiered parse, structured outcomes, per-field provenance) is in
`TODO.md` and unchanged.

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
