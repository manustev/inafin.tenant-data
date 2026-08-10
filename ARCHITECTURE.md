# INAFIN Tenant Data Layer — Architecture Proposal

**Status:** Proposal for review · v0.3 · 2026-08-03
**Scope:** Category C (Tenant Operational Data), and the tenant-side ~130 of the 166 documents
in the Source Document Register (Doc 4 §2).
**Baseline:** greenfield. Bronze, Silver, the trigger, and the isolation layer are all to be built.

**Changed in v0.3 — the isolation model.** v0.1/v0.2 used shared-schema tenancy with `tenant_id`
on every table and row-level security. This is now **schema-per-tenant**, migrating to
**database-per-tenant** as tenants justify it. `tenant_id` is *not* carried as a column on tenant
tables. The isolation mechanism is `GRANT`, not a row predicate — see §5. RLS no longer appears in
this design.

**Changed in v0.2 — the read path.** v0.1 had v2 reading tenant data through a service API, which
would make reconciliation depend on the ingestion service being up. v2 now reads its layer directly
with its own role, so the two pipelines share a store without sharing a runtime dependency.

---

## 1. The question, answered

> *Should platform data and tenant data be built as separate layers, given strict tenant isolation?*

**Separate them — but along the isolation boundary, not the A–F category boundary.**

The requirement doc's six categories are a good *governance* taxonomy and a poor *deployment* one.
The line that matters is "does this hold tenant data," which gives three data planes:

| Plane | Categories | Isolation obligation |
|---|---|---|
| **Platform** | A, B, D2, F | Shared, read-mostly, versioned. No tenant data. |
| **Tenant** | **C1–C5** | Hard isolation, per-tenant blast radius. |
| **Intelligence** | E | Physically separate cluster, consent-gated, no join path. |

And **two independently deployable pipelines** over the tenant plane:

```
   ┌──────────── PIPELINE 1 : inafin-tenant-data (NEW) ─────────────┐
   │                                                                 │
   │  ERP / uploads / portal APIs / third parties                    │
   │            │                                                    │
   │            ▼                                                    │
   │      ┌──────────┐   validate: types,        ┌──────────┐        │
   │      │  BRONZE  │──▶ mandatory fields,  ───▶│  SILVER  │        │
   │      │ immutable│   cleanse, normalise      │ Postgres │        │
   │      └──────────┘                           └────┬─────┘        │
   │      bucket/tenant                    t_<slug>_silver           │
   └──────────────────────────────────────────────────┼──────────────┘
                                                      │
                        ┌─────────────────────────────┴──────────┐
                        │                                        │
                  Kafka │ batch_ready                   read      │  (t_<slug>_recon,
                    (the doorbell)                    v1_ views   │   SELECT only)
                        │                                        │
   ┌────────────────────▼────────────────────────────────────────▼──┐
   │            PIPELINE 2 : inafinplatform/v2                       │
   │  micro-batch consume → recon rules → GOLD (facts, results)      │
   └─────────────────────────────────────────────────────────────────┘
```

Both pipelines connect to the same cluster with **different roles and non-overlapping grants**.
Neither can do the other's job, and neither needs the other running.

---

## 2. The handoff contract — the part that decides whether they are really independent

Get this wrong and you have two services that are decoupled on a diagram and coupled in production.

### 2.1 Kafka is the doorbell. Silver is the queue of record.

The trap: if Kafka is the only record of *what needs processing*, the two pipelines are coupled by
Kafka's retention window. Pipeline 2 down longer than retention = silent data loss. A rule change
requiring a re-run over FY2023–24 = impossible, because that topic expired 18 months ago.

That last case is not an edge case here. Your rule catalog and tenant packs are already
effective-dated (`domain/rule_catalog.py`, `domain/tenant_pack.py`), and the corpus is bitemporal by
design. **Re-running reconciliation under a changed rule or a corrected corpus is a first-class
operation.** An event-only integration cannot support it.

So:

- **Silver holds a batch table** — the durable, queryable statement of what exists and what is ready.
  This is the work queue.
- **Kafka carries a small manifest** — it exists only to cut latency from "poll interval" to
  "milliseconds." It is an optimisation.
- **Pipeline 2 must produce identical results whether woken by Kafka or found by polling.** That
  equivalence is a test (§10), not an aspiration.

Consequences, all upside:

| | Because Kafka is a doorbell |
|---|---|
| Kafka retention loss | Harmless — the work is still in Silver |
| Kafka ordering guarantees | Non-load-bearing — Pipeline 2 orders by reading Silver |
| Kafka down entirely | Degrades to polling latency; nothing breaks |
| Tenant business data in Kafka | **None** — manifests only, so Kafka's isolation burden collapses |
| Backfill / replay | Ordinary path, not a special tool |
| Backpressure signal | `count(*) WHERE status='READY' AND ready_at < now()-15min` — better than consumer lag |

### 2.2 The batch manifest

