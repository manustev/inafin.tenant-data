# Phase 1 — Walking Skeleton: Isolation Boundary + One Document Type End-to-End

**Companion to:** `ARCHITECTURE.md` v0.3 · **Status:** plan for review · v0.2 · 2026-08-03
**Baseline:** greenfield. Empty repo, no database, nothing to migrate.

**Changed in v0.2:** isolation moved from shared-schema + RLS to **schema-per-tenant + GRANT**
(no `tenant_id` columns). Scope widened from "isolation foundation only" to a **walking skeleton** —
isolation *plus* one document type flowing Bronze → Silver → Kafka → v2 → Gold.

---

## 1. Objective

Two things, proven together:

1. **The boundary holds.** Schema and role per tenant, pool-safe role assumption, the identity guard,
   and a conformance suite that proves cross-tenant reads are impossible.
2. **The contract works.** One document type — **purchase invoice** — travels the full path: Bronze
   object → validation → Silver → batch manifest → Kafka doorbell → v2 micro-batch consume → Gold.

Doing these together is the point. The handoff contract (`ARCHITECTURE.md` §2) is the highest-risk
unknown in the design; proving it against one document type costs days, and discovering it is wrong
after 74 ingestion adapters are built on it costs months. Equally, an isolation boundary tested only
against empty canary tables has never met a real read path.

### Exit criteria

**Isolation** (`ARCHITECTURE.md` §10.1–8)

1. Conformance suite green, running **through PgBouncer in transaction mode**.
2. Every test asserts a **positive control** — the other tenant's rows genuinely exist and are
   unreachable, not merely that a query returned empty.
3. `scripts/check_isolation.py` green, in CI *and* run post-migrate in every environment.
4. The mutation check (§10.8) demonstrably **fails** the suite when a grant is widened. A suite
   nobody has seen fail is measuring nothing.

**Handoff** (`ARCHITECTURE.md` §10.9–12)

5. One purchase invoice traced end-to-end, Bronze object key → Gold fact, with lineage at every hop.
6. Doorbell equivalence: Kafka-on and poll-only runs produce byte-identical Gold output.
7. Independence drill: each pipeline stopped for a full run of the other; no loss, correct catch-up.
8. Replay determinism: same version tuple → no new rows; bumped `rule_catalog_version` → full re-run.

**Operability**

9. `app.provision_tenant('acme', <uuid>)` creates schemas, roles, grants, identity rows, and migrates
   to head — atomically, or not at all.
10. Migration runner applies a DDL change across N schemas, resumably, with drift detection.

### Explicitly out of scope

Remaining ~129 document types (Phases 2–4) · Stream A pollers and Stream C connectors (Phases 3–4) ·
full Bronze format matrix (Phase 2) · crypto-shredding (Phase 5) · Neo4j #2 (Phase 6) · logical
replication of `platform_ref` (Phase 2 — seeded from a fixture here).

---

## 2. Decisions to close before writing code

| # | Decision | Recommendation | Consequence if deferred |
|---|---|---|---|
| **D1** | Schema naming: `t_<slug>_{bronze,silver,gold}` (3N) or one schema with layer prefixes (N) | **3N** — preserves the layer privilege split, and `pg_dump -n 't_acme_*'` still works for the DB-per-tenant move | Renaming schemas later means re-granting everything |
| **D2** | Tenant slug format and immutability | Lowercase `[a-z0-9_]{3,32}`, **immutable once provisioned** | It is in schema names, role names, bucket names, and Kafka keys. It cannot be renamed cheaply. |
| **D3** | v2 currently uses `tenant_id: str` = `"acme-corp"`. Does it become the slug? | Yes — `TenantContext` carries the slug and resolves schema + role from it. v2's `str` signature survives. `src/mockdata/` reseeded. | Phase 1's v2 leg blocks on it |
| **D4** | Postgres version | **16** | — |
| **D5** | Which document type for the skeleton | **Purchase invoice** — archetype 1, hybrid storage, line-item level, feeds B1/B2 recon which already exists in v2 | A simpler type would not exercise the line-item path, which is where the real shape lives |
| **D6** | Secrets for `app_login` and `tenant_migrate` | Env locally; Vault from staging. No superuser DSN in any app config. | — |

