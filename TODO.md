# TODO

Parked work, in priority order. Anything under **Deploy blockers** must close before
this repo serves a real tenant.

---

## Deploy blockers

Config divergences between the local dev stack and production. None are design
changes; all of them are load-bearing.

- [ ] **B1 — `app_login` credential to Vault.** Currently in `.env`. This role can
      `SET ROLE` to every tenant, so its credential is as sensitive as the whole
      cluster. Rotation path already exists (`bootstrap/00_cluster.sql` is
      idempotent and ALTERs an existing role's password).
- [ ] **B2 — `put_public_access_block` must be fatal on AWS S3.** It fails on MinIO
      and is currently logged as a WARNING (`src/provisioning/objectstore.py`). A
      publicly readable Bronze bucket is a breach, not a warning. Gate on
      `environment != "development"`.
- [ ] **B3 — PgBouncer auth: `plain` + `userlist.txt` → `scram-sha-256` +
      `auth_query`.** Today `app_login`'s password sits in cleartext in
      `docker/pgbouncer/userlist.txt`. See "PgBouncer auth" below.
- [ ] **B4 — raise `default_pool_size`.** `1` is deliberate for conformance gate
      10.3 (forces two tenants onto one backend). Keep it at 1 in the test stack;
      it must not ship to production.
- [ ] **B5 — verify Object Lock COMPLIANCE against real S3.** MinIO honours it, but
      AWS applies bucket-level defaults differently. Verify *before* the first real
      artefact lands — after that the retention is unmodifiable by design, including
      by root. That is the point, and it is also why a wrong setting is permanent.
- [ ] **B6 — `make migrate` + `scripts/check_isolation.py` run in the deploy
      pipeline**, not by hand. The grant matrix is re-asserted on every migrate;
      that is what makes drift detection mean anything.

### PgBouncer auth (detail for B3)

The runtime never connects to Postgres directly, so PgBouncer holds `app_login`'s
credential. Two problems with `auth_type = plain`:

1. The password is cleartext in a file on the pooler host. Read access to that
   container is equivalent to read access to every tenant's data.
2. It cannot be rotated without redeploying the pooler.

Target: `auth_type = scram-sha-256` with `auth_user` + `auth_query`, so PgBouncer
looks the verifier up from `pg_shadow` on demand and no credential file exists.

**Verify in staging before cutover.** PgBouncer's SCRAM support has a sharp edge
around using a stored verifier for the *server-side* login (a SCRAM secret verifies
a client but cannot by itself produce a login to the backend). SCRAM pass-through
is the intended path; confirm it works against your PG16 build rather than
discovering it at cutover. This is the one blocker with a real chance of
surprising us.

---

## Sign-off required (not mine to decide)

- [ ] **Alembic deviation for the tenant migration chain.** Flagged in
      `src/migrate/runner.py`. Tenant migrations are one DDL body applied N times
      with a schema substitution — a templating problem, not a version-graph one.
      The shared chain could move to Alembic without touching the tenant path.
- [ ] **`inafinplatform/v2` integration.** `src/reader/silver_reader.py` is the
      v2-facing library; it lands behind v2's existing `StoragePort`. Deliberately
      not done from this repo.
- [ ] **`tenant_migrate` holds `CREATEROLE`** (provisioning creates tenant roles).
      Scoped in PG16, but it is a real grant.

---

## Ingestion surface — what's built and what's still missing (updated 2026-08-14)

`src/api/` — REST upload/trigger/status + GraphQL reads, per the shape agreed
2026-08-07 (`HANDOFF-2026-08-07.md`). Upload, status, and the GraphQL reads
wrap real library calls unchanged (`BronzeIngestionService.receive`,
`SilverReader.artefact_outcome`, `EntitlementReader.find`).

- [x] **Bronze->Silver dispatcher — DONE, sixth session (2026-08-14).**
      `src/dispatch/router.py`'s `dispatch_load` reads
      `platform_ref.document_type.dispatch_mechanism` (shared migration
      `023`, generated from `registry/document_types.csv`'s new column) and
      routes to `RegisterLoader`/`SalesRegisterLoader`/
      `promote_transaction_documents`/`run_extraction`. `POST
      /artefacts/{id}/trigger` calls it synchronously, in-process, after
      recording `load_trigger` as before. See CLAUDE.md's "Current state
      (sixth session)" for the full design and the `entity_id`/`gstin`
      details `TriggerRequest` gained. The future entitlement path
      (`archetype == 3`) mentioned in the old version of this item is still
      unbuilt (`entitlement_instrument`, `TYPED-TABLES-PLAN.md` 11) — adding
      it needs one new `dispatch_mechanism` value and one new branch in
      `router.py`, same one-time cost every other mechanism already paid.
- [ ] **No real auth.** `src/api/auth.py`'s `StaticTokenAuth` is a static
      bearer-token -> slug map from `Settings.api_tenant_tokens` — a
      placeholder satisfying the `AuthPort` Protocol, not a security boundary.
      ARCHITECTURE.md 5.6 wants tenant identity from a signed Keycloak JWT
      claim, verified at "the gateway". No JWKS fetch, no signature check, no
      expiry, no revocation exists. Do not deploy this adapter anywhere
      internet-facing; swapping in a real one is a second `AuthPort`
      implementation, not a route-handler rewrite.

Still not built: a worker process or object-storage watcher that would
consume `load_trigger` rows (or watch Bronze directly) and call
`dispatch_load` independently of the API request path — not currently
blocking anything, since the trigger route now calls it directly; a worker
would be an alternate caller of the same function, not new logic. The
Bronze->Silver load itself still stays in-process psycopg by design
(`HANDOFF-2026-08-07.md` "Why the upsert must NOT go behind an API") — the
sixth session's synchronous in-process call from the trigger route does not
violate this; see `src/api/app.py`'s module docstring.

**Resolved, seventh session (2026-08-14)**: the `inafin-api` migration
collision found in the sixth session is settled — that chain is withdrawn
and rebuilt under this repo's own numbering (shared `024`-`031`, tenant
`021`-`025`). See CLAUDE.md's "Current state (seventh session)",
`API-CONTRACT.md`, and `HANDOFF-2026-08-14-session7.md`.

- [ ] **BLOCKING, next session's first item**: `onboarding_customer` and the
      other onboarding tables (`026`-`028`) were built from table names
      only, not `inafin-api`'s actual column shapes — `inafin-api`'s own
      hand-off spec now says the shapes are "materially different" from
      their write contract. Needs the real
      `migrations/platform/0002`-`0004` content from `inafin-api`, then a
      deliberate reconciliation migration. See
      `HANDOFF-2026-08-14-session7.md` §4.
- [ ] **This repo's Postgres is now a permanently shared dev server**
      (`inafin-api`/`inafin-portal` also point at it). `docker compose
      down -v` must never be run again — see `HANDOFF-2026-08-14-session7.md`
      §2 and CLAUDE.md's Workflow section.
- [ ] **`hsn_master`/`sac_master` are seeded empty** (shared migration `029`,
      seventh session). No real HSN/SAC code list exists anywhere in this
      workspace. DDL and grants are in place; populate from a real source
      when one is available. Nothing downstream depends on real rows yet —
      the onboarding picklist these support is `inafin-api`'s to build.

---

## Phase 0 — decisions with no owner yet

See ARCHITECTURE.md §9 (findings) and §11 (open decisions).

**Not a phase.** ADR-006 (storage decisions), ADR-007 (Category D) and ADR-013
(recount) are closed by `registry/` — the decisions are the CSV's columns. What
remains:

- [ ] **ADR-001 — per-tenant dedup registry.** Contradicts the requirements doc.
      Needs sign-off before more ingestion lands on the current design.
- [ ] **ADR-008 — Forensic Mode as an engagement.** DECIDED 2026-08-03: build
      Operational only; Forensic is deferred. See "Deferring Forensic Mode"
      below for what that costs and the one thing that must land now anyway.
- [ ] **ADR-002/003/004/005** — record decisions already built. Cheap.
- [ ] **ADR-009/010/011/012** — graduation trigger, HITL queue, micro-batch
      sizing (5k/30s, needed before v2 wiring), Neo4j.
- [ ] **Archetype 3 extraction adapters. BLOCKED on input availability
      2026-08-06 — not on priority.** The table, read contract and gates are
      built (tenant migration 006). What is NOT built is turning a LUT PDF into
      an `InstrumentRecord` — that is per-document-type extraction and belongs to
      the Stream A/B/C connectors. 33 types share the row; the extractor differs
      per type. Start with LUT (A5.01) and EPCG (A5.11): one unrestricted, one
      HSN-scoped, so between them they exercise both scope paths.

      **The blocker:** no LUT or EPCG sample exists anywhere in the workspace.
      A tier-1 parser written against a guessed layout is fiction that passes its
      own tests, and the fixture would encode the guess. Unblocked by two or
      three real (or redacted) documents. Note A5.01 is `source_system
      CLIENT_GSTN` — a portal-obtained LUT may arrive as ARN JSON, in which case
      its tier-1 adapter is not a PDF parser at all. Confirm the real input
      before writing one.

      **Design agreed 2026-08-06, so it is not re-derived:**

      - Tiered: deterministic parse first, LLM only on failure. Correct shape.
      - **The routing signal is a structured outcome, not a confidence score.**
        A field parser has no meaningful probability — it bound the required
        fields or it did not, and a float threshold is a magic number nobody can
        defend when an auditor asks why one document went each way. Three
        outcomes: `Extracted` (all required fields bound, all validations
        passed); `Partial(missing=[…], failed=[…])` — named fields, so the LLM
        tier is told what to look for rather than re-extracting everything;
        `NoTextLayer` — the scanned-PDF case, detectable *before* parsing and
        likely the largest real driver of fallback.
      - **The LLM tier never silently overwrites a deterministically-bound
        field.** Disagreement on `instrument_number` is a conflict for a human,
        not a vote the LLM wins — otherwise a hallucinated LUT ARN becomes the
        basis for zero-rating a year of exports. Provenance is per-field.
      - **Per-field provenance has nowhere to live yet.** `InstrumentRecord` has
        no such field and `entitlement_instrument` no such column. It is Silver,
        not Bronze — extraction method is a judgement, not a fact about the bytes
        (invariant 6). Prefer a separate `extraction_audit` table keyed by
        `bronze_ingest_id` over a column, because an attempt can produce *no*
        instrument row at all and that attempt still needs recording. New tenant
        migration; 006 is applied and pinned.
      - Escalation sink: reuse `_quarantine` in `src/silver/promote.py` rather
        than building a queue, so ADR-010 (HITL queue) stays open rather than
        being pre-empted by an implementation detail.
      - The LLM tier must be stubbed in CI. `make ci` runs offline.
- [x] **Artefact type vs row type. DECIDED 2026-08-04 — the registry carries
      artefact types only.** A row's character is `(doc_type, direction)` on the
      archetype table, not a second vocabulary: `PURCHASE_REGISTER` + `INWARD`
      says everything `PURCHASE_INVOICE` said, in the vocabulary the Source
      Document Register already uses. Forced by Archetype 1's composite FK —
      `transaction_document.doc_type` references `platform_ref.document_type`,
      so it must be a registry code. Recorded in shared migration 008.

      The 5 Phase 1 values are **retired from use, not deleted**: Bronze is
      insert-only and existing `artefact_ledger` rows reference them, so history
      must stay resolvable. `test_retired_row_type_is_refused` is the guard, and
      it is a test rather than a foreign key for exactly that reason.

      **Watch item.** `SilverReader.pending_batches` defaulted to
      `PURCHASE_INVOICE`; a stale default there returns an empty list rather
      than an error, so Pipeline 2 looks idle instead of broken. Any future
      document-type rename must grep for defaults, not just for FK targets.
- [ ] **Registry open questions** — `A5.06` stream, and whether `obligation`
      belongs on the engagement rather than the document type.

---

## Resubmission produces duplicate batches for the same period

**CLOSED 2026-08-06 by `TYPED-TABLES-PLAN.md` §5, and now DEMONSTRATED for
A1.01. The resolution table below is withdrawn — do not implement it.**

Proven rather than argued, in `tests/conformance/test_sales_register.py`: an
unchanged weekly re-export reports `inserted=0, unchanged=4, superseded=0`; a
grown file inserts only the new invoice; a corrected invoice closes the live row
and inserts a new version, with the superseded version keeping its own lines.

One correction to §5 fell out of building it. §5 keys the upsert on `row_hash`,
which covers the header — and a corrected LINE under an unchanged header
produces an identical header hash, so the resubmission would no-op and the stale
line would survive. The table carries `document_hash` (header hash plus every
line hash, in line order) and the upsert compares that.

The gap existed only because idempotency sat at the *batch* level, and it did so
only because the archetype tables had nowhere to put a per-type natural key.
Table-per-type gives each register its own key (`invoice_no` for a sales
register, `(tax_period, sku)` for a stock register), so resubmission becomes a
row-level upsert: insert / no-op on identical `row_hash` / supersede-and-insert
on changed. No batch supersession, no `PENDING_REVIEW`, no HITL queue.

Two claims below are also **factually wrong** and are left only so the error is
not repeated: not every archetype-2 type is `PERIODIC` (24 of 27 are; `A7.04`,
`B6.01`, `D2.01` are `CONTINUOUS`), and archetype 2 is not a coherent shape at
all. Verify registry claims against `registry/document_types.csv`.

The original text follows.

---

**Open gap. Blocks Operational onboarding, and blocks Archetype 2 — decide
before building it, not after.** Every one of the 27 archetype-2 types is
`PERIODIC`, so they are precisely the case below. Archetype 1 was unaffected
because its types are `CONTINUOUS` and keyed on the document; Archetype 2 keys
on the *period*, which is the same axis supersession turns on. Build the table
first and this arrives in 27 places at once, on the first mock file.

Overwrite is impossible — Bronze keys are `bronze/YYYY/MM/DD/{ingest_id}.{suffix}`
(date-partitioned and UUID-keyed), the bucket is versioned with Object Lock, and
COMPLIANCE retention runs 2190 days. Byte-identical resubmission is deduped and
idempotent. None of that is the problem.

The problem is the *near*-identical case. A weekly full-file export of the FY25
purchase register differs every week — more rows each time — so each week is a
new SHA-256, a new artefact, and on promotion **a new `ingest_batch` for the same
period**. There is no uniqueness on `(entity_id, document_type, period_start,
period_end)`, so two READY batches coexist and Pipeline 2 reconciles the same
invoices twice.

`ingest_batch.superseded_by` (self-FK) and `Batch_Status = SUPERSEDED` already
exist for exactly this, but nothing decides *when* supersession applies.

Proposed resolution — auto-resolve the unambiguous cases, escalate the rest:

| Situation | Action |
|---|---|
| Same scope, identical content | Dedup (already handled — no batch created) |
| Same scope, strict superset of rows | Auto-supersede the prior batch |
| Same scope, fewer rows | HITL — usually a truncated or failed export |
| Overlapping but non-identical periods | HITL |
| New period | Normal ingest |

**Split the flag from the queue.** Batch state (`PENDING_REVIEW`) belongs in
Silver because it is batch state. The queue and workflow belong in `ops-db` per
§11.3 — putting them in Silver would require the ops team to hold read access to
tenant Silver schemas, which puts a governance function across the isolation
boundary.

None of this touches Bronze. Bronze is INSERT-only as of shared migration 006;
the artefact is stored the moment it arrives, whatever we later conclude about
it. `PENDING_REVIEW` and `IGNORED` are `Batch_Status` values — universal_master
rows, data rather than schema. `pending_batches` filters `status = 'READY'`, so
an unreviewed batch is invisible to Pipeline 2 and cannot double-count while it
waits: fail-safe, not fail-open.

---

## Indexes on the typed tables — add later, per table

Deferred deliberately 2026-08-06. The typed tables (`TYPED-TABLES-PLAN.md`) ship
with only the indexes correctness requires — the partial natural-key unique index
(`WHERE superseded_at IS NULL`) and the FK/lineage columns. **Query indexes are a
separate pass, added per table once the real access patterns are known.** Writing
them now would be guessing at Pipeline 2's shape.

- [ ] Index pass over every typed table, once v2's query patterns are real.

### A natural key containing a NULLable column does not enforce itself — CLOSED 2026-08-10

Found 2026-08-07 while building the A1 loaders.

Three natural keys in tenant migration 010 include `supplier_gstin`, which is
nullable because an unregistered supplier is legitimate — `purchase_register`,
`creditor_ageing_report`, `common_input_service_invoice`. A unique index treats
two NULLs as **distinct**, so the partial unique index did not in fact forbid two
live rows for the same (entity, gstin, NULL, invoice_no). It only looked like it
did.

The loader was already correct: `RegisterLoader._upsert_natural` matches nullable
key columns with `IS NOT DISTINCT FROM`, so a resubmitted RCM purchase supersedes
instead of duplicating, and
`test_a_supplier_with_no_gstin_supersedes_instead_of_duplicating` fails if anyone
changes it back to `=`. What was missing was the *database* saying so.

**Closed by tenant migration `014_nulls_not_distinct.sql`** — `NULLS NOT
DISTINCT` added to all three indexes, so the constraint now means what the
column list implies. No duplicate sweep was needed: no production data exists
(all six deploy blockers below are still open). Gate:
`test_the_index_rejects_two_live_nulls_the_lookup_would_have_matched`
(`tests/conformance/test_registers.py`) inserts past the loader directly and
asserts the second NULL-supplier row is refused.

### Partitioning: decided NOT to partition

Recorded so it is not reopened as an oversight. There is no declarative
partitioning anywhere in the schema and there should not be.

The reason is correctness, not volume. **A unique index on a partitioned table
must include every partition key column** — verified against the PG16 dev
cluster:

```
ERROR:  unique constraint on partitioned table must include all partitioning columns
DETAIL:  UNIQUE constraint on table "t" lacks column "tax_period"
```

The sales register's natural key is `(client_id, gstin, invoice_no)` and
**excludes `tax_period` on purpose**: an invoice number must be unique for a
client regardless of which period's file carried it, so an `INV-4471` appearing
in both the April and May exports is caught as the ERP duplicate it is.
Partitioning by `tax_period` would force that column into the key, store both
copies as live rows, and report the invoice twice — with nothing failing loudly.

Volume does not argue the other way either: schema-per-tenant already splits the
data, so per-tenant volume is what counts, and ~3.6M line rows/year is
unremarkable for Postgres. The partial indexes keep the live working set well
below the total row count.

Revisit only if a single tenant table passes ~50M live rows, and treat the
natural-key constraint above as the thing to solve first rather than discover.

---

## Deferring Forensic Mode — what it costs

Decision: build Operational only. This records what that does and does not defer,
so the re-entry is a known quantity rather than a rediscovery.

### Genuinely free to defer

Only **8** in-scope document types are Forensic-only, **all 8 CONDITIONAL**:

| Ref | Type | Archetype |
|---|---|---|
| A2.02 | Memorandum & Articles of Association | 8 |
| A3.02 | Form DIR-12 | 7 |
| A7.02 | RBI Compounding Order | 6 |
| B1.08 | GSTR-2A (pre-2020 historical) | 5 |
| B3.04 | Amnesty / settlement order | 6 |
| B3.06 | Adjudication & hearing orders | 6 |
| D2.02 | BRC (pre-eBRC) | 2 |
| D4.03 | IBC resolution plan | 6 |

Seven of eight are HYBRID — a document plus a small extracted field set. They are
reactive evidence, not transaction data. All eight are already registry rows, so
enabling them later is **zero schema change**. Bronze is type-agnostic and already
holds everything under Object Lock at 72-month retention, so nothing about the
evidentiary path changes either.

Note also: **no new archetype is needed for them.** Archetype 6 (proceeding
events) gets built for Operational regardless — B3.01 (Open SCN register) is
MANDATORY and mode=BOTH.

### Engagement does NOT belong in Bronze or Silver

An earlier draft of this file argued for putting `engagement_id` on `ingest_batch`
and the archetype tables now, as cheap insurance. **That was wrong**, on the
customer's objection, and it is recorded here so it does not get re-proposed.

**Bronze is what arrived; engagement is why we are looking.** One FY25 purchase
register is one artefact. If a Forensic audit later covers FY25, that same
artefact is its evidence too. Stamping an engagement at intake forces either
duplicating the object or mislabelling it. The same reasoning carries into
Silver: `ingest_batch` records that rows arrived, and the archetype tables hold
facts about the entity valid over periods. An engagement selects entity + period
+ document scope — a WHERE clause, not a column.

In Operational mode the engagement is an app-layer declaration ("we began ADR
processing for this customer for FY25"). It never needs to reach the data layer.

The specific claim that the watermark would break was also wrong.
`consumer_watermark` is keyed `(consumer_name, document_type)`, and
`consumer_name` is already a `SilverReader` constructor parameter. A Forensic
replay passes its own name and gets an independent cursor with **no schema
change at all**.

### The only place engagement may eventually be needed: Gold

If a Forensic engagement and the Operational stream both run the same batch at
the same `(rule_catalog_version, corpus_version, tenant_pack_version)`, they
collide on `batch_execution`'s PK — and a Forensic report arguably needs
"engagement 42 concluded this" as an audit record in its own right.

Three tables in one schema, written by one consumer, no cross-repo contract on
the write side. Deferring it is cheap, and Gold is the consumer side — which is
where engagement belongs anyway.

**Contrast with `tenant_id`, which is a different question.** A tenant is an
*isolation boundary* enforced by GRANT; a column there would be a weaker
duplicate of a stronger control. An engagement is not a boundary at all — it is
a lens over data that exists independently of it. Neither belongs in a column,
for opposite reasons.

### Still deferred, and cheap to defer

Engagement lifecycle and state transitions; the Forensic T9 completeness gate
(all Mandatory present before any output, vs Operational's refresh intervals);
the HITL queue; `evidence_item` and Forensic retrieval/export.

### What Operational mode DOES need, and does not have

- [x] **`refresh_cadence` on the registry** — DONE 2026-08-03. Shared migration
      005. 45 ONE_TIME / 67 PERIODIC / 16 CONTINUOUS. See `registry/README.md`.
      The *interval* itself is still unset: PERIODIC says "on a schedule", not
      which one. That belongs to the app layer or to a later column, and needs
      a decision before Operational onboarding is built.
