# inafin-tenant-data

Pipeline 1 of the INAFIN tenant data layer: Bronze → Silver, schema-per-tenant
isolation, and the batch manifest that hands work to `inafinplatform/v2`.

Design: [`ARCHITECTURE.md`](ARCHITECTURE.md) · Phase plan: [`PHASE1-PLAN.md`](PHASE1-PLAN.md)

---

## The one thing to understand first

**Kafka is a doorbell. Silver's `ingest_batch` table is the queue of record.**

Pipeline 2 discovers work by querying Silver, not by consuming Kafka. A Kafka
message only makes it run sooner. Everything else follows: a purged topic loses
nothing, a broker outage costs latency, a consumer down for a week catches up,
and re-running reconciliation over FY2023–24 after a rule change is an ordinary
operation rather than an impossible one.

If you change one thing in this repo, do not make correctness depend on message
delivery. `tests/handoff/test_handoff.py::test_kafka_on_and_poll_only_produce_identical_gold`
exists to stop that.

---

## Quick start

```bash
cp .env.example .env
make up          # postgres + pgbouncer + minio + kafka
make migrate     # shared chain, then every tenant, then drift check
make provision SLUG=acme
make test        # 40 gates
make gates       # isolation + static checks
```

Ports are deliberately non-default (55432 / 56432 / 59000 / 59092) so this stack
never collides with the `inafinplatform/v2` stack on 5432.

---

## Invoking each layer: API, Bronze, Silver

Three ways to call this codebase, in the order data actually moves through it.
The API layer is the newest and thinnest — it wraps the other two unchanged,
so read Bronze and Silver first if something the API does looks surprising.

### API — `src/api/` (REST + GraphQL)

The only layer reachable over HTTP. Run it:

```bash
uvicorn src.api.app:app --reload
```

**Auth is a placeholder**, not a security boundary (`src/api/auth.py` —
`StaticTokenAuth`, a static bearer-token → tenant-slug map). Every request
needs `Authorization: Bearer <token>`, where the token is a key in
`Settings.api_tenant_tokens` (`.env`'s `API_TENANT_TOKENS`, a JSON object —
`.env.example` ships `{"dev-acme-token": "acme"}`). The tenant slug is never
a request parameter; it only ever comes from this resolution.

**Upload an artefact** — wraps `BronzeIngestionService.receive` for real
(file-check → hash → dedup → virus scan → PUT → ledger INSERT):

```bash
curl -X POST http://localhost:8000/artefacts \
  -H "Authorization: Bearer dev-acme-token" \
  -F "entity_id=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
  -F "document_type=BILL_OF_ENTRY" \
  -F "file=@export.csv;type=text/csv"
# {"ingest_id": "...", "bucket": "...", "object_key": "...", "size_bytes": ..., "deduplicated": false}
```

**Check "did my upload succeed"** — wraps `SilverReader.artefact_outcome`:

```bash
curl http://localhost:8000/artefacts/<ingest_id>/status \
  -H "Authorization: Bearer dev-acme-token"
# {"status": "PENDING" | "QUARANTINED" | "ACCEPTED" | "PARTIAL", "rejections": [...], ...}
```