D1–D5 are blocking.

---

## 3. Work breakdown

### Track A — Isolation foundation

| # | Item | Depends on | Size |
|---|---|---|---|
| A1 | Repo skeleton, tooling, `docker-compose` (PG 16 + PgBouncer + MinIO + Kafka) | — | S |
| A2 | `bootstrap/00_cluster.sql` — `app_login`, `tenant_migrate`, database, revokes | A1 | S |
| A3 | Shared migration chain: `app` + `platform_ref` schemas, `assert_schema_owner()`, `provision_tenant()` | A2 | M |
| A4 | Tenant migration chain (template) + **migration runner** with per-schema versioning, resumability, drift detection | A3 | **L** |
| A5 | `TenantContext` + `TenantScopedPool` (`SET LOCAL ROLE`, fully-qualified SQL, `search_path=''`) | A3 | M |
| A6 | `platform_ref` fixture seed | A3 | S |
| A7 | **Conformance suite** §10.1–8 | A4, A5 | **L** |
| A8 | `scripts/check_isolation.py` + CI wiring | A4 | M |
| A9 | Static gates (banned-pattern lint) | A1 | S |

### Track B — Walking skeleton (purchase invoice)

| # | Item | Depends on | Size |
|---|---|---|---|
| B1 | Bronze: MinIO bucket-per-tenant provisioning, Object Lock, STS issuance | A3 | M |
| B2 | Bronze: `artefact_ledger` + chain-of-custody stamping (hash, source, received_at) | A4, B1 | M |
| B3 | Validation/normalisation for purchase invoice only — types, mandatory fields, cleanse | B2 | M |
| B4 | Silver: `purchase_invoice`, `ingest_batch`, `v1_` views + grants | A4 | M |
| B5 | Kafka manifest publisher | B4 | S |
| B6 | v2 leg: Silver-reader adapter behind existing `StoragePort`, `gold.batch_execution`, watermark + overlap + idempotent dedupe | B4, B5 | **L** |
| B7 | **Handoff gates** §10.9–12 | B6 | M |
| B8 | Runbook: provision a tenant, rotate credentials, grant/revoke support access, replay a batch | A4, B6 | S |

**Critical path:** A1→A2→A3→A4 gates everything. A4 (the migration runner) is the item most likely
to be underestimated — it is not "run Alembic in a loop," it is per-schema version tracking, bounded
concurrency, partial-failure recovery, and mid-release provisioning.

---

## 4. The build, concretely

### 4.1 Repo skeleton

Mirrors `inafinplatform/v2` conventions so the repos read the same way.

```
inafin-tenant-data/
├── ARCHITECTURE.md
├── PHASE1-PLAN.md
├── pyproject.toml
├── docker-compose.yml            # postgres:16, pgbouncer, minio, kafka
├── bootstrap/
│   └── 00_cluster.sql            # superuser, once per cluster
├── migrations/
│   ├── shared/                   # app, platform_ref — runs once
│   └── tenant/                   # template — runs per tenant schema
├── src/
│   ├── core/
│   │   ├── config.py
│   │   ├── tenant_context.py     # slug → schema names + role names, one formatter
│   │   ├── pool.py               # TenantScopedPool — the ONLY way to get a connection
│   │   └── provisioning.py
│   ├── bronze/                   # object store client, ledger, custody stamping
│   ├── silver/                   # validation, normalisation, batch publication
│   └── domain/
├── scripts/
│   ├── migrate.py                # the fan-out runner
│   └── check_isolation.py
└── tests/
    ├── conformance/              # §10.1–8
    ├── handoff/                  # §10.9–12
    └── fixtures/
```