```sql
-- t_<slug>_silver.ingest_batch — written by Pipeline 1, read-only to Pipeline 2.
CREATE TABLE t_acme_silver.ingest_batch (
  batch_id            uuid PRIMARY KEY,
  entity_id           uuid NOT NULL,              -- business scope, NOT the isolation boundary
  document_type       text NOT NULL REFERENCES platform_ref.universal_master(value),
  source_stream       text NOT NULL,              -- A | B | C  (§7)
  period_start        date NOT NULL,
  period_end          date NOT NULL,
  row_count           integer NOT NULL,
  content_hash        bytea  NOT NULL,            -- idempotency anchor
  bronze_manifest_ref text   NOT NULL,            -- Bronze objects that produced this batch
  status              text   NOT NULL,            -- READY | SUPERSEDED | QUARANTINED
  ready_at            timestamptz NOT NULL,       -- the cursor axis
  ingest_run_id       uuid NOT NULL
);
CREATE INDEX ON t_acme_silver.ingest_batch (ready_at) WHERE status = 'READY';
```

No `tenant_id` column — the schema *is* the tenant. The Kafka message carries the tenant slug in its
envelope (so a consumer knows which schema to open) plus these fields: ids, counts, hashes, a period.
No line items, no GSTINs, no amounts.

### 2.3 How Pipeline 2 finds work (and never misses any)

```sql
SELECT * FROM t_acme_silver.v1_ingest_batch
WHERE status = 'READY'
  AND ready_at > $watermark - interval '1 hour'   -- deliberate overlap, see below
ORDER BY ready_at
LIMIT $micro_batch_size;
```

**The overlap window is not sloppiness.** A naive `ready_at > watermark` cursor silently skips rows:
a transaction that takes its timestamp early but commits late becomes visible *after* the watermark
has passed its `ready_at`. This is the classic high-watermark skip. Re-read a safety window and rely
on idempotent dedupe.

**Dedupe is not keyed on `batch_id` alone.** Re-running after a rule change is a legitimate new
execution, not a duplicate:

```sql
-- t_<slug>_gold.batch_execution — owned by Pipeline 2. Silver stays read-only to it.
CREATE TABLE t_acme_gold.batch_execution (
  batch_id             uuid NOT NULL,
  rule_catalog_version text NOT NULL,
  corpus_version       text NOT NULL,
  tenant_pack_version  text NOT NULL,
  executed_at          timestamptz NOT NULL,
  PRIMARY KEY (batch_id, rule_catalog_version, corpus_version, tenant_pack_version)
);
```

Backfill after a rule change is then just: run with the new version tuple. The key makes retries safe
and makes "which corpus version produced this finding" answerable — the same question a CA gets asked
in a hearing. This composes directly with the effective-dating in `rule_catalog.py` and
`tenant_pack.py`.

Pipeline 2 keeps claim state in its **own** schema. `SELECT ... FOR UPDATE SKIP LOCKED` on the Silver
batch table is deliberately *not* the mechanism — that needs write access and puts the pipelines back
in each other's way.

### 2.4 The schema contract

Pipeline 2 never touches Silver base tables. It reads **versioned views**, in the tenant's Silver
schema, prefixed `v1_`:

```sql
CREATE VIEW t_acme_silver.v1_purchase_invoice AS SELECT ... FROM t_acme_silver.purchase_invoice;
GRANT SELECT ON t_acme_silver.v1_purchase_invoice TO t_acme_recon;   -- base table NOT granted
```

Pipeline 1 refactors base tables freely; the view is the contract. A breaking change publishes `v2_`
alongside `v1_`, both live, migrate, retire. Contract tests run in both repos against the view
definition (§10).

**Do NOT set `security_invoker = true` on these views.** v0.3 originally carried it over from the
RLS design as "hygiene"; building it proved that is wrong, and the polarity is reversed under
schema-per-tenant. The views are **definer-rights** (the Postgres default): they execute as their
owner, `tenant_migrate`, which does hold SELECT on the base tables. That is exactly what lets
`recon` be granted SELECT on the *view* while holding no privilege on any base table. With
`security_invoker = true` the view would execute as `recon`, `recon` would need SELECT on the base
tables, and the `v1_` contract would collapse into "recon can read all of Silver".
`scripts/check_isolation.py` fails the build if the option ever appears.

---

## 3. Medallion layers for tenant data

| Layer | Content | Store | Written by | Read by |
|---|---|---|---|---|
| **Bronze** | Byte-exact received artefact. Never parsed, never edited. | Object store, **bucket per tenant** + ledger row | Pipeline 1 | Evidence retrieval, replay |
| **Silver** | Validated, cleansed, normalised | `t_<slug>_silver` | Pipeline 1 | Pipeline 2 (via `v1_` views) |
| **Gold** | Facts, contest positions, results, artefacts | `t_<slug>_gold` | Pipeline 2 | HITL, API, reporting |

Gold sits in the same cluster as Silver, in a sibling schema. Gold facts join to Silver rows
constantly (HITL display, audit trail, "show me the invoice behind this finding"), and cross-cluster
joins are what this design exists to avoid.

### 3.1 Bronze

**One uniform rule: every ingest produces exactly one Bronze object plus one ledger row —
regardless of whether it arrived as a PDF upload or a GSTN API response.**

- **Bytes → object store**, top-level bucket per tenant:
  `s3://inafin-tenant-<slug>/bronze/<yyyy>/<mm>/<dd>/<ingest_id>.<ext>`, with **Object Lock in
  compliance mode**. Compliance-mode WORM cannot be overridden by anyone, including root. An
  append-only Postgres table can always be undone by a superuser — so for material that may be
  tendered as evidence, object lock is a genuinely stronger guarantee.