**Trigger a load — STUB.** Records that a load was requested
(`{{bronze}}.load_trigger`, tenant migration 015); calls no loader. There is
still no `doc_type_code -> loader` dispatcher (`TODO.md`, "Ingestion surface —
what's built and what's still missing"), so `status` stays `PENDING`
afterward:

```bash
curl -X POST http://localhost:8000/artefacts/<ingest_id>/trigger \
  -H "Authorization: Bearer dev-acme-token" -H "Content-Type: application/json" \
  -d '{"doc_type_code": "BILL_OF_ENTRY"}'
# {"trigger_id": 1, "ingest_id": "...", "doc_type_code": "...", "status": "recorded"}
```

**Read** — GraphQL over `v1_` views, wrapping `SilverReader`/
`EntitlementReader` with no new read logic:

```bash
curl -X POST http://localhost:8000/graphql \
  -H "Authorization: Bearer dev-acme-token" -H "Content-Type: application/json" \
  -d '{"query": "query($id: UUID!) { artefactOutcome(bronzeIngestId: $id) { status rejectedCount } }", "variables": {"id": "<ingest_id>"}}'
```

A `/graphql` GET in a browser (with the header set via an extension, or via
any GraphQL client) opens GraphiQL for exploring the schema interactively.

### Bronze — `src/bronze/service.py` (library call, no API)

Every layer below API is a plain async library call — construct a
`TenantScopedPool` and a `TenantContext`, then call the service directly.
This is what the API layer itself does; nothing here is API-only.

```python
from src.core.config import get_settings
from src.core.pool import TenantScopedPool
from src.core.tenant import TenantContext
from src.bronze.service import BronzeIngestionService
from src.provisioning.objectstore import S3ObjectStore

settings = get_settings()
pool = TenantScopedPool(settings.pg_app_dsn)
await pool.open()

store = S3ObjectStore(
    endpoint_url=settings.s3_endpoint_url, region=settings.s3_region,
    access_key=settings.s3_access_key, secret_key=settings.s3_secret_key,
    bucket_prefix=settings.s3_bucket_prefix, retention_days=settings.bronze_retention_days,
)
bronze = BronzeIngestionService(pool, store)
ctx = TenantContext(slug="acme")

receipt = await bronze.receive(
    ctx, entity_id=entity_id, data=csv_bytes,
    document_type="BILL_OF_ENTRY", filename="export.csv",
)
# ArtefactReceipt(ingest_id=..., content_hash=..., bucket=..., object_key=..., deduplicated=False)
```

### Silver — one entry point per document family, all library calls

No single "load this artefact" function exists yet — that dispatcher is
open work (`TODO.md`). Pick the entry point for the document family:

**23 flat A1 registers** (`PURCHASE_REGISTER`, `CREDITOR_AGEING_REPORT`, …) —
`src/silver/registers/`, spec-driven:

```python
from src.silver.registers import RegisterLoader, spec_for

spec = spec_for("PURCHASE_REGISTER")
outcome = await RegisterLoader(pool, spec).load(
    ctx, entity_id=entity_id, gstin="27AAFCI9876P1ZQ",
    ingest_id=receipt.ingest_id, data=csv_bytes,
    period_start=date(2026, 4, 1), period_end=date(2026, 4, 30),
    content_format="csv",  # or "ndjson"
)
# RegisterOutcome(inserted=.., unchanged=.., superseded=.., rejected=.., ...)
```

**`SALES_REGISTER` (A1.01)** — the one type with a header/line split, its own
loader, CSV only:

```python
from src.silver.sales_register import SalesRegisterLoader

outcome = await SalesRegisterLoader(pool).load(
    ctx, entity_id=entity_id, gstin="27AAFCI9876P1ZQ",
    ingest_id=receipt.ingest_id, data=csv_bytes,
    period_start=date(2026, 4, 1), period_end=date(2026, 4, 30),
)
```

**The 10 remaining archetype-1 types** (Bills of Entry, GTA consignment
notes, e-way bills, …) — `src/silver/promote.py`, contract-driven, one table
pair (`transaction_document` + `transaction_line`) shared across all 10:

```python
from src.silver.promote import SilverPromotionService

manifest = await SilverPromotionService(pool).promote_transaction_documents(
    ctx, document_type="BILL_OF_ENTRY", ingest_id=receipt.ingest_id,
    entity_id=entity_id, data=csv_bytes,
    period_start=date(2026, 4, 1), period_end=date(2026, 4, 30),
)
```

**Archetype 3, `entitlement_instrument`** (33 HYBRID types — LUT, EPCG, IEC,
…) — `src/silver/entitlement.py`. Persistence only: the caller must already
have an extracted `InstrumentRecord` (turning a LUT PDF into one is
unbuilt extraction work, `TODO.md`):

```python
from src.silver.entitlement import EntitlementService, InstrumentRecord

instrument_id = await EntitlementService(pool).record(ctx, InstrumentRecord(
    entity_id=entity_id, instrument_type="LUT", issuing_authority="CGST",
    instrument_number="LUT/2026/001", valid_from=date(2026, 4, 1),
    batch_id=batch_id, bronze_ingest_id=receipt.ingest_id,
))
```

**Reading Silver** — `src/reader/silver_reader.py` (`SilverReader`) and
`src/reader/entitlement_reader.py` (`EntitlementReader`), both `Role.RECON`,
both what the API layer's GraphQL resolvers call. `inafinplatform/v2` imports
these directly rather than going through HTTP.

---

## Isolation model in sixty seconds

| | |
|---|---|
| Boundary | `GRANT`, not a row predicate. No RLS, no `tenant_id` columns. |
| Schemas | `t_<slug>_bronze`, `t_<slug>_silver`, `t_<slug>_gold` |
| Roles | `t_<slug>_ingest`, `t_<slug>_recon`, `t_<slug>_support` |
| Runtime login | one shared `app_login`, **NOINHERIT**, member of every tenant role |
| Assumption | `SET LOCAL ROLE` per transaction — reverts at COMMIT |
| Guard | `app.assert_tenant_context(slug, schema)` in the preamble |
| Object store | bucket per tenant, Object Lock COMPLIANCE, CGST s.36 retention |

`app_login` has **no privileges at all** until it assumes a tenant role. That is
what makes one shared PgBouncer pool safe, and it is why a code path that forgets
`SET LOCAL ROLE` reads nothing rather than everything.

## Where the schema actually lives

The migrations are the definition of record; everything below is generated from
them. To see the real thing in a running cluster:

```bash
# every schema
docker compose exec postgres psql -U postgres -d tenant_db -c '\dn'

# one tenant's tables and views
docker compose exec postgres psql -U postgres -d tenant_db -c '\dt t_acme_silver.*'
docker compose exec postgres psql -U postgres -d tenant_db -c '\dv t_acme_silver.*'

# one table in full — columns, constraints, indexes, comments
docker compose exec postgres psql -U postgres -d tenant_db -c '\d+ t_acme_silver.transaction_document'

# what the registry says about a document type
docker compose exec postgres psql -U postgres -d tenant_db -c \
  "SELECT doc_type_code, archetype, stream, field_contract
     FROM platform_ref.document_type WHERE archetype = 1;"

# which document types have a typed table yet, and what it is called
docker compose exec postgres psql -U postgres -d tenant_db -c \
  "SELECT doc_type_code, table_name FROM platform_ref.document_type
    WHERE table_name IS NOT NULL ORDER BY doc_type_code;"

# the shared domains every typed table draws on
docker compose exec postgres psql -U postgres -d tenant_db -c '\dD platform_ref.*'
```

**This section still describes the archetype tables.** `transaction_document`
and `entitlement_instrument` are being unwound for `STRUCTURED` types — see
`TYPED-TABLES-PLAN.md`. `sales_register` / `sales_register_line` (tenant
migration 008) is the first typed table and the pattern the rest follow.

### Conventions

Naming — each one is load-bearing somewhere, not decoration:

| Pattern | Meaning |
|---|---|
| `t_<slug>_bronze` / `_silver` / `_gold` | schema per tenant per layer; derived only in `src/core/identifiers.py`, which validates the slug first |
| `__name` | infrastructure, not tenant data — no tenant role gets more than SELECT, and none at all on `__migration_version` |
| `v1_name` | the read contract; the only Silver objects `recon` may touch, and the only ones safe to change under |
| `_uq` / `_idx` | unique index / non-unique index, both created explicitly so the name survives a rewrite |
| `<table>_<meaning>` | a named CHECK that encodes a rule (`transaction_document_totals`); column-level CHECKs keep Postgres' `<table>_<column>_check` |
| `{{bronze}}` `{{silver}}` `{{gold}}` | placeholders in `migrations/tenant/`, substituted per tenant by the runner |
| `NNN_name.sql` | migration filename; lexical order **is** apply order, and the body is checksum-pinned once applied |
| `ext_NNN_name.sql` | a **per-tenant** migration under `migrations/tenant_ext/<slug>/`; the prefix is enforced because both chains share one `__migration_version` keyed by filename |
| `<type>` / `<type>_line` | typed table per document type, header and line; the line table's name is derived by convention, not stored — `document_type.table_name` names the header only |

Columns:

| Pattern | Meaning |
|---|---|
| `<thing>_id uuid` | primary key with **no database default** — the application mints the id before the write, so it can be logged and correlated even if the transaction rolls back |
| `<col>_vt` | the vocabulary this column draws from, pinned to a constant by CHECK so the composite FK `(vt, value)` forces `platform_ref.universal_master` to be the only source of valid values |
| `<col>_archetype` | pinned to a constant so the composite FK makes the **registry** enforce archetype membership — a mis-typed row is a foreign key violation, not a review comment |
| `valid_from` / `valid_to` | **business** time — when the fact was true in the world (invoice date, instrument validity) |
| `recorded_at` / `superseded_at` | **system** time — when the platform knew it; nothing is updated in place, corrections append and close the prior row |
| `attributes` / `scope` / `obligation` (`jsonb`) | the axes that vary per document type, read after the indexed predicates have already cut the candidate set |
| `... WHERE superseded_at IS NULL` | partial index — a superseded row is never the answer to "what do we believe now", and excluding it keeps the index the size of the live set |
| `numeric(18,2)` / `(18,4)` / `(18,3)` / `(5,2)` | money / unit price / quantity / rate. Never float: money is exact or it is wrong |
| `text` | always, never `varchar(n)`. A length limit is a validation rule, and validation lives in `src/silver/`, where a rejection can be explained |

Two conventions worth stating as prohibitions, because both have been broken in
real systems and neither fails loudly:

- **No `tenant_id` column on any data table.** The schema is the tenant; a
  column would imply the boundary is a `WHERE` clause someone can forget. It
  appears exactly once per schema, on `__schema_identity` — where it is the
  thing `app.assert_schema_owner` checks *against*, not a filter. That is why no
  tenant role may UPDATE it: a role that could rewrite its own `tenant_id` would
  walk straight past the guard.

  ```bash
  # should return only __schema_identity rows
  docker compose exec postgres psql -U postgres -d tenant_db -At -c \
    "SELECT table_schema||'.'||table_name FROM information_schema.columns
      WHERE table_schema LIKE 't\_%' AND column_name = 'tenant_id';"
  ```
- **No DDL that records a later conclusion on a Bronze table.** Bronze records
  what arrived; Silver records what we made of it.

One known deviation: `transaction_line`'s `UNIQUE (doc_id, line_number)` is
inline, so Postgres named it `transaction_line_doc_id_line_number_key` rather
than `..._uq`. Cosmetic, and not worth a migration to rename — migrations are
checksum-pinned, so correcting it means a new file, not an edit.

### Shared schemas — one copy, cluster-wide

| Schema | Table | What it holds |
|---|---|---|
| `app` | `tenant_registry` | slug → tenant_id, status. Provisioning bookkeeping. |
| `app` | `shared_migration_version` | shared chain, checksum-pinned |
| `platform_ref` | `universal_master` | the vocabulary every `_vt` column FKs to |
| `platform_ref` | `document_type` | **the Document Type Registry** — 125 in-scope types, incl. `archetype`, `refresh_cadence`, `field_contract` |
| `platform_ref` | `document_type_ref` | Doc 4 register refs → document types (aliases, umbrellas) |

`platform_ref` is read through membership of `platform_ref_reader`, conferred
once at provisioning — never granted per tenant, because concurrent fan-out
would contend on the ACL row.

### Per-tenant schemas — one set per tenant, `t_<slug>_*`

| Schema | Table / view | Kind | What it holds |
|---|---|---|---|
| `t_<slug>_bronze` | `artefact_ledger` | table | one row per artefact received. **INSERT-only.** |
| `t_<slug>_bronze` | `__schema_identity` | table | anchors `assert_schema_owner`. Never writable by a tenant role. |
| `t_<slug>_silver` | `ingest_batch` | table | the batch manifest — the queue of record, not Kafka |
| `t_<slug>_silver` | `quarantined_artefact` | table | the verdict on a file that failed validation |
| `t_<slug>_silver` | **`transaction_document`** | table | **Archetype 1** header — 11 document types |
| `t_<slug>_silver` | **`transaction_line`** | table | Archetype 1 line items |
| `t_<slug>_silver` | **`entitlement_instrument`** | table | **Archetype 3** — 33 document types |
| `t_<slug>_silver` | `__migration_version` | table | tenant chain, checksum-pinned |
| `t_<slug>_silver` | `__schema_identity` | table | boundary guard anchor |
| `t_<slug>_gold` | `batch_execution` | table | one row per (batch, version tuple) |
| `t_<slug>_gold` | `consumer_watermark` | table | Pipeline 2's cursor, per document type |
| `t_<slug>_gold` | `fact_record` | table | findings, resolvable back to a Bronze object |

**The read contract** — the only Silver objects `recon` may touch. Definer
rights; never `security_invoker = true`.

| View | Over | Notes |
|---|---|---|
| `v1_ingest_batch` | `ingest_batch` | what Pipeline 2 polls |
| `v1_transaction_document` | `transaction_document` | the generalised Archetype 1 contract |
| `v1_transaction_line` | `transaction_line` | carries `doc_type` and `direction` |
| `v1_purchase_invoice` | `transaction_document` | **compatibility.** Phase 1 column names, filtered to `PURCHASE_REGISTER` |
| `v1_purchase_invoice_line` | `transaction_line` | compatibility, same filter |
| `v1_entitlement_instrument` | `entitlement_instrument` | Archetype 3 |

The two `v1_purchase_invoice*` views exist so `inafinplatform/v2` did not have to
change when migration 007 replaced the base table underneath them. They are a
migration aid, not a second contract — new work uses `v1_transaction_*`.

### Archetype coverage — what is built and what is not

125 in-scope document types collapse into eight archetypes (ARCHITECTURE.md §6).
Three tables cover 44 of them today.

| # | Archetype | Types | Silver table | Status |
|---|---|---|---|---|
| 1 | Transaction line records | 11 | `transaction_document` + `transaction_line` | **built** (tenant 007) |
| 2 | Periodic registers | 27 | `register_snapshot` + `register_line` | not built |
| 3 | Entitlement instruments | 33 | `entitlement_instrument` | **built** (tenant 006), no extraction adapters yet |
| 4 | Financial statements | 4 | — | not built |
| 5 | Filed returns & portal state | 16 | — | not built |
| 6 | Proceeding events | 9 | — | not built |
| 7 | Entity & counterparty master | 13 | — | not built |
| 8 | Narrative contracts | 12 | — | not built |

Counts are from the registry, not from a document — regenerate with:

```bash
docker compose exec postgres psql -U postgres -d tenant_db -c \
  "SELECT archetype, count(*) FROM platform_ref.document_type
    WHERE in_scope GROUP BY archetype ORDER BY archetype;"
```

**Adding a document type to a built archetype is a CSV row, not a migration.**
If it needs a schema change, the archetype abstraction has failed — stop and fix
that rather than absorbing the cost for the remaining types.

### Archetype 1 — one table pair, 11 document types

`{silver}.transaction_document` + `transaction_line` hold sales, purchase and
credit/debit note registers, HO common input service invoices, GTA consignment
notes, Bills of Entry (client and ICEGATE), the IRN and e-way bill registers,
shipping bills and SEZ Bills of Entry.

What differs per type is **data**, in `document_type.field_contract`:

```
direction=INWARD;counterparty=FOREIGN;doc=be_number:text!,be_date:date!,port_code:text!
```

Grammar and parser: `src/silver/contract.py`. `scripts/gen_registry_seed.py`
imports that parser, so a contract that would fail at ingestion cannot be
committed to the CSV.

`counterparty` is the clause that could not have been a global rule:

- `REQUIRED` — a purchase register without a supplier GSTIN is incomplete
- `OPTIONAL` — a B2C sale has none, and that absence is a **fact**, not a gap
- `FOREIGN` — an import Bill of Entry has no GSTIN to have, so one being
  *present* is the error. Treating an overseas supplier as registered would
  fabricate an ITC claim.

Two column choices that look arbitrary and are not:

1. **`direction` is stored, not joined** from the registry at read time. The row
   records what we understood the document to be *when we ingested it*, so a
   later contract correction cannot silently rewrite a released report.
2. **The natural key uses `COALESCE(counterparty_gstin, '')`.** NULLs are
   distinct in a unique index, so two B2C invoices sharing a number would both
   be accepted — surfacing later as doubled turnover.

### Archetype 3 — one table, 33 document types

`{silver}.entitlement_instrument` answers the only question A8/A10 ever asks:

> Was an active instrument of type T, covering HSN H, held by entity E on date D?

```python
await EntitlementReader(pool).is_entitled(
    ctx, entity_id=e, instrument_type="LUT", as_of=invoice_date, hsn="84713010"
)
```

Three things that look wrong and are not:

1. **HSN containment is inverted.** An instrument scoped to `8471` covers supply
   of `84713010`, but a `LIKE '8471%'` test cannot use an index. So the *caller*
   expands the queried code into its prefixes and the stored array is tested for
   overlap — which GIN answers directly.
2. **`scope_hsn IS NULL` means unrestricted**, not "covers nothing". A LUT covers
   all exports. Inverting this would deny every LUT-backed export in the platform.
3. **`status` is not derivable from `valid_to`.** An instrument inside its window
   can be SUSPENDED (rejected SEZ APR) or CANCELLED (breached EOU LoP). Expiry and
   standing are independent, and zero-rating needs both.

Adding the 34th instrument type must be a **registry row**, not a table. The
composite FK `(instrument_type, 3) -> document_type(doc_type_code, archetype)`
makes the registry enforce that: storing a GSTR-1 here is a foreign key
violation, not a review comment.

### Bronze is INSERT-only

`ingest` holds **SELECT and INSERT** on `artefact_ledger` — no UPDATE, no DELETE.
A ledger row is written when the bytes land and is never revised.

Bronze records **fact**: what arrived, byte-exact, when, from whom. Silver records
**judgement**: whether it parsed, what it superseded, whether a human must look.
Deciding any of those requires parsing, and Bronze holds no business content to
parse — so it cannot hold the answer either.

- promoted? → `ingest_batch.bronze_manifest_ref` points back at the artefact
- quarantined? → `{silver}.quarantined_artefact`

Do not add a column to `artefact_ledger` that records a conclusion reached later.
`check_isolation.py` fails on any write privilege there, and
`test_bronze_is_insert_only` proves the refusal at runtime.

### Three things that will break isolation if you undo them

1. **`prepare_threshold=None`** in `src/core/pool.py`. psycopg3 prepares
   statements on whichever backend it was using; PgBouncer routes the next
   transaction elsewhere and you get `prepared statement "_pg3_0" does not exist`.
   It only appears under load.
2. **`search_path = ''`** cluster-wide. With a populated search_path, a cached
   plan binds to whichever schema resolved at prepare time and a later
   transaction reads the *wrong tenant's table* — no error, correct-looking rows.
   All generated SQL is fully qualified for the same reason.
3. **Definer-rights `v1_` views.** Do **not** add `security_invoker = true`. The
   views execute as their owner, which is precisely what lets `recon` read them
   while holding no privilege on any Silver base table.

---

## Runbook

### Provision a tenant

```bash
make provision SLUG=acme
```

Creates the bucket first (S3 cannot join a Postgres transaction; a bucket without
schemas is inert, whereas schemas without a bucket would accept uploads that have
nowhere to land), then schemas + roles + identity rows in one transaction, then
migrations and grants, then flips the registry to `ACTIVE`.

A tenant left in `PROVISIONING` **must not be routed traffic** — its grants may
be incomplete. Re-run the same command; it is idempotent.

### Release a schema change

```bash
# 1. add migrations/tenant/00N_*.sql  (never edit an applied file — checksum-pinned)
make migrate                          # fan-out, bounded concurrency, resumable
python scripts/check_isolation.py     # MUST run post-migrate, every environment
```

`make migrate` re-asserts the grant matrix for every tenant even when no
migration ran. `app.apply_tenant_grants` REVOKEs before it GRANTs, so a privilege
added by hand is removed on the next run — that is what lets `check_isolation.py`
treat any deviation as a failure rather than a warning.

If one tenant fails, the others still complete. Fix and re-run; only the failed
schema is behind.

### Check for drift

```bash
python -m src.cli drift        # exit 1 if any tenant is not at head
```

Wire this to an alert, not a log. Silent drift is how one tenant ends up two
migrations behind and nobody notices until a query fails in production.

### Rotate credentials

`app_login` is the sensitive one — it can `SET ROLE` to any tenant, so its
credential is as sensitive as the whole cluster. Rotate via
`bootstrap/00_cluster.sql` (idempotent; it ALTERs an existing role's password),
then update the pooler's `userlist.txt` / auth source.

### Grant and revoke support access

Support access is a deliberate, logged act. `t_<slug>_support` is read-only and
already exists; the control is who may assume it. Do not grant a human `app_login`.

---

## Repo map

```
bootstrap/00_cluster.sql       roles, database, revokes, search_path=''  (superuser, once)
registry/document_types.csv    Doc 4 Section 2 as data — SOURCE OF TRUTH, reviewed
                               incl. field_contract, the per-type column contract
scripts/gen_registry_seed.py   CSV -> migrations/shared/004_, 005_, 009_ (commit all)
scripts/gen_mock_erp.py        synthetic ERP export for any archetype-1 type
tests/fixtures/                hand-written fixtures — read its README before editing
migrations/shared/             app schema, guards, grant matrix, platform_ref, registry
migrations/tenant/             templated per tenant: {{bronze}} {{silver}} {{gold}}
src/core/identifiers.py        the ONLY place a schema or role name is derived
src/core/pool.py               the ONLY way to get a connection
src/core/tenant.py             TenantContext — required, never ambient
src/migrate/runner.py          fan-out, checksum pinning, drift detection
src/provisioning/              provision_tenant + object store
src/bronze/service.py          intake: hash -> dedup -> PUT object -> ledger row (INSERT-only)
src/silver/contract.py         the field_contract grammar — one parser, two consumers
src/silver/validate.py         contract-driven cleansing; names no document type
src/silver/promote.py          Bronze -> Silver, any archetype-1 type, + batch manifest
src/silver/entitlement.py      Archetype 3 — 33 document types, one table
src/events/publisher.py        the doorbell (never raises)
src/reader/silver_reader.py    what v2 imports for batches
src/reader/entitlement_reader.py  what v2 imports for "is this entity entitled?"
scripts/check_isolation.py     structural gate — CI *and* post-migrate
scripts/check_static.py        source rules no runtime test can catch
```

---

## Test suite

```bash
make conformance   # isolation gates, ARCHITECTURE.md 10.1-10.8, + registry gates
make handoff       # handoff gates,  ARCHITECTURE.md 10.9-10.12
```

Conventions that make these gates mean something:

- **Colliding natural keys.** Both tenants hold the same invoice number, supplier
  GSTIN and content hash. A leak cannot hide behind naturally distinct data.
- **Positive controls.** Every isolation gate also asserts, via a privileged
  connection, that the rows it could not see genuinely exist. Otherwise a failed
  seed passes the test.
- **Through PgBouncer.** `default_pool_size = 1` in dev forces two tenants onto
  one backend. Gate 10.3 proves nothing against a direct connection.
- **Mutation checks** (`tests/conformance/test_mutation.py`) deliberately break
  the boundary and assert the suite notices — including one that removes the
  guard and proves the cross-tenant read then *succeeds*. A suite nobody has seen
  fail is indistinguishable from `assert True`.
