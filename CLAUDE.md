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
make ci                      # ruff + mypy + 149 tests + isolation + static gates
make migrate                 # shared chain, all tenants, drift check
make provision SLUG=acme
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

## Current state (2026-08-06)

Built: isolation foundation, Bronze→Silver, v2 handoff contract, Document Type
Registry (125 in-scope types), Bronze insert-only, **Archetype 3
`entitlement_instrument`** (33 types, one table), **Archetype 1
`transaction_document` + `transaction_line`** (11 types, one table pair).

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

**The archetype work above is being unwound for `STRUCTURED` types** — see
`TYPED-TABLES-PLAN.md` §9 for what survives, what is replaced, and what is still
open. No production data exists (all six deploy blockers open, including B5), so
reversal is new migrations against empty tenant schemas. Nothing is unwound yet:
`transaction_document` still holds the 11 archetype-1 types and
`v1_purchase_invoice` still reads from it. That happens at step 4.

`README.md` §"Where the schema actually lives" lists every schema, table and view
with the psql to re-derive it, plus the naming and column conventions — read that
instead of inferring conventions from whichever migration you happen to open
first. It still describes the archetype tables.

## Next session starts here

**Read `TYPED-TABLES-PLAN.md` first. Build order is its §10; steps 1–3 are done
and step 5 (the 23 flat loaders) is done.**

Step 4 is the remaining one: retire the archetype path for `PURCHASE_REGISTER` —
redefine `v1_purchase_invoice` over `purchase_register` (which now has a loader
and a spec) and stop writing `transaction_document`. Nothing is unwound yet.

After that, the open question is the ingestion *surface*: there is still no HTTP
API, no worker, and nothing that watches object storage — every loader is a
library a caller must invoke. Discussed 2026-08-07; the shape agreed in
principle is a control plane (upload, trigger, status) over HTTP with the
Bronze→Silver load staying in-process psycopg, because the upsert is a
transaction with `SET LOCAL ROLE` inside it and per-row HTTP cannot hold that.
Not built, not decided in detail.

The archetype design is overruled for `STRUCTURED` types (agreed 2026-08-06 with
the customer, against a reference schema they supplied). One typed table per
document type; typed columns and domains instead of an `attributes jsonb` bag
described by `field_contract`. The decisive argument was per-tenant schema
variation, which a table shared by 11 or 33 types cannot support.

**Do step 3's review before step 4.** `A1.01 SALES_REGISTER` is the pattern the
other 23 A1 types copy, so a wrong column there is a wrong column 24 times.

**The reference schema is `reference/inafin_a1_schema.sql`** (received
2026-08-07). It covers A1.01–A1.24 and is the column inventory for steps 4–5 —
read it before adding a table. Shared 012 + tenant 009 reconciled step 3 against
it; §10 of the plan records what changed and, more importantly, **what our
design keeps because the reference has no substitute** (Bronze lineage, a real
batch FK, bitemporality, the natural key, schema-per-tenant isolation).

Column names for all A1 tables follow the reference (`qty`, `uom`, `cgst`, …).

**Still open** (§11):

1. `entitlement_instrument` (tenant 006, 33 HYBRID types) — the reference schema
   is A1-only and does not touch it. The A1 verdict does not automatically carry.
2. The 63 HYBRID types generally — table-per-type may be overkill where the
   field set is three columns.

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

### Why extraction adapters are not next

Deferred on input availability, not on priority — decided 2026-08-06. There is
no LUT or EPCG sample anywhere in the workspace (only CBIC circulars and our own
design PDFs), and a tier-1 parser written against a guessed layout is fiction
that passes its own tests. Structured ERP data is the opposite case: the export
schema is a contract *we* specify, so a synthetic fixture has real fidelity.
That asymmetry is the whole argument — mock an ERP CSV, never mock a government
PDF's layout. Design agreed for when documents arrive is in `TODO.md`.

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
