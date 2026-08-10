# Session handoff — 2026-08-10, second session

**This file is a session record, not a design document.** The design of record is
`CLAUDE.md`, `TYPED-TABLES-PLAN.md`, `ARCHITECTURE.md` and `TODO.md`. Where this
file and those disagree, they win. **Read `CLAUDE.md`'s "Current state" and
"Next session starts here" sections first** — both were updated at the end of
this session.

Previous handoff: `HANDOFF-2026-08-10.md` (first session, same day — TYPED-TABLES-PLAN.md
step 4, row-level rejection, Bronze intake gate, `artefact_outcome`, 410 tests).

---

## One-line state

`make ci` is green from a clean cluster — **422 tests** (up from 410). Three
things landed this session: closed the `NULLS NOT DISTINCT` gap from the prior
session, confirmed the "JSON adapter" ask was already satisfied, and built the
ingestion surface (`src/api/` — REST upload/trigger/status + GraphQL reads).

Re-check: `make ci`; `uvicorn src.api.app:app` then curl it.

---

## What was built, in the order it happened

### 1. `NULLS NOT DISTINCT` — tenant migration `014_nulls_not_distinct.sql`

Closes the gap `HANDOFF-2026-08-07.md` found: `purchase_register`,
`creditor_ageing_report` and `common_input_service_invoice`'s natural-key
indexes include a nullable `supplier_gstin`, and a partial unique index treats
two NULLs as distinct — so two concurrent loads of the same
unregistered-supplier invoice could both land as live rows. The loader's
lookup (`IS NOT DISTINCT FROM`) was already correct; only the index wasn't.

Migration drops and recreates all three indexes with `NULLS NOT DISTINCT`. No
duplicate sweep needed — no production data exists. New gate:
`test_the_index_rejects_two_live_nulls_the_lookup_would_have_matched`
(`tests/conformance/test_registers.py`) inserts past the loader directly.

**Worth knowing:** the gate test's first draft had the exact bug
`test_typed_tables.py`'s `_probe_rejects` docstring already warns about — a
`pytest.raises(UniqueViolation), admin.transaction():` probe that, if the
constraint were ever weakened again, would both fail AND commit its duplicate
row, corrupting fixture data for every test after it. Caught by actually
running the mutation check, not by review; fixed with the same
unconditional-`Rollback` pattern `_probe_rejects` uses.

Mutation check: dropped `NULLS NOT DISTINCT` on both tenants' index, ran the
suite — exactly the new test failed. Restored, re-ran clean. (First attempt at
the mutation check left a corrupting duplicate row from the test's own bug
above; had to manually clean it up before re-running — recorded here so it
isn't mistaken for a data anomaly by a future session.)

Re-check: `pytest tests/conformance/test_registers.py -q`

### 2. JSON adapter — confirmed already done, no code

`CLAUDE.md`'s "Next session" list item 3 read "JSON/Excel adapters... CSV and
NDJSON are done." Checked with the user whether "JSON" meant something NDJSON
doesn't cover (e.g. a wrapped JSON array, for portal-exported documents). User
confirmed: nothing else needed — NDJSON (added last session) **is** the JSON
support the design calls for, per `CLAUDE.md`'s own "JSON resolved to NDJSON."
No new code. Only Excel remains unscoped.

### 3. The ingestion surface — `src/api/`

The item that's been "not built, not decided in detail" since
`HANDOFF-2026-08-07.md`. Built to a deliberately narrow first slice, three
scope decisions made explicitly with the user before writing code (plan file:
`/Users/manusteve/.claude/plans/dapper-skipping-star.md`):

- **Auth: pluggable, placeholder adapter.** `src/api/auth.py`'s `AuthPort`
  Protocol — `resolve(request) -> TenantContext` — same idiom as
  `VirusScanPort`. One adapter, `StaticTokenAuth`: bearer token →
  `Settings.api_tenant_tokens[token]` → slug. Not a security boundary — no
  signature, no expiry, no revocation. ARCHITECTURE.md 5.6 wants a signed
  Keycloak JWT claim; that's still unbuilt, and the Protocol boundary is what
  makes it a second adapter later, not a route-handler rewrite. The
  invariant that *is* enforced today: the tenant slug is never read from
  anything the client's path/query/body supplies — only from the resolved
  token. `src/api/deps.py`'s `get_tenant` is the one place a request becomes
  a `TenantContext`; REST and GraphQL (`src/api/graphql/schema.py`'s
  `context_getter`) both go through the same resolution.

- **GraphQL: built now, not deferred.** New dependency
  (`strawberry-graphql[fastapi]`), mounted at `/graphql`. Resolvers
  (`artefactOutcome`, `entitlement`) wrap `SilverReader`/`EntitlementReader`
  unchanged — no new read logic. `Role.RECON` already grants exactly "SELECT
  on `v1_` views, nothing else in Silver" (`migrations/shared/001_app.sql`
  step 5), which **is** the "reader role" the 2026-08-07 design called for —
  no new Postgres role needed.