- **Metadata → `t_<slug>_bronze.artefact_ledger`**: `ingest_id, entity_id,
  declared_document_type, source_stream, received_at, content_hash, object_key, size_bytes,
  status (RECEIVED|PARSED|PROMOTED|QUARANTINED|REJECTED), promoted_batch_id, error_detail`.
- **Retention:** align object-lock retention with **CGST Section 36 — 72 months from the due date of
  furnishing the annual return** for the year concerned. Set the lock; don't rely on a lifecycle
  policy you might misconfigure.

Not split by source type (files to object store, API responses to JSONB) because that creates two
code paths and, worse, **two evidentiary standards**. A parsed row is not evidence of what GSTN
returned; the signed raw envelope is. Doc 1 says the ingestion record is itself a submitted legal
artefact in Forensic Mode — that must hold for portal responses too.

---

## 4. Store topology

| Store | Plane | Holds | Isolation mechanism |
|---|---|---|---|
| `platform-db` (PG 16) | Platform | Cat B universal master + Pattern-2 tables (**authoring source**), Cat F, tenant registry | None needed — no tenant data |
| `ops-db` (PG + TimescaleDB) | Platform | Cat D2 event log, agent traces, HITL queue | Append-only grants |
| **`tenant-db` (PG 16)** | **Tenant** | `t_<slug>_{bronze,silver,gold}` × N, + `platform_ref`, + `app` | **Schema + role per tenant (§5)** |
| `intel-db` (PG 16) | Intelligence | Cat E aggregates | Physically separate cluster, no shared credentials |
| Weaviate | Platform | Cat A chunks | Corpus only; no tenant data, ever |
| Neo4j #1 | Platform | Amendment/supersession graph | — |
| Neo4j #2 | Tenant | Entity/ITC-flow graph | Database-per-tenant — deferred (§5.9) |
| MinIO Mumbai | Tenant | **Bronze objects**, hybrid/blob doc types | **Bucket per tenant** + STS + per-tenant SSE key + Object Lock |
| Kafka | Transit | **Batch manifests only** | Shared topic, partitioned by tenant (§5.10) |
| Redis | Transit | Ephemeral agent state | Per-tenant key prefix; never source of truth |

### The Category-B co-location decision

The requirement doc states two things that pull against each other: *"every categorical field is a
foreign key to the universal master table"*, and *Category E is a physically separate instance*. You
cannot FK across Postgres clusters — so either Cat B co-locates with Cat C, or the "FK" is
application-layer fiction, which is what the doc says to avoid.

**Resolution:** `platform-db` is the *authoring* source for Category B. `tenant-db` carries a
**`platform_ref` schema kept in sync by logical replication**, `SELECT`-only to tenant roles. Real FK
constraints from `t_<slug>_silver.*` → `platform_ref.universal_master` hold at the engine layer with
no network hop.

Schema-per-tenant makes this cleaner than the shared-schema design did: one `platform_ref` serves
every tenant schema in the cluster, and when a tenant graduates to its own database it gets its own
replica of the same schema.

---

## 5. Tenant isolation — the boundary is GRANT, not a predicate

**Decision:** schema-per-tenant now, database-per-tenant as tenants justify it. `tenant_id` is
**not** a column on tenant tables.

The reasoning: a `tenant_id` predicate is the discriminator for *shared-schema* tenancy. Once the
schema is the boundary, the column does no isolation work — it is noise on ~40 tables. And a
permission error is a better failure than an empty result set: loud, attributable, and impossible to
swallow with a forgotten `WHERE`.

This also makes the stated roadmap cheap. **Schema-per-tenant → database-per-tenant is
`pg_dump -n 't_acme_*'` and restore.** Shared-schema + RLS → database-per-tenant is a row-level
extraction and re-key of every table. The stepping stone was chosen correctly.

### 5.1 Layout

Three schemas and two roles per tenant, preserving the layer privilege separation:

```
t_acme_bronze    t_acme_silver    t_acme_gold
      ▲                ▲ ▲             ▲
      │                │ │             │
   t_acme_ingest ──────┘ │             │      DML on bronze + silver
   t_acme_recon ─────────┘─────────────┘      SELECT on silver v1_ views, DML on gold
```

| Role | `t_X_bronze` | `t_X_silver` base | `t_X_silver` `v1_` views | `t_X_gold` | `platform_ref` |
|---|---|---|---|---|---|
| `t_X_ingest` | DML | DML | — | **none** | SELECT |
| `t_X_recon` | **none** | **none** | SELECT | DML | SELECT |
| `t_X_support` | SELECT | — | SELECT | SELECT | SELECT |
| `tenant_migrate` | DDL owner | DDL owner | DDL owner | DDL owner | DDL owner |

`t_X_recon` holds no `USAGE` on any other tenant's schema at all, so a misrouted query is
`ERROR: permission denied for schema t_globex_silver` — not a silent read.

### 5.2 Pool-safe role assumption

Per-tenant *login* roles would shard your connection pool N ways, because PgBouncer pools by
(user, database). At 50 tenants × 20 connections that is 1000 server connections. This is the
pressure that pushes teams to a single shared role with `search_path` discipline — at which point
schema-per-tenant provides no isolation at all, only renamed tables.