### 4.2 Cluster bootstrap

```sql
-- bootstrap/00_cluster.sql — superuser, once. Never run by the app.
CREATE ROLE tenant_migrate LOGIN PASSWORD :'migrate_pw' NOBYPASSRLS;
CREATE ROLE app_login      LOGIN PASSWORD :'app_pw'     NOINHERIT NOBYPASSRLS;

CREATE DATABASE tenant_db OWNER tenant_migrate;
REVOKE ALL ON DATABASE tenant_db FROM PUBLIC;
GRANT CONNECT ON DATABASE tenant_db TO app_login;
\c tenant_db
DROP SCHEMA IF EXISTS public;          -- nothing lives in public, ever
ALTER ROLE app_login SET search_path = '';   -- unqualified references fail loudly
```

`NOINHERIT` on `app_login` is the load-bearing attribute: it has **no privileges at all** until it
assumes a tenant role. `search_path = ''` is the second: it makes the §5.4 prepared-statement hazard
impossible rather than merely discouraged.

### 4.3 Provisioning as code

```sql
-- app.provision_tenant(slug, tenant_id) — atomic, or nothing.
--   1. CREATE SCHEMA t_<slug>_{bronze,silver,gold} AUTHORIZATION tenant_migrate
--   2. CREATE ROLE   t_<slug>_{ingest,recon,support} NOLOGIN NOBYPASSRLS
--   3. GRANT tenant roles TO app_login          (membership only — NOINHERIT)
--   4. GRANT USAGE / table privileges per the §5.1 matrix
--   5. INSERT __schema_identity into each of the three schemas
--   6. run the tenant migration chain to head
```

Under schema-per-tenant, "provision a tenant" stops being an `INSERT` and becomes a first-class,
tested operation. Treat it as product code — it runs in production, it can fail halfway, and a
half-provisioned tenant is a security state, not just a broken one.

Bucket provisioning (`inafin-tenant-<slug>`, Object Lock enabled) happens in the same operation and
must be idempotent, since S3 bucket creation cannot join a Postgres transaction. Order matters:
create the bucket **first**, then the schemas — a bucket without schemas is inert, whereas schemas
without a bucket will accept ingests that have nowhere to land.

### 4.4 Connection discipline

```python
@dataclass(frozen=True, slots=True)
class TenantContext:
    slug: str                      # validated against D2's pattern at construction

    @property
    def silver_schema(self) -> str: return f"t_{self.slug}_silver"
    @property
    def recon_role(self) -> str:    return f"t_{self.slug}_recon"


class TenantScopedPool:
    """The only way to obtain a connection. The raw pool is never exported."""

    @asynccontextmanager
    async def transaction(
        self, ctx: TenantContext, role: Role
    ) -> AsyncIterator[AsyncConnection]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL ROLE {quote_ident(ctx.role_for(role))}")
                await conn.execute(
                    "SELECT app.assert_schema_owner(%s, %s)",
                    (ctx.silver_schema, ctx.slug),
                )
                yield conn
```

Rules the suite and linter enforce together:

- `ctx` is required and non-optional. No contextvar fallback, no module global, no default.
- Nothing outside `core/pool.py` touches `AsyncConnectionPool`.
- All generated SQL is **fully qualified** from `ctx`, through one identifier-safe formatter.
- `SET ROLE` without `LOCAL` is a banned pattern.
- A batch is single-tenant by construction. Never process a mixed-tenant batch.

### 4.5 PgBouncer

```ini
pool_mode = transaction
default_pool_size = 20          # 1 in the test environment — see §10.3
```

`SET LOCAL ROLE` is transaction-scoped, so a pooled server connection cannot carry one tenant's role
into another's transaction. Note that `server_reset_query` is **ignored in transaction mode** unless
`server_reset_query_always = 1` — it is not the mechanism protecting you here, and should not be
mistaken for one.