- **Trigger: a documented stub.** `POST /artefacts/{ingest_id}/trigger`
  INSERTs into a new table, `load_trigger` (tenant migration
  `015_load_trigger.sql` — INSERT-only, mirrors `artefact_ledger`'s grant
  shape, FK'd to `artefact_ledger` so it 404s for an artefact this tenant
  never uploaded). **It calls no loader.** There is still no
  `doc_type_code -> loader` dispatcher: nothing in `platform_ref.document_type`
  names which class handles a type (only `table_name`, a register-family
  signal, and `archetype`, a structural-family signal). Building the real
  dispatcher was explicitly deferred — the user chose "stub only" when asked.

  **Upload and status are real, not stubs** — `POST /artefacts` wraps
  `BronzeIngestionService.receive` (file-check → hash → dedup → virus scan →
  PUT → ledger INSERT, unchanged), and `GET /artefacts/{id}/status` wraps
  `SilverReader.artefact_outcome` (unchanged).

New dependencies (`pyproject.toml`): `fastapi`, `uvicorn[standard]`,
`strawberry-graphql[fastapi]`; `httpx` added to `dev` for `TestClient`.

New settings: `Settings.api_tenant_tokens: dict[str, str]` (token → slug,
JSON-shaped env var). `.env`/`.env.example` both updated with placeholder dev
tokens.

New tests: `tests/conformance/test_api_ingest.py`,
`tests/conformance/test_api_auth.py`, `tests/conformance/test_api_graphql.py`,
plus `tests/conformance/conftest.py` (new — builds a test `FastAPI` app wired
directly to the suite's own `app_pool` and a MinIO-backed `S3ObjectStore`,
rather than exercising `src.api.app`'s production `lifespan`, which opens its
own pool from env DSNs and would be a second pool per test run).

**Mutation check on the auth boundary:** broke `StaticTokenAuth.resolve` to
ignore which token was presented and always resolve to the first configured
slug. Two tests failed — `test_unknown_token_is_401` and
`test_tenant_a_token_cannot_read_tenant_b_artefact` — both meaningfully (not
spurious collateral: an auth resolution bug of this shape genuinely breaks
both "reject an unrecognised token" and "one tenant's token can't see
another's data"). Restored, re-ran clean.

**Verified against a real running server, not just `TestClient`:**
`uvicorn src.api.app:app --port 8123`, then curl'd upload → status → trigger
→ a request with no `Authorization` header (401) → a GraphQL query — all
against the live dev cluster. Confirms the app actually boots end to end, not
only under the test harness.

Re-check:
`pytest tests/conformance/test_api_ingest.py tests/conformance/test_api_auth.py tests/conformance/test_api_graphql.py -q`

---

## What is explicitly NOT done (so it isn't mistaken for done)

Recorded in `TODO.md` under "Ingestion surface — what's built and what's still
missing":

1. **No Bronze→Silver dispatcher.** The trigger endpoint records intent only.
2. **No real auth.** `StaticTokenAuth` is a placeholder, not deployable
   anywhere internet-facing.
3. **No worker.** Nothing consumes `load_trigger` rows or watches Bronze —
   there is still no automated path from "artefact received" to "artefact
   promoted." A human or a script still has to call a loader library function
   directly, exactly as `tests/handoff/` does today.
4. **No `docker-compose.yml` change** — the API isn't a service in the dev
   stack; run it manually with `uvicorn src.api.app:app`.

---

## What is next

`CLAUDE.md`'s "Next session starts here" has the authoritative, re-ordered
list. Summarizing:

1. v2 sign-off on the dropped `v1_purchase_invoice_line` view (unchanged from
   the first session — still not raised with anyone).
2. The Bronze→Silver dispatcher (`doc_type_code -> loader` routing) — now the
   critical path item, since the trigger endpoint exists but does nothing
   without it.
3. Real auth (Keycloak JWT/JWKS) for the ingestion surface.
4. `entitlement_instrument` (33 HYBRID types) — still open, unchanged.
5. The 63 HYBRID types generally — still open, unchanged.
6. Excel adapter — still unscoped, unstarted, not requested.
7. The six deploy blockers in `TODO.md` (B1–B6) — none have moved.
8. A live ClamAV container for local dev — still not requested.
9. A worker to actually consume `load_trigger` (depends on item 2 above).

---

## Environment notes

- Dependencies installed via `uv pip install -e ".[dev]" --python .venv/bin/python3`
  — the venv has no `pip` binary of its own; `uv` is what's on PATH and what
  actually resolved and installed everything cleanly.
- `ruff`'s bugbear rule (`B008`) flags FastAPI's `Form(...)`/`Depends(...)`
  default-argument idiom by default. Fixed with
  `[tool.ruff.lint.flake8-bugbear].extend-immutable-calls` in `pyproject.toml`
  rather than a blanket per-file ignore — these specific calls are evaluated
  once at route registration, not per request, so B008's concern doesn't
  apply to them.
- `strawberry`'s `GraphQLRouter` needs its context type to subclass
  `strawberry.fastapi.BaseContext`, not just be an arbitrary dataclass —
  mypy caught this (`src/api/graphql/schema.py`'s `Context`), not a runtime
  failure; worth knowing if the GraphQL schema grows and someone reaches for
  a plain dataclass again out of habit.