The way out:

```sql
CREATE ROLE app_login LOGIN PASSWORD '...' NOINHERIT NOBYPASSRLS;  -- owns nothing, inherits nothing
GRANT t_acme_ingest, t_acme_recon TO app_login;                    -- membership only
```

`NOINHERIT` is the load-bearing word: `app_login` has **no privileges at all** until it explicitly
assumes a tenant role. Then, per transaction:

```sql
BEGIN;
  SET LOCAL ROLE t_acme_recon;                      -- transaction-scoped; reverts at COMMIT
  SELECT app.assert_schema_owner('t_acme_silver', 'acme');
  SELECT ... FROM t_acme_silver.v1_purchase_invoice WHERE ...;   -- fully qualified
COMMIT;
```

One shared pool, per-tenant privileges, fail-closed between transactions. Session-scoped `SET ROLE`
(without `LOCAL`) is banned — CI grep, §10.

**Honest weakness:** `app_login` can assume any tenant role, so its credential is as sensitive as the
whole cluster, and a code bug selecting the wrong role leaks. That is the price of a shared pool.
Mitigation fits the roadmap: high-value tenants graduate to their own database with a real per-tenant
credential and no `SET ROLE` at all.

**Prepared statements must be disabled on this pool.** psycopg3 auto-prepares a statement after
`prepare_threshold` executions, on whichever server backend it happened to be holding. PgBouncer
routes the next transaction to a different backend, which has never heard of it:

```
psycopg.errors.InvalidSqlStatementName: prepared statement "_pg3_0" does not exist
```

It surfaces only under load — the first few executions of any query work — which makes it a
production-only failure. `prepare_threshold=None` is set in `src/core/pool.py` and asserted by
`scripts/check_static.py`. (PgBouncer >= 1.21 can track prepared statements via
`max_prepared_statements`; not relied on, because correctness should not depend on a pooler setting
the application cannot see.)

### 5.3 The schema self-identity guard

Grants stop a *deliberate* wrong-schema query. They do not stop a **provisioning mistake** — if
`t_acme_recon` was accidentally granted `USAGE` on `t_globex_silver`, the grant layer is silent.

So each schema declares who it belongs to, at provisioning time:

```sql
CREATE TABLE t_acme_silver.__schema_identity (
  tenant_slug text PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  layer       text NOT NULL,
  provisioned_at timestamptz NOT NULL
);
```

and every transaction opens with:

```sql
SELECT app.assert_schema_owner('t_acme_silver', 'acme');  -- raises unless the row says 'acme'
```

This is **non-circular**, which is the whole point. If the schema name and the role both derive from
one tenant value in code, asserting that value against itself proves nothing. The identity row was
written independently, at provisioning — so if the code is pointed at globex's schema while intending
acme, globex's row says globex and the transaction aborts *before any data is read*.

One row per schema. Not a column on forty tables.

Cost is one extra statement per transaction. For a micro-batch covering 5,000 rows this is free; for
high-QPS point reads, pipeline it in the same round trip as the first query.

### 5.4 Fully-qualified names — the hazard that actually bites

**Never let `search_path` carry tenant meaning.** psycopg3 auto-prepares statements after
`prepare_threshold` executions, keyed on query *text*. If the SQL says
`SELECT ... FROM purchase_invoice` and the tenant is resolved by `search_path`, the plan binds to
whichever schema resolved **at prepare time**. The next transaction, with a different `search_path`,
reuses that plan and reads **the wrong tenant's table** — no error, correct-looking rows, wrong
tenant.

This is the most dangerous failure mode of schema-per-tenant with a shared pool, and it defeats
grants (the role may legitimately hold both, mid-provisioning or in a support session).

Rules:
- All generated SQL is **fully qualified**: `t_acme_silver.purchase_invoice`.
- Schema name is derived from the tenant value, through one identifier-safe formatter, in one place.
- `search_path` is set to `''` on the connection, so an unqualified reference fails loudly rather
  than resolving to something.
- §10.5 tests this directly.

### 5.5 What dropping `tenant_id` gives up

Stated honestly, so the trade is visible:

- **Self-describing rows.** A CSV extracted from a Silver table and detached from its schema has no
  tenant provenance. In a compliance platform where rows can become evidence, that matters. Mitigated
  by: exports go through an export service that stamps tenant + schema + extraction time in a
  manifest, never a raw `COPY`.
- **Post-hoc verification of a restore.** With a column, "assert every row is tenant X" is a query.
  Without, a schema restored under the wrong name is undetectable from the data alone. Mitigated by
  `__schema_identity` — it travels *with* the dump, so a misnamed restore is caught by
  `assert_schema_owner` on first use.
- **Cross-tenant aggregation (Category E)** needs the label added at read time. It fans out across
  schemas anyway (§5.11), so it labels by schema and this costs nothing.

None of these are worth a column on forty tables. But they are the reason `__schema_identity` exists
rather than nothing at all.

### 5.6 Identity

JWT carries the tenant as a signed claim (Keycloak). It is **never** a caller-supplied parameter —
always derived from the token; the gateway rejects any request whose body disagrees. For Pipeline 2,
a batch consumer with no user in the loop, the tenant comes from the Kafka manifest envelope and is
re-asserted by `assert_schema_owner` on every transaction.

