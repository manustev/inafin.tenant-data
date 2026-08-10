# Typed tables per document type — the redesign

**Status: agreed 2026-08-06. Supersedes ARCHITECTURE.md §6 (archetypes) for
`STRUCTURED` document types.** Nothing here is built yet.

This document exists because ARCHITECTURE.md, `CLAUDE.md` and `registry/README.md`
currently instruct the next session to widen the archetype design. They are the
design of record and they are now wrong on this axis. Read this first.

---

## 1. What was rejected, and why

The archetype design collapsed many document types onto one table pair, with
whatever differed per type held in an `attributes jsonb` bag described by
`platform_ref.document_type.field_contract`. Archetype 1 put 11 types in
`transaction_document` + `transaction_line`; archetype 3 put 33 in
`entitlement_instrument`.

It was rejected on three grounds, in ascending order of weight:

1. **`jsonb` gives up what Postgres is for.** No CHECK constraints, no domains,
   no typed indexes, no generated columns, no planner statistics. `invoice_type`
   and `place_of_supply` on a sales register are fixed, known columns; they were
   living as untyped strings in a JSON blob.

2. **`field_contract` reimplemented DDL, worse.** A mini-grammar in a CSV cell
   with a hand-written parser (`src/silver/contract.py`), sitting on top of a
   database that already has a schema language with far stronger guarantees.

3. **Decisive: per-tenant schema variation.** A tenant needs to carry additional
   columns on a register. That is impossible on a table shared by 11 or 33
   document types across every tenant. Table-per-type plus schema-per-tenant
   makes it a local change.

The archetype's stated promise — "adding the twelfth type is a registry row, not
a sprint" — was never actually delivered: `TODO.md` already concedes that each
type needs its own extraction adapter regardless. The DDL was never the
expensive part, so the saving was on the cheap axis and the cost was on the
expensive one.

---

## 2. Isolation is unchanged

`client_id` is the **customer's customer** — a client of the tenant. It is
business data, not a tenant discriminator.

All existing isolation invariants stand untouched: schema-per-tenant, `GRANT` as
the boundary, no `tenant_id` column, no RLS, `app_login` NOINHERIT holding
nothing until `SET LOCAL ROLE`, `search_path = ''` cluster-wide. This redesign
is entirely inside one tenant's Silver schema.

---

## 3. Where the tables live

The reference schema uses `meta` (catalog) and `ingest` (typed landing tables).
Mapped onto this repo's isolation model:

| Reference | Here | Why |
|---|---|---|
| `meta.*` | `platform_ref.*` — **one shared copy** | Already exists, already holds the 125-type registry with obligation, mode, stream, cadence. A second catalog would be two answers to one question. |
| `ingest.*` | `t_<slug>_silver.*` — **one per tenant** | Per-tenant typed tables are the whole point. A shared `ingest` schema would put every tenant's rows in one table and make `client_id` load-bearing for isolation, which is exactly what invariant 2 forbids. |

`platform_ref.document_type` gains a `table_name` column (code → table), which is
the one genuinely new piece of catalog data table-per-type requires.

The reference domains (`meta.gstin`, `meta.money_inr`, `meta.qty`,
`meta.tax_rate`) land in `platform_ref` and are shared. `meta.gstin`'s full
structural regex is adopted — it is stricter and more correct than the current
`^[0-9]{2}[A-Z0-9]{13}$`.

---

## 4. The row envelope

Every typed table carries the same envelope. Three parts, each settling a
different question.

```sql
-- identity and scope
id             bigint generated always as identity primary key,
client_id      bigint      not null,          -- customer's customer
gstin          platform_ref.gstin not null,
tax_period     date        not null,          -- or fy text, for FY-level types

-- lineage: row -> batch -> artefact -> object store
doc_type_code  text        not null references platform_ref.document_type(code),
batch_id       uuid        not null references {{silver}}.ingest_batch(batch_id),
bronze_ingest_id uuid      not null,

-- content identity
row_hash       text        not null,

-- bitemporal
valid_from     date        not null,
valid_to       date        not null default date '9999-12-31',
recorded_at    timestamptz not null default now(),
superseded_at  timestamptz,

-- audit
created_at, created_by, modified_at, modified_by
```

**Lineage is explicit, not inferred.** `bronze_ingest_id` is carried on the row
itself rather than reached through `batch_id`, so a row can be traced to the
artefact under Object Lock in one hop, and the chain survives if batch bookkeeping
is ever reorganised.

**`row_hash` is content identity, and it is what closes the resubmission gap.**

---

## 5. Resubmission: solved at the row, not the batch

`TODO.md` §"Resubmission produces duplicate batches for the same period" is
**closed by this design**, and its proposed resolution table is withdrawn.