### 4.6 The skeleton path (Track B)

One purchase invoice, traced:

```
upload ──▶ hash, stamp custody
       ──▶ PUT s3://inafin-tenant-acme/bronze/2026/08/03/<ingest_id>.csv  (Object Lock)
       ──▶ INSERT t_acme_bronze.artefact_ledger  (status=RECEIVED)
       ──▶ validate: types, mandatory fields, cleanse
       ──▶ INSERT t_acme_silver.purchase_invoice (N rows)
       ──▶ INSERT t_acme_silver.ingest_batch     (status=READY, content_hash, ready_at)
       ──▶ UPDATE artefact_ledger                (status=PROMOTED, promoted_batch_id)
       ──▶ Kafka: {slug, batch_id, doc_type, period, row_count, hash}
                    │
v2  ◀───────────────┘  (or finds it by polling — same code path)
       ──▶ SET LOCAL ROLE t_acme_recon; assert_schema_owner
       ──▶ SELECT FROM t_acme_silver.v1_ingest_batch WHERE ready_at > wm - 1h
       ──▶ dedupe against t_acme_gold.batch_execution (batch_id + 3 versions)
       ──▶ recon rules
       ──▶ INSERT t_acme_gold.{fact_records, batch_execution}
```

Every hop records its predecessor's identifier. At the end, a Gold fact resolves back to a Bronze
object key in one query chain — which is the lineage a CA needs in a hearing, and the cheapest
possible time to prove it works.

---

## 5. Definition of done

- [ ] D1–D5 closed and recorded as ADRs
- [ ] `docker compose up` gives PG 16 + PgBouncer + MinIO + Kafka; shared chain migrates from empty
- [ ] `provision_tenant` creates two tenants (acme, globex) atomically, including buckets
- [ ] Migration runner applies a DDL change to both schemas; drift detection catches a skipped one
- [ ] `app_login` has no privileges without `SET LOCAL ROLE`; no role has `BYPASSRLS`
- [ ] Conformance suite §10.1–8 green **through PgBouncer**
- [ ] Mutation check demonstrated failing, in front of whoever signs this off
- [ ] One purchase invoice traced Bronze → Gold, lineage resolvable in one query chain
- [ ] Handoff gates §10.9–12 green
- [ ] `check_isolation.py` in CI and post-migrate in every environment
- [ ] Runbook complete

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| **Migration runner underestimated** (A4) | Scoped as L, on the critical path, with drift detection in the definition of done. It is not "Alembic in a loop." |
| **Half-provisioned tenant** — schemas exist, grants missing | `provision_tenant` is atomic; `check_isolation.py` treats a schema without matching identity rows and grants as a failure, not a warning |
| **`search_path` creeps back in** for convenience | `ALTER ROLE app_login SET search_path = ''` at bootstrap + §10.5 test + lint on unqualified references |
| **`app_login` credential is cluster-wide** | Accepted for pooling; mitigated by tiering high-value tenants to their own database (ARCHITECTURE §11 decision 2). Rotate on any suspicion. |
| **Suite green but toothless** | Mutation check (§10.8) in the definition of done |
| **Skeleton quietly widens** to "a few more document types" | D5 fixes it at one. Additional types are Phase 2, after the contract is proven. |
| **Phase 1 compressed because it ships one document type** | The exit criteria are binary and testable. Make the argument once, now, rather than after the first cross-tenant incident. |

---

## 7. Sequencing

A1→A2→A3 unblocks two tracks that then run in parallel: A4–A6 (migrations, pool, fixtures) and B1–B2
(Bronze). They converge on A7 and B4. A8/A9 can be built alongside by a second person.

**Write conformance tests §10.1 and §10.3 first, against two provisioned tenants, and let them fail.**
Grants written to satisfy a failing test come out better than grants written from a document — and
§10.3 (role leakage through the pooler) tends to reshape how people write the pool wrapper.