### 5.7 Object store

**Top-level bucket per tenant**, region Mumbai: `inafin-tenant-<slug>`. Access via **STS AssumeRole**,
policy scoped to one bucket, TTL ≤ 15 min. No long-lived keys, and no key that can name two buckets.
**SSE with a per-tenant KMS key** (Vault Transit) — this is what makes crypto-shredding possible (§8).
Object key embeds the tenant slug redundantly, so a bucket-policy regression is still caught by a path
assertion.

### 5.8 Migration fan-out — the real operational cost

N schemas × every DDL change, each able to partially fail. This is the reason teams abandon
schema-per-tenant, and it needs tooling from day one, not from the day it hurts:

- **Two migration chains.** *Shared* (`app`, `platform_ref`, functions) runs once. *Tenant* is a
  template applied to every tenant schema with Alembic's `version_table_schema`, so each schema
  carries its own `alembic_version`.
- **A runner** that iterates the tenant registry with bounded concurrency, is resumable, retries one
  failed schema without blocking the rest, and provisions new tenants at head mid-release.
- **No shared catalogue writes inside the per-tenant path.** Concurrent workers each issuing
  `GRANT ... ON SCHEMA platform_ref` contend on one `pg_namespace` ACL row and Postgres raises
  `tuple concurrently updated`, failing an arbitrary subset of tenants. Shared reference access is
  granted once to a `platform_ref_reader` group role; tenant roles join the group at provisioning.
- **Drift detection** — a query answering "which schemas are not at head," alerting, not just
  logging. Silent drift is how one tenant ends up two migrations behind and nobody notices until a
  query fails in production.
- **Provisioning is code**, not a runbook: `app.provision_tenant(slug, tenant_id)` creates three
  schemas, two roles, all grants, the identity rows, and runs the tenant chain to head — atomically,
  or not at all.

### 5.9 Graph (Neo4j #2)

Neo4j has no grant model this fine-grained without Enterprise (database-per-tenant). **Deferred.**
At current scale, PAN-group traversal and vendor-customer networks are handled by Postgres recursive
CTEs inside the tenant schema. Do not ship a weak-isolation graph and describe it as isolated;
revisit when ITC-network analysis justifies the licence on its own merits.

### 5.10 Event bus

Because Kafka carries **manifests, not tenant business data**, its isolation burden collapses — which
turns topic strategy from a security decision into a scaling one. **Shared topic, partitioned by
tenant**, not topic-per-tenant: the latter is fine at tens of tenants and falls over at thousands,
since partition count is a cluster-wide limit. A mis-delivered manifest causes a redundant read
against a schema the consumer's role cannot open — caught by grants, then by
`assert_schema_owner`. Layered, as intended.

### 5.11 Cross-tenant reads that are legitimate

Category E aggregation must read every consenting tenant. Under shared-schema this was one query with
a bypass role; now it is a consent-gated fan-out:

- A dedicated `intel_reader` role, granted `SELECT` on specific `v1_` views **only in the schemas of
  tenants who have consented** — so consent is enforced by grant, not by a `WHERE` clause the batch
  job might forget.
- Revoking consent revokes a grant. That is auditable in `information_schema`, which is a materially
  better answer than a consent flag someone has to remember to check.

---

## 6. Tenant data model — collapsing 166 documents into 8 archetypes

The register lists 166 documents; ~130 are tenant-side. Modelling those as 130 tables is unbuildable.
Nearly all fall into **eight archetypes** with shared shape and lifecycle:

| # | Archetype | Covers | Storage | Approx. |
|---|---|---|---|---|
| 1 | **Transaction line records** | Sales/purchase invoices, credit & debit notes, e-invoices, e-way bills, BoE/SB | Hybrid | ~12 |
| 2 | **Periodic registers** | Creditor ageing, payment, RCM, ITC-reversal, inter-GSTIN, cost allocation, stock, FAR, advance receipt, unbilled revenue, forex payment, jobwork dispatch | Structured | ~15 |
| 3 | **Entitlement instruments** | LUT, IEC, SEZ LoA/LoP, EOU LoP, STPI reg, EPCG, AA, EODC, B-17, BLUT, AEO, SCOMET, incentive approvals, own AAR, stay orders, amnesty certs | Hybrid | ~30 |
| 4 | **Financial statements** | P&L, Balance Sheet, Trial Balance, Notes, Annual Report, 3CD, Cost Audit Report | Structured / Hybrid | ~7 |
| 5 | **Filed returns & portal state** | GSTR-1/2B/3B/9/9C/6/7/8, ledgers, refund status, registration status | Structured (Stream A) | ~15 |
| 6 | **Proceeding events** | ADT-01, SCN, SCN reply, DRC-01B/03, DAR, FAR, adjudication & hearing orders, MCM | Hybrid | ~12 |
| 7 | **Entity & counterparty master** | Org hierarchy, GSTIN register, customer/vendor master, GL master, bank master, related-party register, shareholding, director/DIN, SKU master | Structured | ~15 |
| 8 | **Narrative contracts** | Supply agreements, cost-sharing, service contracts, TP docs, APA, job-work agreements | Hybrid (blob + extracted terms) | ~12 |