The gap existed only because idempotency was at the batch level: a weekly
full-file export produced a new SHA-256, a new artefact, and a second `READY`
batch for the same period, with nothing to decide which one Pipeline 2 should
believe. That required superset comparison, a HITL queue, and a `PENDING_REVIEW`
state — none of which anybody had designed.

With row-level identity it is an upsert:

| Incoming row | Action |
|---|---|
| Natural key not present | Insert. `valid_from` = document date. |
| Natural key present, `row_hash` identical | No-op. |
| Natural key present, `row_hash` differs | Close the live row (`superseded_at = now()`, `valid_to`), insert the new version. |

A weekly full-file re-export of a growing register therefore no-ops on every row
it has already seen and inserts only what is new. No batch supersession, no
human in the loop, no `PENDING_REVIEW`.

`ingest_batch` remains the queue of record and the unit of work for Pipeline 2
(invariant 1 is untouched). It stops being the unit of *truth* about which rows
are current — that moves to the rows.

**The natural key differs per table, and that is now allowed.** A sales register
keys on `(client_id, gstin, invoice_no)`; a stock register keys on
`(client_id, gstin, tax_period, sku)`; a creditor ageing report on
`(client_id, gstin, as_at_date, supplier_gstin, invoice_no)`. Under the archetype
this variation had nowhere to live, which is what forced the batch-level
workaround. Per-type tables make it a per-table unique index:

```sql
create unique index ..._natural_key_uq on {{silver}}.sales_register
    (client_id, gstin, invoice_no) where superseded_at is null;
```

Partial on the live version, as tenant migration 007 already does. Note this
makes Silver not strictly INSERT-only — closing a superseded row is an UPDATE.
That is consistent with invariant 6: Bronze records fact and stays insert-only,
Silver records judgement.

---

## 6. Header and line are separate tables

The reference schema is flat — invoice header repeated on every line row. Header
and line are split back out.

The reason is not normalisation for its own sake. A flat table has nowhere to
record an ERP-supplied invoice total that **disagrees with the sum of its lines**,
so the disagreement is silently reconciled away at load. That disagreement is a
finding. A header table gives it somewhere to be recorded and detected.

- `sales_register` — one row per invoice, carries ERP-supplied totals.
- `sales_register_line` — one row per line, FK to the header.
- Header `row_hash` covers header fields; line `row_hash` covers line fields.
- Line natural key is `(header_id, line_number)`.
- Header totals are stored **as supplied**, never overwritten with the computed
  sum. Comparison against the line sum is Pipeline 2's job.

Types with no line structure (trial balance, creditor ageing, payment register)
get a single table. The split is applied where the document genuinely has one.

---

## 7. Per-tenant column extension

The mechanism behind the whole redesign, so it is specified rather than assumed.

**Recommended:** base tables stay templated and byte-identical across tenants
(`migrations/tenant/`), and tenant-specific columns arrive via a per-tenant
directory `migrations/tenant_ext/<slug>/`, applied only to that slug and recorded
in the same checksum-pinned `__migration_version` table.

`MigrationRunner.drift_report()` currently compares each tenant against one
expected list; it becomes expected = common chain + that slug's extension chain.
Drift detection keeps meaning what it means today.

The alternative — an `ext jsonb` column on every table for bespoke fields — is
rejected as written, because it reintroduces the untyped bag this redesign
exists to remove. It is worth revisiting only if extensions turn out to be
numerous and short-lived.

**Constraint that falls out of this:** `v1_` views expose the **common core
only**. Tenant extension columns live below the view line. Otherwise
`inafinplatform/v2` sees a different shape per tenant, and the view stops being a
contract. Definer-rights as always; never `security_invoker = true`.

---

## 8. One table per shape, and the shape is per registry type

Considered and **rejected**: collapsing `AMAZON_MTR`, `FLIPKART_GTR`,
`MARKETPLACE_SETTLEMENT_SSR`, `MARKETPLACE_DISBURSEMENT_DRR` and
`MARKETPLACE_RETURNS_VRET` into one table with a `report_type` discriminator.

They get separate tables. The reason is not column overlap — it is that these
apply to a minority of tenants (ecommerce sellers only), each operator's export
differs, and per-operator tables let a tenant who has none of them simply not
have the tables. A discriminator column would force one shared shape on five
operators whose formats move independently.

The general rule: **one table per registry document type.** Collapse only where
the shape is provably identical and the types always co-occur — and on current
evidence, nowhere in the 62 `STRUCTURED` types.

---

## 9. What happens to what is already built