**The unit of work is not 130.** It is ~3 ingestion mechanisms (upload/ERP connector, portal API
poller, third-party connector) × 8 normalisation archetypes, plus ~130 document-type *descriptors*
held as data in the Document Type Registry (Category B). Adding the 30th entitlement instrument
should be a row, not a sprint. If it becomes a sprint, the archetype abstraction has failed — stop
and fix it rather than absorbing the cost 100 more times.

### Archetype 3 is the highest-leverage one

Thirty-odd documents — LUT, LoA, EPCG, AA, EODC, B-17, AEO, the taxpayer's own AAR, stay orders,
incentive approvals — are the same object:

```
EntitlementInstrument
  instrument_id, entity_id
  instrument_type       → FK platform_ref.universal_master
  issuing_authority     → FK platform_ref.universal_master
  instrument_number
  valid_from, valid_to                  -- the query that matters
  scope       (JSONB)                   -- HSN codes / services / premises / ports covered
  obligation  (JSONB)                   -- NFE target, export obligation quantum, bond amount
  status                → ACTIVE | SUSPENDED | CANCELLED | DISCHARGED
  supersedes_instrument_id              -- renewal chains
  bronze_ref, integrity_hash
```

Every zero-rating, deemed-export and scheme check in A8/A10 reduces to one query:

> *Was an active instrument of type T, covering HSN H, held by entity E on date D?*

One table, one index, one retrieval contract — instead of thirty. This is also exactly the `D5.02`
two-part check the register calls out: **targeted notification in corpus (Cat A)** ∧ **entity's
entitlement instrument (Cat C)**. The corpus half already exists in `inafin-gst-corpus`; this table
is the missing half.

Archetype 2 collapses similarly: one `register_snapshot` header + `register_line` detail, with the
per-register column contract held as **data** in the Document Type Registry rather than 15 Python
schemas.

### Archetype 1, and the mechanism the rest will reuse

**Built 2026-08-04** (tenant migration 007). Eleven document types — sales, purchase and credit/debit
note registers, HO common input service invoices, GTA consignment notes, Bills of Entry (client and
ICEGATE), the IRN and e-way bill registers, shipping bills, SEZ Bills of Entry — share
`transaction_document` + `transaction_line`. It replaced `purchase_invoice`, which was a Phase 1
vertical slice; the `v1_` views were redefined over the new tables with identical column lists, so
Pipeline 2 saw nothing.

The part that generalises is **`document_type.field_contract`** — a string per type, parsed by
`src/silver/contract.py`:

```
direction=INWARD;counterparty=FOREIGN;doc=be_number:text!,be_date:date!,port_code:text!
```

`counterparty` is the instructive clause, because it is the one that cannot be a global rule.
`REQUIRED` for a purchase register; `OPTIONAL` for a sales register, where a B2C invoice has no
GSTIN and that absence is a *fact*; `FOREIGN` for an import Bill of Entry, where a GSTIN being
**present** is the error — treating an overseas supplier as registered would fabricate an ITC claim.
A single global rule gets two of those three wrong.

Nothing in the validation or promotion path names a document type. This is the same mechanism
Archetype 2 needs, proven on eleven types before it has to carry twenty-eight.

**What deliberately stayed out of the registry: ERP header aliasing.** A Tally purchase register and
a SAP one label the same field differently — but that varies by *client*, not by document type.
Putting it here would make the shared vocabulary carry one tenant's spreadsheet conventions. The
registry declares the canonical contract; mapping a client's headers onto it belongs to the
connector.

### The spine

```
legal_entity ─┬─ registration (GSTIN | IEC | LoA | STPI)
              ├─ financial_period
              └─ engagement (Forensic | Operational) ─ document_ledger ─ evidence_item
```

The tenant is the schema. `entity_id` / `gstin` is the **business scope** boundary *within* it — a PAN
group is one tenant with many entities, and cross-GSTIN ITC optimisation (A7) traverses entities
inside one schema, which is exactly where it should happen.

---

## 7. Ingestion — three streams, not two

The requirement doc has Stream A (portal-authoritative) and Stream B (client-authoritative). The
register introduces a third class the taxonomy has no home for: **Category D, External Third Party** —
Amazon MTR, Flipkart GTR, SSR/DRR/VRET, AD-bank FIRC, MCA21, NCLT orders.

The distinction has teeth: **when a marketplace MTR disagrees with the client's sales register, the
finding attributes to the operator, not the taxpayer.** Anomaly attribution and defence posture both
depend on it — so `source_stream` belongs on the batch manifest and on every Silver row.

| | Stream A | Stream B | **Stream C (new)** |
|---|---|---|---|
| Doc 4 category | B (35 types) | A (~74 types) | D (21 types) |
| Source | GSTN/ICEGATE/DGFT API | Client ERP/upload | Marketplace, AD bank, MCA, NCLT |
| Authority | Government record | Client's books | Third party's record |
| On mismatch | Client is wrong | Client is wrong | **Third party may be wrong** |
| Ingestion machine | **Pull** — scheduler, Vault credentials, rate limits, portal downtime, retry/backoff, ARN tracking | **Push** — upload/connector | Mixed — API + file drop |
| Bronze artefact | Signed raw API envelope | Uploaded file | Settlement file / API response |