| Built | Fate |
|---|---|
| `transaction_document` + `transaction_line` (tenant 007, 11 types) | Replaced by per-type tables. `A1.01`, `A1.02`, `A1.03`, `A1.24` are in the first slice. |
| `entitlement_instrument` (tenant 006, 33 HYBRID types) | **Open.** Not addressed by the reference schema. HYBRID = document + small field set, which is the weakest case against a shared table. Decide separately. |
| `platform_ref.document_type.field_contract` (shared 009) | Retired for types that get a typed table. Column stays — migrations are checksum-pinned and 009 is applied. |
| `src/silver/contract.py`, `validate.py`, `promote.py` | Rewritten. The contract grammar and its parser go away with the types they described. |
| `v1_purchase_invoice`, `v1_purchase_invoice_line` | Column lists preserved, redefined over `purchase_register`. v2 must not notice. |
| `ingest_batch`, `quarantined_artefact`, Bronze, Gold | Unchanged. |

Migrations are checksum-pinned, so none of the above are edited — reversal is new
migrations. There is no real tenant and no production data (all six deploy
blockers in `TODO.md` are open, including B5), so this is the cheapest this
rework will ever be.

---

## 10. Build order

1. ~~Shared migration: domains in `platform_ref`, `document_type.table_name`.~~
   **DONE 2026-08-06** — shared `010_typed_table_domains.sql`.
2. ~~`migrations/tenant_ext/` support + drift-report change.~~ **DONE** —
   `_load_ext` in `src/migrate/runner.py`, `migrations/tenant_ext/README.md`,
   7 gates in `tests/conformance/test_tenant_ext.py`.
3. ~~**`A1.01 SALES_REGISTER` end to end.**~~ **DONE, AWAITING REVIEW** — tenant
   `008_sales_register.sql`, shared `011_sales_register_catalog.sql`,
   `src/silver/sales_register.py`, three hand-written fixtures, 15 gates in
   `tests/conformance/test_sales_register.py`. **This is the reference pattern.
   Review it before replicating** — see "What to check first" below.
4. ~~`A1.03 PURCHASE_REGISTER` + `A1.02 CREDIT_DEBIT_NOTE_REGISTER`~~ **TABLES
   DONE** (tenant 010). Still open: retiring `transaction_document` and
   redefining `v1_purchase_invoice` / `v1_purchase_invoice_line` over
   `purchase_register`. That needs a HEADER table — `purchase_register` is
   line-grain and flat per the reference, and the two `v1_` views are
   header-grain — so it is a real design step, not a rename.
5. ~~Remaining A1 types~~ **TABLES DONE 2026-08-07** (tenant 010, shared 013).
   All 24 reference tables exist with the full envelope, natural or content keys,
   and `v1_` views. **Loaders do not.** Only A1.01 can accept a row; the other 23
   need a parser + upsert each, with `src/silver/sales_register.py` as the
   template. That is the next body of work and it is where the remaining effort
   actually is.
6. The other ~38 `STRUCTURED` types, registry group by registry group.

### Step 3 reconciled against the reference — 2026-08-07

`reference/inafin_a1_schema.sql` arrived after step 3 was built. Shared 012 and
tenant 009 reconcile it. **Column names now follow the reference** for all A1
tables (`qty`, `uom`, `cgst`, `sgst`, `igst`, `cess`, `total_value`, `hsn_sac`),
confirmed 2026-08-07.

Corrected against the reference:

- `Invoice_Type` is now `B2B, B2CL, B2CS, EXP, SEZWP, SEZWOP, DE`. 011's
  argument that an ERP supplies unsplit `B2C` was overruled — the customer wants
  the classification at ingestion. `EXPWP/EXPWOP/DEXP/NIL_RATED/EXEMPTED/NON_GST`
  were inventions and are gone.
- `tax_rate` is `numeric(6,3)`, not `(5,2)`. The narrower type silently ROUNDED a
  three-decimal rate — the worst failure mode available, since the row loads and
  the number is wrong.
- Added: `supply_type`, `trade_discount`, `freight`, `packing`, `insurance`,
  `irn`, `ewb_no`, and `total_tax` as a generated column.
- Removed: `customer_name`, `ecommerce_gstin` — my inventions, absent from the
  reference.

**`document_hash` is a correction to §5, and it stands.** §5 keys the upsert on
`row_hash`, but a corrected line under an unchanged header produces an identical
header hash, so a header-only hash no-ops the resubmission and the stale line
survives. The table carries both; the upsert compares `document_hash`.

### What our design keeps, because the reference has no substitute

The reference is a good column inventory and a weak record model. Do not let a
later reconciliation quietly drop any of these:

| Ours | What the reference has |
|---|---|
| `bronze_ingest_id` | **Nothing.** No Bronze linkage at all, so no path from a Silver row to the byte-exact artefact under Object Lock — the evidentiary chain. |
| `batch_id uuid` FK to `ingest_batch` | `batch_id bigint not null` with no FK and no table behind it. It points at nothing. |
| `ingest_run_id` on the batch | Nothing. Which pipeline execution produced this is unrecorded. |
| `valid_from/valid_to/recorded_at/superseded_at` | **Nothing.** A correction overwrites; what we believed when a return was filed is lost. Unusable for Forensic Mode. |
| natural key `(entity_id, gstin, invoice_no)` partial on live | `(client_id, gstin, tax_period, row_hash)` — dedups byte-identical rows only. A corrected invoice inserts a second row with nothing marking the first stale, and the same invoice in two months' files is two live rows. §5 exists because of this. |
| `entity_id` + schema-per-tenant GRANT | `client_id` as a discriminator column, which invariant 2 forbids. |
| header/line split | Flat, so an ERP header total that disagrees with its line sum has nowhere to live. |
| `created_by DEFAULT current_user` | An `app.current_user` GUC — supplied by the caller, so a claim rather than a fact, and session-scoped `set_config` is refused by `scripts/check_static.py`. |
| `doc_type_code = 'SALES_REGISTER'` | Register refs (`'A1.01'`). Registry codes were settled in shared 008; refs stay resolvable via `document_type_ref`. |

### The `invoice_` prefix in the ERP CSV contract

The reference is flat, so `taxable_value`/`cgst`/`total_value` are LINE values
there and it carries **no invoice-level totals at all**. A header/line split
needs both grains in one flat row, so the invoice-level claim is prefixed in the
CSV (`invoice_taxable_value`, …) and unprefixed on the tables, where the table
already says which grain you have.

Header totals are **nullable and not required**. NULL means the ERP made no
invoice-level claim — distinct from a claim of zero, and distinct again from a
claim that disagrees with the lines. Computing them from the lines would
manufacture a claim that was never made, after which every invoice agrees with
itself by construction and §6's whole point is lost.

### Two additional decisions made while building step 3

- **`entity_id uuid`, not the reference schema's `client_id bigint`** (§4). This
  repo's tenant is the client; `entity_id` is the legal entity within it, is
  what `ingest_batch` already carries, and is what Pipeline 2 already reads.
  Confirmed 2026-08-06.
- **`platform_ref.gstin` does not pin position 14 to `Z`** (§3). The published
  regex does, and it rejects TDS (`D`), TCS (`C`) and UIN registrations, all of
  which are valid counterparties. The domain enforces the embedded PAN instead —
  strictly stronger than the `^[0-9]{2}[A-Z0-9]{13}$` it replaces, without
  dropping valid rows at ingestion.

---

## 11. Open

- ~~**Per-tenant column extension** (§7).~~ **CONFIRMED and BUILT 2026-08-06**
  as recommended: `migrations/tenant_ext/<slug>/`, checksum-pinned in the same
  `__migration_version`, `drift_report()` now compares each slug against
  `common + that slug's chain`. The `ext jsonb` alternative stays rejected.
- **Archetype 3 / `entitlement_instrument`** — 33 HYBRID types. Not covered by
  the reference schema; needs its own decision (§9).
- **The 63 `HYBRID` types generally** — document plus a small extracted field
  set. Table-per-type may be overkill where the field set is three columns.
- ~~**Partitioning.**~~ **Decided 2026-08-06: do not partition**, and query
  indexes are a later per-table pass. Both are recorded in `TODO.md` §"Indexes on
  the typed tables". The partitioning reason is correctness, not volume: a unique
  index on a partitioned table must include the partition key, which would force
  `tax_period` into the sales register's natural key and stop it catching the same
  invoice number arriving in two periods' files.
- **A1.20 marketplace reports.** §8 rejected a `report_type` discriminator; the
  reference uses one, with per-operator field mapping called out as
  configuration. **Resolved 2026-08-07: follow the reference — one table.** §8's
  rejection was reasoning about a reference it had not seen. §8's text above is
  left in place as the record of what was decided and reversed.
- **`ingest_batch.row_count` counts lines, not documents**
  (`src/silver/validate.py:104`). With header/line split this needs to become
  two numbers or one clearly-named one. `SalesRegisterLoader` writes the LINE
  count deliberately, preserving the existing meaning rather than quietly
  redefining a column Pipeline 2 already reads — the open item is unchanged, not
  pre-empted. `UpsertOutcome` returns both numbers, so the caller is not stuck
  with the ambiguous one.
- **Cadence intervals** — `PERIODIC` says "on a schedule", not which one
  (`TODO.md`). Unaffected by this redesign, still open.