Stream A is a materially different machine from Stream B — a scheduled poller with credential
management, not a file handler. Scoping it as "more document types" is the most likely way to
under-plan this build (§12).

---

## 8. Bitemporality, immutability, and the DPDP erasure problem

**Bitemporal throughout Silver and Gold:**
- `valid_from` / `valid_to` — when the fact was true in the world (invoice date, instrument validity).
- `recorded_at` / `superseded_at` — when the platform knew it. For Stream A this is the portal
  retrieval timestamp, which the doc correctly calls the authoritative moment of knowledge.

Every read is as-of both axes. No row is updated in place; corrections append a new version and close
the prior one. Amendments arrive as new batches marking prior batches `SUPERSEDED` — which is why
`status` is on the manifest rather than inferred.

**The tension nobody has resolved:** Category C is immutable and event-sourced; the DPDP Act grants
erasure rights.

**Answer: crypto-shredding.** PII columns and Bronze objects are encrypted under the per-tenant (and,
for personal data, per-subject) DEK. Erasure destroys the key, not the row. Hashes still chain, counts
still reconcile, the audit trail stays structurally intact — and the plaintext is unrecoverable. The
only approach satisfying both an immutable legal audit trail and a statutory erasure obligation, and
it must be designed in from row one.

---

## 9. Findings against the source documents

**1. The deduplication registry as specified is a cross-tenant leak.** The doc puts fingerprints as a
global primary key, checked before every C2 write. So if tenant B uploads a file byte-identical to one
tenant A holds — a common Excel template, a CBIC form, a shared marketplace report — B's write is
rejected as a duplicate, **B has learned something about A**, and B has lost their own document.
Schema-per-tenant resolves this structurally: the registry lives in each tenant's schema, so the
fingerprint namespace is per-tenant by construction. Do **not** re-centralise it.

**2. C1 discards evidence Forensic Mode needs.** The doc says Stream A has *"no blob store — no raw
file exists to store."* But Doc 1 says the ingestion record is itself a submitted legal artefact. A
parsed row is not evidence of what the portal returned. Resolved by the uniform Bronze rule (§3.1).

**3. Category D (External Third Party) has no home in the A–F taxonomy.** See §7. Marketplace
settlement reports are conditional-mandatory for every e-commerce client and currently have no
category, no storage decision, and no trust semantics.

**4. 20+ documents the register marks Mandatory have no storage decision in Doc 1 §3.** Concretely:
bank statements (A1.14/15 — the *authoritative* record for the 180-day rule), creditor ageing (A1.12),
related-party register (A2.04 — explicitly *"cannot be derived from transaction data"*), shareholding
pattern (A2.03), Ind AS 24 disclosures (A2.05), GSTIN register (A2.07), certificate of incorporation
(A2.01), **LUT (A5.01 — the instrument all export zero-rating rests on)**, DRC-03 voluntary payments
(B3.03), open SCN register (B3.01), director list/DIN (A3.01), SKU master with MRP flag (A1.17),
foreign-currency payment register (A1.23 — whose absence the register says makes *all* import-of-
services checks INCOMPLETE). The eight archetypes absorb these without adding eight tables, but the
storage decision table must name them or the completeness gate has nothing to check against.

**5. Doc 4's own totals do not reconcile.** The group rows in §2.1 sum to **167 total / 114
conditional**; the summary states **166 / 113**. Mandatory (53) is correct. Resolve before the T9
completeness gate is driven off these counts.

**6. "FK to universal master" + "physically separate Category E" cannot both hold across clusters.**
Resolved by the `platform_ref` replication design (§4). Record as an ADR.

**7. `TENANT_ID` as an env var (v2's current state) is not multi-tenancy.** It is single-tenant
deployment with a label.

---

## 10. Verifying isolation and the handoff (not optional)

**Isolation gates** — every test provisions **two** tenants and seeds colliding natural keys:

1. **Cross-tenant read.** As `t_acme_recon`, query `t_globex_silver.*` → permission denied. Plus a
   positive control: a privileged connection confirms globex's rows exist. Without that second
   assertion, a failed seed passes the test.
2. **No role assumed.** As `app_login` with no `SET LOCAL ROLE` → permission denied on everything.
   Proves `NOINHERIT` is doing its job.
3. **Role leakage across transactions.** Through PgBouncer, `default_pool_size = 1`, interleave acme
   and globex transactions so they *must* share one server connection. Assert the role reverts and
   does not bleed. **Only fails through the pooler** — running against Postgres directly proves nothing.
4. **Identity guard.** Point an acme-intended transaction at globex's schema with grants deliberately
   misconfigured to permit it; assert `assert_schema_owner` aborts before any row is read.
5. **Prepared-statement / `search_path` hazard.** Execute an unqualified query above
   `prepare_threshold` across alternating tenants; assert it errors (because `search_path = ''`)
   rather than silently resolving. This is §5.4 made mechanical.
6. **Privilege matrix.** Table-driven over §5.1: every role × schema × operation asserted allowed or
   denied. Notably `t_X_recon` cannot write Silver; `t_X_ingest` cannot read Gold.
7. **Structural gate.** Every tenant schema: has `__schema_identity` with exactly one row matching
   its name; grants match the matrix exactly; no unexpected role holds `USAGE`. No role has
   `BYPASSRLS`. Every `v1_` view granted without its base table.
8. **Mutation check.** Grant `t_acme_recon` `USAGE` on `t_globex_silver` inside a rolled-back
   transaction and assert test 1 **fails**. Repeat for `NOINHERIT` and for `__schema_identity`.

Test 8 is the one people skip. A conformance suite nobody has seen fail is indistinguishable from
`assert True` — and it is the entire justification for Phase 1.

**Handoff gates:**

9. **Doorbell equivalence.** Run a batch set with Kafka enabled, then again poll-only. Assert
   byte-identical Gold output. This keeps Kafka an optimisation.
10. **Retention-loss drill.** Purge the topic mid-run; assert every READY batch is still processed.
11. **Independence drill.** Stop Pipeline 1 for a full run of Pipeline 2, and vice versa. Assert no
    errors, no loss, correct catch-up on restart.
12. **Replay determinism.** Re-execute under an unchanged version tuple → no new Gold rows. Bump
    `rule_catalog_version` → a new, complete result set.

**Static gates:** raw SQL outside the storage adapter package · session-scoped `SET ` or `SET ROLE`
without `LOCAL` · unqualified table references in generated SQL · `BYPASSRLS` outside `bootstrap/` ·
`AsyncConnectionPool` imported outside the pool module.

Plus a quarterly red-team exercise: hand someone valid acme credentials and ask for a globex row.

---

## 11. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Schema naming: `t_<slug>_{bronze,silver,gold}` (3N) vs one schema with layer prefixes (N) | 3N — preserves the layer privilege split in §5.1, and `pg_dump -n 't_acme_*'` still works |
| 2 | When does a tenant graduate to its own database? | Define the trigger now (data volume, contractual isolation, or dedicated deployment) so it is a policy, not a negotiation |
| 3 | Does the HITL queue live in `ops-db` or the tenant schema? | `ops-db`, holding tenant-scoped references only — platform governance data |
| 4 | Is Forensic Mode a separate tenant or an engagement within a tenant? | Engagement within a tenant — forensic clients convert to operational, and you do not want to migrate their history |
| 5 | Micro-batch size and Pipeline 2 poll interval | Start 5k rows / 30s; both config, tuned on real volume |
| 6 | Neo4j Enterprise | Deferred (§5.9) — decide on ITC-network merits |

---

## 12. Build sequence

| Phase | Scope | Proves |
|---|---|---|
| **0** | ADRs for §9 findings; decisions in §11 closed | Signed off by design authority |
| **1** | **Walking skeleton** — isolation foundation + provisioning + migration runner, *plus* **one** document type (purchase invoice) end-to-end: Bronze object → validate → Silver → manifest → Kafka → v2 consumes → Gold | The boundary holds **and** the handoff contract works. §10.1–8 and §10.9–12 green together. |
| **2** | Stream B connector framework + Category A (~74 types) by archetype | Client ERP/upload ingestion at breadth; the archetype abstraction survives contact |
| **3** | Stream A portal pollers (Category B, 35 types) — scheduler, Vault credentials, rate limits, downtime handling, ARN tracking | A genuinely different ingestion machine (§7) |
| **4** | Stream C (Category D, 21 types) + remaining archetypes; Document Type Registry complete | Completeness ledger covers the full 25-item onboarding request |
| **5** | Crypto-shredding; Category E consent-gated aggregation fan-out | DPDP erasure; benchmarking |
| **6** | Neo4j #2, per decision 6 | ITC-network reconciliation |

Phase 1 is a thin end-to-end slice rather than a horizontal layer, deliberately. The handoff contract
(§2) is the highest-risk unknown in this design; proving it with one document type is far cheaper
than discovering it is wrong after 74 ingestion adapters are built on top of it.

---

## Appendix — impact on `inafinplatform/v2`

| Item | Change |
|---|---|
| `src/domain/ports.py` | **Unchanged.** Every method already takes `tenant_id`. |
| `src/adapters/storage/postgres.py` | Becomes the **Gold** writer, against `t_<slug>_gold`. Silver reads land in a sibling adapter against `v1_` views. |
| `src/core/container.py` | `tenant_id` moves from a settings field to a per-batch `TenantContext` that resolves to a schema name and a role. The one genuinely invasive change, confined to the container and worker entrypoint. |
| `src/workers/` | New micro-batch consumer: Kafka doorbell + Silver poll, sharing one code path (§10.9). |
| `src/application/ingestion/chain_of_custody.py` | **Moves to Pipeline 1** — chain of custody is a property of the data layer. |
| `src/application/ingestion/mode_router.py` | **Moves to Pipeline 1** — Forensic/Operational differ at ingestion, not in the engine. |
| `src/application/ingestion/completeness_gate.py` | **Splits.** Evidence ledger (*what exists* — a fact) → Pipeline 1. Gate policy (*is it sufficient* — a judgement, T9) → stays. |
| `TENANT_ID` env var | **Deleted.** Its continued existence is what makes multi-tenancy impossible today. |
| `src/mockdata/` | Reseeded — tenant ids become slugs resolving to real provisioned schemas. |

This lands lightly because of the existing hexagonal design: `tenant_id` was already threaded through
every port method, so the reconciliation core never learns that its data source changed.
