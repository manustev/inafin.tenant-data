# Session history — inafin-tenant-data

Archived from `CLAUDE.md` on 2026-08-24 (eleventh session) so that the file
loaded into every conversation stays small. **Nothing here is superseded by
being archived** — this is the verbatim narrative of sessions 1–10: what was
built, what was verified and how, what was tried and rejected, and why.

`CLAUDE.md` keeps the durable rules (invariants, decisions-not-to-re-propose,
workflow) and the current open backlog. Read this file when you need the
reasoning *behind* one of those, or the detail of how something was verified.

The per-session hand-off documents (`HANDOFF-*.md`) are unchanged and remain
the finest-grained record.

---

## Snapshot moved from CLAUDE.md on 2026-09-03 (trim pass, after sixteenth session)

CLAUDE.md's "Built and working" table and "Load-bearing rules" detail were
moved here verbatim so that file stays lean for every new session to load.
**Nothing here is superseded by being archived.** As of the sixteenth
session (2026-09-03): 666 passed / 2 skipped / 0 failed, `make lint` +
`make typecheck` clean, `make migrate` zero drift, schema release **`v8`
CURRENT**.

### Built and working — do not re-propose any of this

| Area | What exists |
|---|---|
| Isolation | schema-per-tenant, GRANT-only, `app_login` NOINHERIT + `SET LOCAL ROLE`; templated tenant migrations (`src/migrate/runner.py`) |
| Bronze | insert-only `artefact_ledger`, intake gate (`src/bronze/filecheck.py`, `scan.py` + `VirusScanPort`), MinIO object store with Object Lock |
| Registry | `platform_ref.document_type`, 125 in-scope types; `field_contract`, `extraction_spec`, `table_name`, `dispatch_mechanism` are all **registry DATA**, not code branches |
| Silver A1 | all 24 A1 registers load — `sales_register.py` (header/line) + 23 spec-driven via `RegisterSpec`/`RegisterLoader`, kept honest by `test_register_specs.py`'s DDL gate |
| Silver A2–A7 | `src/extraction/` — 7 archetype bases, registry-driven specs, all 50 specimens in `reference/A1-A7Documents/`; typed tables for archetypes 3/4/6/7/8. All five PDF archetype services now `record_or_supersede` — a re-dispatched artefact corrects its row (`src/silver/supersede.py`), it does not conflict |
| PDF text | `PypdfReader` + `FallbackPdfTextReader` + real `PaddleOcrReader` (optional `ocr` extra, verified against real weights) |
| Dispatch | `src/dispatch/router.py`'s `dispatch_load` — 5 mechanisms: `PDF_EXTRACTION`, `SALES_REGISTER`, `REGISTER_LOADER`, `ARCHETYPE1_PROMOTE`, `GSTN_JSON_PROMOTE` |
| Trigger errors | `src/dispatch/trigger.py` classifies DB failures into `UnknownArtefact` / `SilverConstraintViolation` (`kind` CONFLICT/INVALID); `POST /artefacts/{id}/trigger` and `tenantctl trigger` map to 404/409/422 — never an opaque 500 |
| API | `src/api/` REST upload/trigger/status + GraphQL reads; `POST /artefacts/{id}/trigger` dispatches synchronously in-process |
| Manifest | `SilverWriteResult.batch_id` → `src/dispatch/manifest.py` → `BatchPublisherPort`/`_publish_if_ready`, wired for all mechanisms, verified with a recording fake |
| Connectors | `src/connectors/` — `SourceConnectorPort`, working `LocalFixtureConnector`, 7 live adapter stubs, `factory.build_source_connector` keyed on `Settings.source_data_mode` |
| Category B | `GSTR_2B` (tenant `026`, shared `033`) and `GSTR_3B` (tenant `027`, shared `034`) built end to end and verified against the live DB |
| Platform | shared `024`–`032` identity/onboarding/catalog/grants, reconciled to `inafin-api`'s real contract; `API-CONTRACT.md` is the published boundary |
| Schema catalogue | `platform_ref.document_type_schema`/`document_type_field` (shared `035`/`036`) — the tenant-facing export contract for all 125 in-scope types, derived by `src/catalogue/document_schema.py`, never hand-written |
| Field constraints | `document_type_field`'s constraint columns + `document_type_rule` (shared `041`/`042`) — derived straight from live `pg_constraint`/`pg_type` by `src/catalogue/field_constraints.py`, gated against drift by `test_field_constraints.py` |
| Schema releases | `schema_release`/`schema_artifact` (shared `039`) + the `<prefix>-platform` bucket; `scripts/publish_schema_release.py` publishes a release. **`v8` is CURRENT** (sixteenth session — `GSTIN_REGISTER`'s corrected `as_of_date` source_label), `v1`–`v7` kept but `SUPERSEDED` |
| Schema pinning | `t_<slug>_bronze.schema_pin` (tenant `028`) — pinned on first upload by `src/catalogue/pin.py`'s `ensure_schema_pin`; an operator rolls a tenant forward with `tenantctl reschema` (`roll_forward`/`roll_forward_all`) |
| Local auth | `AUTH_MODE=none` (`src/api/auth.py`'s `NoAuth`) for local dev only — `StaticTokenAuth` is still the non-`none` default and still a placeholder for real auth |
| Archetype-7 table content | 4 of 7 entity-master types' PDF TABLE content (not just header facts) is now a typed, queryable Silver fact table — `SHAREHOLDING_PATTERN`, `GSTIN_REGISTER`, `DIRECTOR_LIST_WITH_DIN`, `KMP_LIST` (shared `048`–`059`, tenant `032`–`035`). `TableFactExtractor` (`src/extraction/entity_master_types.py`) is the generic base every type but `SHAREHOLDING_PATTERN` (which needs a real FY-expansion, not a straight row-to-fact write) uses as-is. `src/extraction/tablevalue.py`'s `parse_table_rows` reconstructs a row from a WINDOW of physical lines (shortest match wins), not a one-line look-back — needed because real specimens wrap a row across up to 5 lines, sometimes mid-token (a GSTIN split across a line break). `SHAREHOLDING_PATTERN`/`GSTIN_REGISTER`/`DIRECTOR_LIST_WITH_DIN` all write end to end and are supersede-tested; `GSTIN_REGISTER`'s own `as_of_date` was mislabeled, not a genuine gap — fixed via a new colon-less, one-line-lookahead `date` fallback in `_find_value` (`src/extraction/labelvalue.py`, mirroring the existing `money` one), shared `058`/`059`, published as schema release `v8`. `KMP_LIST` stays a named `Partial(missing=("as_of_date",))` — its table grammar is built and unit-tested but genuinely unreachable: the specimen states no date at all, only a fiscal-year label, and `entity_master_record.as_of_date` is `NOT NULL` and part of its own bitemporal key, so it cannot be fabricated. `RELATED_PARTY_REGISTER`'s table was deliberately NOT built — its last two columns are open-ended free prose with no delimiter between them; every row-boundary strategy tried corrupted data rather than merely dropping a row (see `tablevalue.py`'s module docstring) |

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
- **A "current row" lookup must key on the unique index's own columns, not
  "the business key" in the abstract** (`src/silver/supersede.py`). A lookup
  that disagrees with the index does not prevent a duplicate, it just moves
  where the duplicate appears. Closing a prior row without setting its
  `supersedes_*` pointer satisfies the index and silently breaks the
  bitemporal chain — a mutation check is what catches that, not a passing test.

---

## Current state (2026-08-19, tenth session)

**Category B connector layer — the source-pulling side, upstream of Bronze
intake. Not the Silver/dispatch-mechanism work `HANDOFF-2026-08-19-categoryB.md`
scoped (D1/D2/D3 are explicitly parked, Steve's call, "next iteration").**
Steve's actual brief this session (`reference/B-Document_prompt.md`) was
narrower and upstream of that handoff: build the adapters that PULL Category
B documents from GST portal/API/ICEGATE/DGFT/external sources into Bronze,
connector/adapter-shaped so a new source is additive, with a config flag to
answer from a local fixture folder instead of a live call (no real
credentials exist anywhere in this workspace).

**The 146-file sample set landed the same day** (`reference/B-Documents/`,
mock taxpayer VARDHMAN PRECISION INDUSTRIES, FY 2024-25, 4 GSTINs, 19 seeded
anomalies in `ANOMALY_KEY.json`). Before building anything, the handoff's own
task 0 (CSV-vs-JSON losslessness check) was run for real, not assumed: GSTR-1/
2B/3B's flat CSVs are confirmed LOSSY (2B's CSV covers only its `b2b` section,
228 of 258 total lines, and drops `isd`/`impg` entirely; 3B's CSV is a
21-column hand-picked summary of a 55-leaf-path JSON) — JSON is source of
record for the nested returns, CSV is sufficient only for genuinely flat
types (GSTR-7/8, REG-06, SCN register, ...). This finding lives in
`src/connectors/local_fixture.py`'s docstring and directly drives its
format-preference rule.

**New package `src/connectors/`** — `SourceConnectorPort` (`base.py`, one
`fetch()` method, mirrors `VirusScanPort`/`ObjectStorePort`'s Protocol-not-
library-import shape), `FetchedDocument` (bytes + filename + content_format +
source_ref + fetched_at), two named exceptions
(`ConnectorNotConfiguredError` for an unwired live adapter,
`SourceDocumentNotFoundError` for a fixture miss). `local_fixture.py`'s
`LocalFixtureConnector` reads `<fixture_root>/<tenant_slug>/<ref>/<filename>`
— fully working today — with two resolution rules: filter by
gstin+period-in-filename when both are supplied (naturally excludes flat
all-periods rollups without knowing their filenames), then prefer
json > csv > pdf when more than one format survives (mutation-checked: with
`_FORMAT_PREFERENCE` reversed, exactly `test_json_is_preferred_over_csv_and_
pdf_when_all_three_exist` failed — `B3.01`'s open-SCN register, the one real
case where csv/json/pdf all describe the same document with no per-file
GSTIN to filter on; restoring fixed it).

**Seven live adapters** (`src/connectors/adapters/`), one per
`platform_ref.document_type.source_system` value the Category B rows
actually use (`GSTN_API`, `GSTN_PORTAL`, `ICEGATE_API`, `DGFT_PORTAL`,
`IRP_API`, `EWB_PORTAL_API`, `COURT_REGISTRY`) — a shared `HttpSourceConnector`
ABC (`adapters/base_http.py`) gates every one on `_configured()`
(base_url + credential both set) before ever reaching `_do_fetch`, which
every adapter still raises `NotImplementedError` from today (no GSP/ICEGATE/
DGFT/IRP contract exists in this workspace). Each adapter's docstring records
the real call shape from GST domain knowledge (GSP-mediated session auth for
GSTN_API/EWB_PORTAL_API, GSTN's transport envelope needing unwrapping for
GSTR_2B specifically, why GSTN_PORTAL/DGFT_PORTAL have no published API and
what the two realistic paths are, IRP's per-provider registration, and why
`CourtRegistryConnector` will likely never have a real `_do_fetch` at all —
`COURT_STAY_ORDER` per the source register is `Client / Court Registry`,
i.e. manual upload, not a pullable source). `INAFIN_CORPUS`
(`B4.03`/`B4.04`) deliberately has NO connector — confirmed against
`registry/README.md` §2, those two rows belong to `inafin-gst-corpus`, not
here.

**`factory.py`'s `build_source_connector(source_system, settings)`** is the
one place `Settings.source_data_mode` (`"local_fixture"` default | `"live"`)
decides which of the above answers a call — mirrors `src/dispatch/router.py`'s
dispatch-table idiom exactly. Live mode reads
`Settings.source_connector_base_urls`/`source_connector_credential_refs`
(both `dict[str, str]`, keyed by `source_system`, same shape
`api_tenant_tokens` already uses) — turning a source on for real is
populating one dict entry plus filling in that one adapter's `_do_fetch`,
never a change to the factory or any caller.

**The "B1.01 is used by A1,A6,A7,B6, mode BOTH" mapping the brief asked for
already existed** — `registry_lookup.py`'s `resolve_document_source` is a
thin read, not a new table: `platform_ref.document_type.sections`
(a native `text[]`) and `.mode`, joined against `document_type_ref` (the
separate ref<->doc_type_code table, needed because one ref can resolve to
several codes — `A1.20`'s seven, per `003`'s own schema comment) for the
canonical `ref`. Verified directly: `GSTR_1`'s row reads
`sections={A1,A2,A7,A8,A9,A10}, mode=BOTH`, an exact match to
`INAFIN_Recon_Doc4_Sec2_SourceDocRegister_v2..md`'s B1.01 row. No schema
change was made for this.

**`scripts/stage_bronze_fixtures.py`** reshapes `reference/B-Documents/
inafin_mock_categoryB/` (organised by group/ref for human reading) into
`fixtures/bronze_source/<tenant_slug>/<ref>/<filename>` (organised by tenant
first, as any real multi-tenant deployment needs) — `reference/B-Documents/`
itself is untouched, staying the as-received copy. Run once this session
against tenant slug `vardhman`: 145 of 146 files staged across all 35 B
refs (the 146th is `ANOMALY_KEY.json`/`MANIFEST.json`/`README.md` at the
sample set's root, correctly not staged — those aren't per-ref documents).

**Verified**: `make lint`/`make typecheck` clean on `src scripts tests`
(mypy findings in `scripts/check_isolation.py`/`baseline_api_schema.py` are
pre-existing, confirmed via `git status` — untouched this session; ruff
findings inside `reference/B-Documents/_generator/` are outside `make lint`'s
scope, sample-data generator code, not this repo's source). Full suite: 553
passed / 2 skipped (up from 543) — 10 new tests in
`tests/conformance/test_connectors.py`, run against the live shared cluster,
never a reset.

**Not built this session, and deliberately not attempted**: an actual puller/
orchestrator that iterates `(tenant, doc_type_code, gstin, period)` tuples
and calls `connector.fetch()` then `BronzeIngestionService.receive()` — the
brief asked for the connectors themselves, not the scheduling loop that
calls them; `dispatch_load`'s own `POST /trigger` vs. future-worker
relationship (CLAUDE.md, ninth-session-era "Next session" item 10) is the
closest existing precedent for what that orchestrator would look like. D2
(upload-first vs poller — though this connector layer's local-fixture mode
already IS the "upload/local" half of that question) and D3 (whether
archetype 5 needs a new `GSTN_JSON`-style `dispatch_mechanism`) are still
open — `HANDOFF-2026-08-19-categoryB.md` is UNCHANGED and still the
read-first document for that follow-on work. **D1 was decided later the same
session — see below, it is no longer open.**

### Same session, continued: Bronze→Silver→Gold design discussion, D1 decided, manifest-publish gap closed

Discussed before building: how Category B bytes move Bronze→Silver→Gold once
the connector layer lands them. Two findings from that discussion, both
acted on:

1. **Gold is not this repo's job.** `ARCHITECTURE.md` §2/§5: Gold
   (`t_<slug>_gold`) is owned by `inafinplatform/v2` ("Pipeline 2"), fed by
   *its own* recon rules consuming Silver — this repo's job stops at Silver
   plus ringing a Kafka doorbell. Checked, not assumed: `BatchPublisher`/
   `BatchManifest` (`src/events/publisher.py`, `src/silver/promote.py`)
   existed and were tested (`tests/handoff/`) but **nothing in the live
   dispatch path called `publish()`** — true for every mechanism, every
   document type, not a Category-B-specific gap. Steve confirmed: close it.
2. **D1 (archetype-5 table shape) decided: typed columns, except named
   narrative fields.** GSTR-1/2B/3B/9/9C's header facts (gstin, period, ARN,
   filing status) and every section a reconciliation check actually
   joins/filters on (invoice lines, ITC amounts, supplier GSTINs — what
   `ANOMALY_KEY.json`'s MOCK-01 through MOCK-19 touch) get real typed
   columns, per-return-type tables hanging off a shared header — consistent
   with `TYPED-TABLES-PLAN.md`'s standing ban on jsonb-bag archetype tables.
   Only genuinely narrative/variable fields (GSTR-9C Table 6's free-text
   reasons list, and similar) may fall back to text/jsonb, on a
   named-field-by-named-field basis, not a whole-table escape hatch. **Not
   yet built** — this decides the shape for whenever the archetype-5 tables
   themselves are built (D1's own migrations, still unbuilt); it does not by
   itself close `HANDOFF-2026-08-19-categoryB.md`'s D1 item, which should be
   marked decided-not-built there.

**The manifest-publish gap is closed, generically, for all four dispatch
mechanisms — not a Category B change.**

- **`SilverWriteResult` (`src/extraction/base.py`) gained a `batch_id`
  field.** Every archetype's `to_silver()` already created an `ingest_batch`
  row via `create_single_document_batch` (or, for the archetype-1-within-PDF
  path, via `SilverPromotionService`) — that id existed, it just wasn't
  surfaced past `record_id` (which names the ARCHETYPE TABLE's own row,
  `instrument_id`/`event_id`/etc., a different id for 5 of the 7 archetype
  bases). All 7 success-return sites now set `batch_id=` explicitly.
- **New `src/dispatch/manifest.py`: `read_batch_manifest(pool, ctx,
  batch_id)`.** One generic read of `{silver}.ingest_batch` by `batch_id`,
  used for every mechanism's manifest — not four separate constructions.
  Works because all four mechanisms already write a row with exactly the
  columns `BatchManifest` needs; this module is a read of the row of record,
  not a new one.
- **`BatchPublisherPort` Protocol added to `src/events/publisher.py`** (same
  shape as `VirusScanPort`/`ObjectStorePort`) — `BatchPublisher` satisfies it
  structurally, no inheritance needed, same idiom `NullScanner`/
  `ClamAVScanner` already use.
- **`dispatch_load` gained an optional `publisher: BatchPublisherPort |
  None = None` parameter**, and a new `_publish_if_ready` helper wraps all
  four mechanisms' returns: publishes iff `publisher is not None and
  outcome.batch_id is not None` (a QUARANTINED outcome has no batch to
  publish). `BatchPublisher.publish` itself never raises, so no try/except
  needed here — verified this is still true, not just assumed from its
  docstring.
- **Wired end to end**: `src/api/app.py`'s lifespan now constructs and
  starts a real `BatchPublisher` from `Settings.kafka_bootstrap`/
  `kafka_batch_topic`/`kafka_enabled`, stored on `app.state.batch_publisher`
  and stopped on shutdown; `src/api/deps.py` gained `BatchPublisherDep`;
  `POST /artefacts/{id}/trigger` passes it into `dispatch_load`.
- **Verified with a fake, not just wiring inspection**:
  `tests/conformance/conftest.py`'s `RecordingPublisher` (a
  `BatchPublisherPort` fake recording every manifest) replaces the real
  Kafka producer in `api_app`; all four `test_trigger_dispatches_*` tests
  (`tests/conformance/test_api_ingest.py`) now assert exactly one manifest
  was published per mechanism, with the right `batch_id`/`document_type`/
  `entity_id` — including `PDF_EXTRACTION`, whose `batch_id` was `None` in
  every trigger response before this change (now a real id, since
  `SilverWriteResult.batch_id` is threaded through). Mutation-checked:
  removing `_publish_if_ready`'s publish call failed exactly those 4 tests,
  nothing else; restoring returned the suite to green.
- **Verified**: `make lint`/`make typecheck` clean, full suite 553 passed /
  2 skipped (same count as the connector-layer work above — this was the
  same session, sequential work, not a second pass).

### Same session, continued again: remaining design items closed, GSTR_2B built end to end

Discussed and closed, all confirmed: **D2** (upload-first, poller deferred —
unchanged from the earlier recommendation). **D3** (archetype 5 needs a new
dispatch mechanism, `GSTN_JSON_PROMOTE`, bespoke Python per return type —
mirrors the archetype-2 `register_types.py` precedent, not a declarative
spec grammar). **Orchestrator shape** (a synchronous "pull now"
function/CLI is the right near-term caller of `src/connectors/`, a poller
becomes a second caller later — still UNBUILT, not started this session).
**0f** (the cheap-14 loader) — recommendation stands, still deferred, not
started.

**GSTR_2B built end to end — table, parser, dispatch mechanism, tests,
verified against the live cluster.** The first archetype-5 type, chosen per
`HANDOFF-2026-08-19-categoryB.md`'s own suggested order (hardest shape
first, sets the pattern).

**A real design correction caught before any DDL was written**: the
original plan (this session's own D1 discussion, and the handoff's own
option 2) was a shared `filed_return` HEADER table across GSTR_1/2B/3B/9/9C
with per-return section tables hanging off it. Checked against
`TYPED-TABLES-PLAN.md` §8 before building it — **§8 already rejected exactly
this shape**, for the marketplace-report types (`AMAZON_MTR`/`FLIPKART_GTR`/
...), "one table per registry document type... collapse only where the
shape is provably identical and the types always co-occur." GSTR-1/2B/3B/9C
do not share a shape (established earlier this session by diffing their real
JSON). **Corrected design: no shared header table at all.** `GSTR_2B` gets
its own fully independent table set (`gstr_2b`/`gstr_2b_line`/
`gstr_2b_itc_summary`); GSTR_3B/1/9/9C will each get their own when built,
never sharing a table via a discriminator. `gstr_2b_line`'s own `section`
column (B2B/IMPG/ISD) does NOT violate §8 — those are sub-SECTIONS of one
document TYPE's payload, not separate registry types, the same relationship
`sales_register_line`'s `line_number` already has to its header.

**Built, following `sales_register.py` (tenant `008`) as the explicitly
designated reference pattern, column for column**:
- Tenant migration `026_gstr_2b.sql` — `gstr_2b` (header, `id bigint`
  identity, `doc_type_code` pinned `DEFAULT 'GSTR_2B' CHECK (=...)`, natural
  key `(entity_id, gstin, tax_period) WHERE superseded_at IS NULL`,
  bitemporal, `batch_id` FK to `ingest_batch`), `gstr_2b_line` (B2B/IMPG/ISD
  flattened to one row per invoice item (B2B) or per document (IMPG/ISD) —
  heavily nullable across the three real shapes, on purpose, same reasoning
  migration `006`'s docstring already states for `entity_master_record`),
  `gstr_2b_itc_summary` (the `itcsumm` block, stored as GSTN reported it,
  never recomputed from the lines — same "stored as supplied" reasoning
  `sales_register`'s header totals already use). Three `v1_` views, common
  core only.
- Shared migration `033_gstr_2b_dispatch.sql` — widens `023`'s
  `dispatch_mechanism` CHECK with `DROP CONSTRAINT`/`ADD CONSTRAINT`
  (**`023` itself untouched** — applied migrations are never edited;
  `scripts/gen_registry_seed.py`'s `DISPATCH_MECHANISM_PATH` must NOT be
  regenerated over it, same precedent migration `014`'s `ITC_04` archetype
  correction already set), points `GSTR_2B`'s registry row at
  `dispatch_mechanism = GSTN_JSON_PROMOTE, table_name = gstr_2b`, seeds
  `Gstr2b_Section`/`Gstr2b_Document_Kind`/`Gstr2b_Itc_Category`/
  `Gstr2b_Itc_Availability` vocabulary (CDNR included in the vocabulary even
  though unparsed — see below). `registry/document_types.csv`'s `B1.03` row
  updated to match, by hand, NOT by rerunning the generator (same reasoning).
  `scripts/gen_registry_seed.py`'s `DISPATCH_MECHANISMS` frozenset widened
  too, so future CSV validation accepts the value — this does not
  regenerate `023`, only changes what a NEW generation run would validate.
- `src/silver/gstn_returns/gstr2b.py` — `parse_gstr_2b`/`Gstr2bLoader`.
  **CDNR is deliberately NOT parsed.** All 24 real GSTR-2B specimens in
  `reference/B-Documents/` carry an empty `cdnr` array — zero populated
  examples exist in this workspace. Rather than guess the `nt[]` shape from
  its `b2b` sibling (plausible from general GST knowledge, but exactly the
  "invented vocabulary" mistake `CLAUDE.md` already names once for the
  onboarding tables), `_reject_unparsed_cdnr` quarantines any artefact with
  a non-empty `cdnr` instead. The DB vocabulary already allows the value, so
  this is a parser change away from full coverage, not a second migration.
  A period mismatch between the payload's own `rtnprd` and the trigger's
  `period_start` is also rejected — a real integrity check, not decorative.
- `src/dispatch/router.py` — new `_GSTN_JSON_PROMOTE` branch, dispatching
  through a `_GSTN_RETURN_LOADERS: dict[str, type]` keyed by `doc_type_code`
  (today: `{"GSTR_2B": Gstr2bLoader}`) — the same "one dict entry per type,
  not an if/elif" shape `PDF_EXTRACTION`'s archetype-1-within-PDF dispatch
  already uses. Manifest-publish (`_publish_if_ready`) works unchanged for
  this mechanism too — `Gstr2bOutcome.batch_id` is `None` on an unchanged
  resubmission (no `ingest_batch` row written at all, matching
  `sales_register.py`'s per-row idempotency promise, extended to
  whole-statement grain since a 2B statement is one document, not many).
- **Verified against the real staged sample data, not synthetic
  fixtures** (`tests/conformance/test_gstn_returns.py`, 7 new tests):
  parses the real `GSTR2B_29AABCV1234K1Z9_112024.json` correctly (B2B + ISD
  sections present, 0 IMPG that period, ISD line correctly has
  `taxable_value IS NULL`); loads and is idempotent on byte-identical
  resubmission; a changed statement (ISD section cleared) correctly
  supersedes the prior header while its own lines stay attached to it;
  routes end-to-end through the real `dispatch_load` entry point, not just
  the loader directly. Two pre-existing tests needed updating for a real,
  expected reason (not a regression): `test_gen_registry_seed.py` (fixed by
  the `DISPATCH_MECHANISMS` widen above) and
  `test_register_specs.py::test_the_catalog_covers_every_a1_type_except_
  sales_register`, whose exception set is now `{"SALES_REGISTER",
  "GSTR_2B"}` — that test's scope was always A1-only, and `GSTR_2B` is
  archetype 5, so a second named exception is the correct fix, not a
  widened assertion that would stop catching a real A1 gap.
- Mutation-checked the one real judgment call in this build: temporarily
  disabling `_reject_unparsed_cdnr`'s check failed exactly
  `test_nonempty_cdnr_is_rejected`, nothing else; restored clean.
- **Verified**: `make migrate` applied both new migrations cleanly against
  the live shared cluster, zero drift, confirmed again with zero pending
  migrations on a second run. `make lint`/`make typecheck` clean. Full
  suite: 560 passed / 2 skipped (up from 553).

**Not built this session**: GSTR_1/9/9C (each needs its own table set — D1's
"typed except narrative" decision applies, but the concrete DDL is per-type
work, not started); the pull orchestrator (0d, still open); the cheap-14
loader (0f, still deferred); CDNR parsing for GSTR_2B (blocked on a real
specimen, not on design).

### Same session, continued once more: GSTR_3B — code complete, UNVERIFIED against the DB

Followed `gstr_2b`'s pattern for the second archetype-5 type. GSTR-3B's real
shape (confirmed against all 31 specimens, not one) is genuinely different
from GSTR-2B — a summary of FIXED BOXES, not a grouped invoice list — which
is exactly the divergence D1's discussion predicted and the reason this was
chosen as the second type to build, before GSTR-1/9/9C.

- Tenant migration `027_gstr_3b.sql` — `gstr_3b` header (Table 3.1's five
  sub-items, Table 4(C) net ITC, Table 6 interest each as their own typed
  columns, since exactly one of each exists per return), plus two child
  tables for the small CLOSED-vocabulary lists: `gstr_3b_itc_detail`
  (Table 4(A)/(B)/(D) itc_avl/itc_rev/itc_inelg, `ty` vocabulary
  IMPG/IMPS/ISRC/ISD/OTH/RUL — confirmed identical across all 31 files by
  direct count, not assumed) and `gstr_3b_inward_supply` (Table 5,
  GST/NONGST). `inter_sup` (Table 3.2) is NOT modelled — same situation as
  `GSTR_2B`'s `cdnr`: all 31 real specimens carry empty
  `unreg_details`/`comp_details`/`uin_details` arrays, zero ground truth to
  build against.
- Shared migration `034_gstr_3b_vocabulary.sql` — registry row
  (`dispatch_mechanism=GSTN_JSON_PROMOTE, table_name=gstr_3b`) plus
  vocabulary. No CHECK widen needed (`GSTN_JSON_PROMOTE` already exists from
  `033`). `registry/document_types.csv`'s `B1.05` row updated by hand, same
  as `B1.03` was.
- `src/silver/gstn_returns/gstr3b.py` — `parse_gstr_3b`/`Gstr3bLoader`,
  same whole-statement-supersession shape `gstr2b.py` established.
  Additionally rejects an unrecognised `ty` value in the ITC/inward-supply
  lists (not just a non-empty `inter_sup`) — a stricter check than GSTR_2B
  needed, since GSTR_2B's sections have no comparable small closed
  vocabulary to validate against.
- `src/dispatch/router.py`'s `_GSTN_RETURN_LOADERS` now has `GSTR_3B` too.
  **A real mypy Protocol bug was found and fixed while wiring this**:
  `GstnReturnOutcomePort` (`src/silver/gstn_returns/__init__.py`, new) had
  declared `batch_id`/`inserted` as plain attributes, which mypy's Protocol
  matching requires to be SETTABLE — both `Gstr2bOutcome` and `Gstr3bOutcome`
  are frozen dataclasses, so neither satisfied it. Fixed by declaring them
  as read-only `@property` in the Protocol instead.
- `tests/conformance/test_gstn_returns_gstr3b.py` written, mirroring
  `test_gstn_returns.py`'s GSTR_2B gates exactly (parse correctness against
  the real specimen, idempotent resubmission, supersede-on-change, dispatch
  routing, plus one extra: rejecting an unrecognised `ty`).
- **`ruff check` and `mypy` both clean** on everything above.

**NOT YET APPLIED OR RUN.** The shared dev Postgres (`docker compose`,
containers `inafin-tenant-data-{postgres,pgbouncer,minio,kafka}-1`) went
down mid-session — `docker ps` hung for several minutes, then all four
containers showed `Exited (255)`. This was NOT caused by anything run this
session (no `docker compose down` was ever issued — CLAUDE.md's standing
rule against it was followed) and looks like Docker Desktop itself
restarting or crashing on the host. **Next session: confirm the containers
are back up (`docker ps`), then run `make migrate` for real** — `027`/`034`
have never been applied, `test_gstn_returns_gstr3b.py` has never been run,
and NEITHER has been verified against the live cluster the way `026`/`033`
were. Do not assume GSTR_3B is done the way GSTR_2B is — it is written and
statically clean, not proven.

---

## Current state (2026-08-14, eighth session)

**Item `00` from the seventh session's "Next session starts here" is
resolved.** The onboarding tables (shared migrations `026`-`028`, guessed
from table names only) are reconciled against `inafin-api`'s real write
contract in new shared migration
`032_platform_onboarding_contract_reconciliation.sql`. `inafin-api` supplied
the exact column list per table directly in this session (rather than the
withdrawn `migrations/platform/0002`-`0004` files being retrieved and read),
and `032` adopts it verbatim, table by table — see `API-CONTRACT.md`'s new
"Onboarding tables — reconciled" section for the column-level diff (renamed
columns, changed CHECK vocabularies, columns that moved from one table to
another, columns dropped).

**`onboarding_customer` was altered in place, not dropped** — confirmed via
`information_schema` that `platform_ref.tenant_customer` and every tenant
schema's `deboarding_case.customer_id` FK it directly, so a `DROP TABLE`
(even `CASCADE`) would have silently stripped a real constraint in every
tenant schema. Every other onboarding table, plus `hsn_master`/`sac_master`,
were confirmed (queried against the live cluster, not assumed) to hold zero
rows and were dropped and recreated cleanly, then re-granted table-for-table
to match what migration `030` already granted `app_login` — `030` itself
was not touched and needed no changes, since it grants by table name only.

**One deliberate, flagged deviation from the literal contract**:
`inafin-api`'s own hand-off note said `customer_document`'s
`document_id`/`doc_type_code`/`status` fields "cannot safely substitute" for
their document-workflow shape — their contract has no `doc_type_code` at
all, using free-text `document_name`/`category`/`requirement` instead. Rather
than silently dropping the Document Type Registry linkage the seventh
session's design specifically wanted ("the onboarding document picklist and
the ingestion registry are one list, not two"), `customer_document` and
`data_requirement` each keep `doc_type_code` as an **optional, nullable**
bridge column — not part of `inafin-api`'s contract, never required by it,
costs nothing sitting `NULL`. Flagged in both the migration file's header and
`API-CONTRACT.md`, not resolved by fiat.

**No vocabulary was invented for fields `inafin-api` didn't enumerate** —
`gst_registration.registration_type`/`registration_status` and
`data_requirement.category`/`priority`/`requirement_status` are free text,
no `CHECK`. Inventing a plausible-looking `CHECK` vocabulary is exactly what
made `026`-`028`'s first attempt wrong once already (`onboarding_customer
.status`, `gst_registration.status`); this session did not repeat it.

**Verified against the live (not reset) cluster**: `make migrate` (shared
`032` applied, zero drift across both tenants), `make lint`/`make typecheck`
clean, full suite 542 passed / 2 skipped / the same one pre-existing
`test_isolation.py::test_support_is_read_only` ordering failure from the
fourth session (item `0a`, untouched — not caused by this session). Grant
surface reverified directly against `information_schema.role_table_grants`
for `app_login`, not by inspection: the reconciled tables carry exactly the
same read-only-vs-read/write split migration `030` already declared.

**Not done this session, unchanged**: item `0a` (the pre-existing ordering
test bug) and every other "Next session starts here" item below except `00`.
No code outside `migrations/shared/032_...sql`, `API-CONTRACT.md`, and
`TODO.md` was touched.

---

## Current state (2026-08-14, seventh session)

**The `inafin-api` migration collision from the sixth session is resolved —
rebuilt, not restored.** The sixth session moved `inafin-api`'s files
(`017`-`022` shared, `021`-`024` tenant) to
`/tmp/inafin_api_migrations_holding/` after finding they broke a clean
cluster and referenced tables that exist nowhere. This session's
conversation with `inafin-api`'s owner (see the seventh-session chat log if
you need the full back-and-forth) settled every open question:

- Those files were always meant as **proposals for transfer into this repo**
  (`inafin-api`'s own `migrations/platform/0001`-`0004`), not an independent
  chain — `hsn_master`/`sac_master` were referenced but created nowhere and
  needed to be owned here.
- `022_api_admin_tenancy.sql`'s RLS/`tenant_id` design for the six admin
  tables is **withdrawn**, not reconciled. They now live in `{{gold}}` like
  every other tenant-owned table — isolated by schema/GRANT, not a row
  predicate.
- `platform_ref.tenant` and `app.tenant_registry` are the same concept;
  `app.tenant_registry` wins.
- **Ownership model, settled**: this repo owns all schema, migrations,
  grants, provisioning, and platform reference data. `inafin-api` owns API
  code only and consumes a published contract (`API-CONTRACT.md`, new this
  session) — it must never place executable migrations in this repo's
  directories again.
- The old `017`-`022`/`021`-`024` sequence is **formally withdrawn**.

**What replaced it**: shared migrations `024`-`030`, tenant migrations
`021`-`025`. `024_crypto_schema.sql` (pgcrypto, general-purpose now, not
connector-specific), `025_platform_identity_access.sql` (`api_principal`,
`role`, `permission`, `role_permission`, `principal_tenant_role` — the last
FK'd to `app.tenant_registry(tenant_id)`, not a recreated
`platform_ref.tenant`), `026_platform_onboarding_customer.sql`
(`onboarding_customer` and friends, plus `platform_ref.tenant_customer`, the
customer→tenant resolution table), `027_platform_onboarding_profile_and_
artifacts.sql` (`customer_profile`, `gst_registration`, `customer_document`,
`data_requirement` — `customer_document`/`data_requirement` key off the
existing `platform_ref.document_type` registry, so the onboarding document
picklist and the ingestion registry are one list, not two),
`028_platform_onboarding_state.sql` (`onboarding_state`),
`029_platform_catalog_hsn_sac.sql` (`hsn_master`/`sac_master`, DDL only,
**seeded empty** — no real code list exists in this workspace, stated
plainly rather than invented; see `TODO.md`), `030_platform_control_plane_
grants.sql` (the invariant-#2 exception, see above — `app.v1_tenant_
directory` view plus the exact `app_login` grant list, all in one file so
it stays auditable).

Tenant `021`-`024` (`connector_configuration`, `workspace_runtime`,
`datahub_upload_request` + idempotency) are unchanged from `inafin-api`'s
draft — already correctly `{{gold}}`-scoped. Tenant `025_admin_operational_
tables.sql` is the rebuilt six admin tables, RLS/`tenant_id` entirely
omitted; `deboarding_case.customer_id` FKs cross-schema to `platform_ref
.onboarding_customer`, the same pattern `entitlement_instrument` already
uses against `platform_ref.document_type`.

**Verified, not just written.** `make migrate` applied all 7 shared + 5
tenant migrations cleanly against the running (not reset) cluster, zero
drift; `make lint`/`make typecheck` clean; full suite 541 passed / 2
skipped / the same one pre-existing `test_isolation.py` ordering failure
from the sixth session (unrelated, not touched). Confirmed by direct SQL
query against `information_schema.role_table_grants` — not by inspection —
that `app_login` holds **zero** privileges on any `t_*_bronze/silver/gold`
object, and its full ambient grant list is *exactly* migration `030`'s
named tables, nothing more. New isolation test
`test_cross_tenant_admin_table_read_is_denied` proves `app.apply_tenant_
grants`'s generic gold-table sweep actually reached the new `admin_role`
table (no hand-written grant needed for it), not just that the mechanism
should work by construction. **A full `docker compose down -v` clean-cluster
verification was offered and explicitly declined this session** — the
above is real verification against the existing cluster, but nobody has
yet proven this sequence applies from empty. Do that before the next
session claims this done from a truly clean state.

**`inafin-api`'s own code changes are outstanding, in that repo, not this
one**: drop the RLS admin path and route it through `SET LOCAL ROLE
t_<slug>_recon`; migrate authorization queries off `platform_ref.tenant`
onto `app.v1_tenant_directory`; move admin SQL onto the new `{{gold}}`
tables. `API-CONTRACT.md` is written so that work has an exact list to
implement against.

**Later in the same session, three more things**, full detail in
`HANDOFF-2026-08-14-session7.md`:

1. **Standing rule, permanent**: this repo's Postgres is now a shared local
   dev server across `inafin-api`, `inafin-portal`, and this repo — never
   run `docker compose down -v` or any other full reset again. The
   Workflow section above is updated; verification from here on is
   targeted DDL plus `information_schema`/`pg_catalog` queries against the
   live cluster, never a wipe.
2. **`inafin-api` sent a real hand-off spec**
   (`inafin-api/docs/tenant-data-api-compatibility.sql` — a documented
   pattern going forward: a spec file in *their* repo, applied through
   *our* migration runner, never an executable migration of theirs). One
   real change in it: `platform_ref.api_principal` needed `issuer` for
   their OIDC (issuer, subject) principal lookup. Applied as shared
   migration `031_api_principal_issuer.sql` — additive, `issuer` nullable
   and unbackfilled on purpose, since no identity-provider source of truth
   exists in this workspace to backfill from yet. Verified: `make migrate`
   zero drift, full suite 542 passed / same one pre-existing failure,
   lint/typecheck clean.
3. **That same spec file surfaced a real problem this session's guesswork
   caused**: migrations `026`-`028` (`onboarding_customer` and friends)
   were designed from table names only — the user's message named which
   tables to rebuild but not their columns, since the actual withdrawn
   `inafin-api` files (`migrations/platform/0002`-`0004`) were never read.
   `inafin-api`'s spec says plainly: our shapes are "materially different"
   from their onboarding write contract, and NOT to patch this with ad hoc
   ALTERs. **Not resolved this session** — see "Next session starts here."

## Current state (2026-08-14, sixth session)

**Items #1-3 from the fifth session's "Next session starts here" are done:
the general Bronze→Silver dispatcher, `POST /artefacts/{id}/trigger` wired to
it synchronously, and a real OCR fallback.** `make lint`/`make typecheck`/
`make gates` all green; full suite 541 passed / 2 skipped (OCR, opt-in) / 1
pre-existing unrelated failure (see "Discovered this session" below) from a
clean `docker compose down -v` cluster running ONLY this repo's own migration
chain (see the `inafin-api` finding below for why "only this repo's own
chain" is a real caveat right now, not a formality).

**Dispatch routing is registry data, the same idiom as `field_contract`/
`extraction_spec`.** New column `platform_ref.document_type.dispatch_mechanism`
(shared migration `023_dispatch_mechanism.sql` — NOT `017`, see the numbering
collision below), one of `PDF_EXTRACTION | SALES_REGISTER | REGISTER_LOADER |
ARCHETYPE1_PROMOTE` or empty (not upload-triggered / no extractor built yet).
Backfilled in `registry/document_types.csv` by actually querying the code
each mechanism depends on (`SPEC_BY_DOC_TYPE`, `REGISTER_DOCUMENT_EXTRACTORS`,
`extraction_spec` presence, `_RETIRED_ARCHETYPE_1_TYPES`) rather than guessed
— counts self-check exactly: 50 `PDF_EXTRACTION` (== the 50 specimen PDFs),
29 `REGISTER_LOADER`, 5 `ARCHETYPE1_PROMOTE`, 1 `SALES_REGISTER`, 40 empty.
`scripts/gen_registry_seed.py`'s new `_check_dispatch_mechanism` cross-checks
every populated cell against that same code at generation time — a value that
doesn't actually resolve cannot be committed. **Adding a document type under
an EXISTING mechanism is now a CSV row, not a code change**, closing the loop
`extraction_spec` started. Adding a genuinely new mechanism still needs one
new branch in `src/dispatch/router.py`, once — unavoidable, but a one-time
cost per mechanism, not per document type.

**A real, latent bug found and fixed along the way**: `CREDIT_DEBIT_NOTE_REGISTER`
and `HO_COMMON_INPUT_SERVICE_INVOICE` are archetype 1 in the CSV AND already
have working `RegisterSpec` entries (`src/silver/registers/catalog.py`,
built in the second/third sessions) — meaning BOTH `promote_transaction_documents`
and `RegisterLoader` would have accepted either type before this session,
with nothing stopping a caller from double-writing through the wrong one.
`src/silver/promote.py`'s `_RETIRED_ARCHETYPE_1_TYPES` (previously just
`{"PURCHASE_REGISTER"}`) now also lists these two, so the archetype-1 path
refuses them defensively — `dispatch_mechanism=REGISTER_LOADER` for both is
the single source of truth now. No test exercised this collision before,
so nothing broke; it was just wrong.

**New module `src/dispatch/`**: `router.py`'s `dispatch_load(ctx, pool, *,
ingest_id, entity_id, doc_type_code, data, content_format, period_start=None,
period_end=None, gstin=None, store=None, reader=None, ...)` resolves
`dispatch_mechanism` and calls the right one of `run_extraction` /
`SalesRegisterLoader.load` / `RegisterLoader.load` / `promote_transaction_documents`,
returning a normalised `DispatchOutcome(mechanism, batch_id, written, status,
detail)` — `status` reuses `SilverReader.artefact_outcome`'s vocabulary
(`ACCEPTED`/`PARTIAL`/`QUARANTINED`). `content_format.py`'s
`infer_content_format` reuses `filecheck.py`'s (now-public) `extension_of`.
`BronzeIngestionService` gained `ledger_entry()` (metadata read-back —
`entity_id`, `original_filename` — alongside the existing `fetch()` for
bytes) since the trigger route needs both and `TriggerRequest` only ever
carried `doc_type_code`.

**`POST /artefacts/{id}/trigger` now actually dispatches**, synchronously,
in-process, after recording `load_trigger` as before. `TriggerRequest` gained
optional `period_start`/`period_end`/`gstin` (required only by mechanisms
that need them — PDF needs none); `TriggerResponse` gained a real `status`,
`mechanism`, `batch_id` instead of the constant `"recorded"`. This is a
**deliberate revision** of `src/api/app.py`'s prior docstring ("Bronze->Silver
promotion is deliberately NOT here and never will be") — re-read as HANDOFF-
2026-08-07.md's actual argument (never a SECOND remote HTTP hop for the
loader), calling `dispatch_load` in-process from the same request handler
does not violate it; the docstring was updated to say so explicitly. Tests:
`tests/conformance/test_api_ingest.py` now exercises all four mechanisms plus
`UNROUTED` (no mechanism) and a 422 for a missing required field, each
through a real `TestClient` HTTP call — not just the dispatcher directly.

**OCR fallback, built AND verified against real weights, not just the seam.**
`src/extraction/reader.py`: `PdfText` gained `source: Literal["native","ocr"]`;
new `FallbackPdfTextReader(primary, secondary=None)` implements `PdfTextPort`
itself — tries `primary`, falls through to `secondary` only if `primary` found
no native text, returns `has_native_text=False` only if BOTH fail. This
resolves `HANDOFF-2026-08-11.md`'s open "what does `NoTextLayer` mean once
OCR exists" question: it means "no native layer AND OCR unavailable-or-also-
failed", and nothing in `dispatch.py` had to change since it already accepted
a `reader:` parameter. `src/extraction/ocr.py`'s `PaddleOcrReader` is the real
secondary adapter — renders pages via `pypdfium2` (pure wheel, no system
Poppler), OCRs via `PaddleOCR`. Lazy-imports `paddleocr`/`pypdfium2` inside
`__init__`/`extract()`, so importing the module costs nothing if the optional
group isn't installed. New `pyproject.toml` `[project.optional-dependencies]
ocr` group (`paddleocr`, `paddlepaddle`, `pypdfium2`) — NOT core, so
`make ci`'s default install stays offline-safe; `Settings.ocr_enabled` (default
`False`) gates whether `app.py`'s lifespan constructs the real adapter at all.

**The verification the design notes said was missing, actually run**:
`tests/fixtures/ocr/lut_scanned_synthetic.pdf` — page 1 of the real LUT
specimen, rasterized via `pypdfium2` and re-saved as an image-only PDF (no
text layer, confirmed). `INAFIN_RUN_OCR_TESTS=1` (opt-in, since it downloads
real model weights — skipped by default, same reasoning a live ClamAV
integration test would need) ran `tests/conformance/test_ocr_reader.py`
against real `paddleocr==3.7.0`/`paddlepaddle==3.3.1`: **it recovered the
known LUT ARN, `AD2704240123456`, from the pixels alone**, `source == "ocr"`,
`has_native_text is True`. This is the first time this workspace's OCR design
has been checked against anything at all. `test_pdf_text_fallback.py` (always
runs, no real OCR) covers the composition rule itself against fakes.

**Discovered this session, neither caused by nor fixed by it — flagged, not
silently worked around:**

- **A migration numbering collision with `inafin-api`.** `migrations/shared/`
  and `migrations/tenant/` are shared, apparently by convention, with a
  separate repo (`inafin-api`, the backend for `inafin-portal`) — files
  `017_connector_crypto.sql` through `022_api_admin_tenancy.sql` (shared) and
  `021_connector_configuration.sql` through `024_datahub_upload_idempotency.sql`
  (tenant) are `inafin-api`'s, committed, and were NOT reflected anywhere in
  this file's history before today. My first attempt at this session's own
  migration collided at number `017`; it is now `023_dispatch_mechanism.sql`.
- **`inafin-api`'s migration chain does not apply to a clean cluster.**
  `018_platform_administration.sql` references `platform_ref.onboarding_customer`,
  which nothing in this git repo creates — `docker compose down -v` + migrate
  fails on it, confirmed with `inafin-api`'s files both present and (to
  isolate) temporarily removed. The working theory: the ORIGINAL local dev
  Postgres volume (before this session reset it) had that table from
  `inafin-api`'s own separate deploy process having run against the same
  shared database previously — state this repo's own tooling cannot
  reconstruct alone. **This repo's test suite provisions tenants by running
  its own migration runner against whatever is in `migrations/shared/`,
  so `inafin-api`'s files being present breaks every test run, not just a
  full reset.** For the remainder of this session, `inafin-api`'s 10 files
  were moved to `/tmp/inafin_api_migrations_holding/` (NOT deleted, NOT
  committed — a working-tree-only relocation) so this repo's own suite could
  run and be verified. **They are not restored as of this handoff** — restore
  them from that path, or wherever they're safely held, once `onboarding_customer`
  is available again (`inafin-api`'s own process, presumably), and coordinate
  with whoever owns that repo about why the two chains share a directory and
  a numbering space at all.
- **A pre-existing, unrelated, order-dependent test bug.** `tests/conformance/
  test_isolation.py::test_support_is_read_only` asserts `tenant_a`'s
  `transaction_document` has exactly 1 row — true only if
  `tests/conformance/test_extraction_transaction.py` (written in the fourth
  session) has not already run and written 2 more rows under the same shared
  `tenant_a.entity_id`. Confirmed via `git log` that both files predate this
  session; not touched. Fails whenever the full suite runs (alphabetical
  collection order puts `test_extraction_transaction.py` first), so this is
  not new, just newly visible from a clean cluster.

## Current state (2026-08-11, fifth session)

**Corrective refactor, requested after review of the fourth session's work.**
The repo owner looked at the 527-test extraction batch (below) and was
legitimately angry: `entitlement_types.py`, `entity_master_types.py`,
`proceeding_event_types.py`, `financial_statement_types.py` and half of
`transaction_types.py` hardcoded each document type's extraction rule (which
PDF label maps to which field, its type, the issuing authority) as a
`ClassVar`-only Python subclass — 36 of them, plus a hardcoded 11-type tuple
in `narrative_contract_types.py`. That is exactly the defect ARCHITECTURE.md
§6 exists to prevent, and exactly the problem this repo already solved once
for Archetype 1/2 via `platform_ref.document_type.field_contract`
(`src/silver/contract.py`). This session applied the identical fix to the
label:value extraction rule. **`make ci` is green from a clean cluster — 531
tests** (up from 527; +4: 3 new generation-time validation tests, 1 new
archetype-dispatch test — no test was deleted, the same 47-specimen
assertions still run, now against registry-loaded specs).

**New mechanism, mirroring `field_contract` exactly:**
- `src/extraction/spec.py` (new) — `ExtractionSpec`/`parse_extraction_spec`,
  the grammar (`authority=GSTN;fields=instrument_number:text!:"LUT ARN",...`),
  raising `SpecError` on anything malformed. Field types reuse
  `labelvalue.py`'s `FIELD_TYPES` vocabulary, not reinvented.
- `registry/document_types.csv` gets a new `extraction_spec` column,
  populated for the 47 previously-hardcoded types (24 entitlement + 2
  proceeding-event + 7 entity-master + 1 financial-statement + 11
  narrative-contract, empty `fields=` on purpose + 2 transaction) — a
  faithful transcription of what the deleted classes already declared, not
  new judgment calls about field mappings.
- `scripts/gen_registry_seed.py` — new `_write_extraction_spec`/
  `_check_extraction_specs`, emitting shared migration
  `016_extraction_spec.sql` (next free number), validated through
  `parse_extraction_spec` at generation time — same guarantee
  `field_contract`/migration `009` already has. Regenerating is
  byte-identical on a second run; **004/005/009 were NOT touched** —
  re-running the generator reproduced pre-existing, already-uncommitted CSV
  drift in those files (`PURCHASE_REGISTER`'s note text, `ITC_04_
  ACKNOWLEDGEMENT`'s archetype) from the fourth session's work; that drift
  was reverted (`git checkout`) before this session's diff was finalized —
  those three migrations are applied and pinned and this refactor does not
  touch them.
- `src/extraction/base.py` — the 7 archetype base classes' `__init__` now
  take `doc_type_code`/`label_spec`/`authority` (uniform across all 6
  label:value-shaped classes, including `TransactionDocumentExtractor`) as
  constructor arguments instead of subclass `ClassVar`s. `extract()`/
  `to_silver()` bodies are otherwise unchanged. `EntitlementExtractor`'s
  separate `instrument_type` ClassVar is gone — every sampled subclass set it
  identical to `doc_type_code`, confirmed before deleting, so `to_silver` now
  uses `self.doc_type_code` directly. `RegisterDocumentExtractor` (archetype
  2) is explicitly UNCHANGED — still `ClassVar`-driven, `# type: ignore[misc]`
  noting the deliberate exception where it overrides the ABC's now-instance
  `doc_type_code` attribute.
- `src/extraction/registry.py` — `EXTRACTOR_BY_DOC_TYPE` (import-time dict of
  classes) replaced by `build_extractor_registry(pool, ctx, *, store=None)`
  (runtime, returns constructed instances): `SELECT doc_type_code, archetype,
  extraction_spec FROM platform_ref.document_type WHERE in_scope AND
  archetype IN (1,3,4,6,7,8)` — same query shape, same connection idiom
  `src/silver/promote.py`'s `_load_spec` already uses for `field_contract`.
  Archetype 1's two PDF types dispatch through a small fixed dict
  (`transaction_types.TRANSACTION_DOCUMENT_EXTRACTOR_CLASS_BY_DOC_TYPE`) since
  `_csv_row` is real per-type business logic, not label:value config.
  Archetype 2 merges in `register_types.REGISTER_DOCUMENT_EXTRACTORS`
  unchanged — genuinely bespoke per-type table parsing, explicitly out of
  this refactor's scope (a payroll table and an FX table share no columns,
  so there is no grammar to move to data).
- `src/extraction/dispatch.py` — `run_extraction` now builds (or accepts a
  pre-built) registry via `build_extractor_registry` instead of importing the
  static dict.
- The 5 hardcoded files (`entitlement_types.py`, `entity_master_types.py`,
  `proceeding_event_types.py`, `financial_statement_types.py`,
  `narrative_contract_types.py`) are shrunk to module docstrings only — the
  real narrative content (why `SEZ_ANNUAL_PERFORMANCE_REPORT` has no clean
  label:value date, which entity-master types land as `Partial`, ...) is kept,
  not deleted, since it explains real tier-1 limitations on real specimens.
- `tests/conformance/test_extraction_*.py` no longer import concrete
  extractor classes — a new `extractor_registry` fixture
  (`tests/conftest.py`, wraps `build_extractor_registry` against a real
  seeded tenant) is what every test pulls an extractor from, so the tests
  exercise the actual runtime data path. New
  `tests/conformance/test_gen_registry_seed.py` (3 tests): a malformed
  `extraction_spec` cell is rejected at generation time, an out-of-scope row
  carrying one is rejected, and the real CSV still generates cleanly —
  mutation-checked live during this session (corrupted a real cell in a
  temp-path copy, confirmed generation refused and wrote nothing, confirmed
  the real CSV alone still passes).

**Acceptance test, run and confirmed, not just asserted**: with the app
stack live (`make reset`), a throwaway `UPDATE platform_ref.document_type SET
extraction_spec = ... WHERE doc_type_code = 'GST_PRACTITIONER_AUTHORISATION'`
(an archetype-3 row that ships with an empty `extraction_spec` cell — nobody
has sampled that PDF) made `build_extractor_registry` serve a working
`EntitlementExtractor` for it on the very next call, no process restart, and
it correctly extracted a synthetic specimen's `instrument_number`/
`valid_from`. Restoring the cell to `''` made it disappear from the registry
again. Zero Python files were touched at any point — this is the concrete
form of "adding the 40th entitlement type is a registry-row change." (The
throwaway test used for this was written, run, and then deleted — it was
never meant to be part of the committed suite.)

**Deviations from the plan, both narrow:**
- `authority` is a constructor parameter on all 5 label:value archetype base
  classes (`EntitlementExtractor` through `NarrativeContractExtractor`), not
  only 3/6 — kept uniform so `build_extractor_registry` can call every base
  class through one `Protocol`-typed dict entry (`_ArchetypeExtractorCtor` in
  `registry.py`) rather than special-casing which two classes accept it.
  Classes that don't use it (`4/7/8`) just ignore the argument.
- `RegisterDocumentExtractor.doc_type_code` needed one `# type: ignore[misc]`
  per leaf subclass (mypy: overriding an instance attribute with a
  `ClassVar`, since the ABC's `doc_type_code` is now typed as an instance
  attribute for the other six archetype bases) — noted inline as the
  deliberate archetype-2 exception, not silenced without explanation.

---

## Current state (2026-08-11, fourth session)

**`make ci` is green from a clean cluster (`docker compose down -v`) — 527
tests** (up from 422). Built the plan in `TYPED-TABLES-PLAN.md`'s sibling doc
for this session, `streamed-marinating-gray.md` ("A2–A7 extraction adapters —
Bronze→Silver, with MinIO Silver copies") — **all 8 phases complete**, not a
partial session. New package `src/extraction/`, 5 new tenant migrations
(`016`–`020`), 2 new shared migrations (`014`–`015`), 4 new Silver tables, 3
new `RegisterSpec` entries, and extractors for all 50 sampled document types
in `reference/A1-A7Documents/`.

**Phase 1 — the framework** (`src/extraction/`): `reader.py` (`PdfTextPort` +
`PypdfReader`, `pypdf` now a real `pyproject.toml` dependency, not the prior
session's throwaway install), `labelvalue.py` (the shared `Label: Value`
grammar — `Extracted(fields) | Partial(fields, missing, failed) | NoTextLayer`,
reusing `src/silver/contract.py`'s `ATTR_TYPES` vocabulary plus one addition,
`date_range`, for `"X to Y"` validity lines), `base.py` (`DocumentExtractor`
ABC + one `to_silver()` hook per archetype), `registry.py`
(`EXTRACTOR_BY_DOC_TYPE`), `dispatch.py` (`run_extraction`, not wired to any
HTTP route — see priority 1 below). `"pdf"` added to
`src/bronze/filecheck.py`'s `ALLOWED_EXTENSIONS`
(`test_disallowed_extension_is_rejected` now uses `.exe` as its negative
example instead).

**Phase 2 — four new tenant tables**, migrations `016`–`019`, each following
`006_entitlement_instrument.sql`'s idiom (composite FK pinning type to
archetype, `_vt`-pinned vocabulary, bitemporal supersession, `v1_` view):
`proceeding_event` (archetype 6), `entity_master_record` (archetype 7),
`financial_statement_extract` (archetype 4, every business column nullable
except `auditor`), `narrative_contract` (archetype 8, **every business
column nullable** — the specimens are prose, not label:value; a row with
empty `key_terms` is the correct successful outcome, not a failure). Shared
`014` seeds their vocabulary AND corrects `ITC_04_ACKNOWLEDGEMENT`'s registry
archetype 5→6 (a document-issued proceeding, not a Stream-A polled return —
`registry/document_types.csv`'s cell is updated to match but
`scripts/gen_registry_seed.py` must NOT be re-run over it; migration `004` is
applied and pinned). One service module per table
(`src/silver/proceeding_event.py`, `entity_master.py`, `financial_statement.py`,
`narrative_contract.py`), mirroring `entitlement.py`'s record/supersede shape.

**Phase 3 — the 24 Archetype 3 extractors** (`src/extraction/
entitlement_types.py`) — the registry's `archetype` column is authoritative
for the count, not the plan's rough prose estimate ("SEZ×6" was actually 5).
22 of 24 specimens extract cleanly; 2 land as a named, asserted `Partial`
(`SEZ_ANNUAL_PERFORMANCE_REPORT`, `CUSTOMS_DUTY_EXEMPTION_CERTIFICATE` — no
clean `Label: Value` date in either specimen). `EntitlementService.record`
called unchanged.

**Phase 4 — archetype 6/7/4/8** (`proceeding_event_types.py`,
`entity_master_types.py`, `financial_statement_types.py`,
`narrative_contract_types.py`): RBI_COMPOUNDING_ORDER + ITC_04_ACKNOWLEDGEMENT
(1 Extracted, 1 named Partial — the ITC-04 specimen is a per-quarter table,
not a header); 7 entity-master types (3 Extracted, 4 named Partial — several
specimens have a `reference_number` but no clean `as_of_date` label); the one
financial-statement specimen (Extracted, sparse — only `auditor` binds); all
11 narrative-contract types (all `Extracted(fields={})` by design — see
migration `019`'s header). Mutation-checked all four new tables' primary DDL
gate (the composite archetype FK for three of them, the CONTENT-key unique
index for `narrative_contract`) — each broke exactly the one test naming it,
restored clean.

**Phase 5 — archetype 1** (`transaction_types.py`): `GtaConsignmentNoteExtractor`
(A4.08) extracts cleanly and promotes a real `transaction_document` row
through `SilverPromotionService.promote_transaction_documents` UNCHANGED.
`BillOfEntryExtractor` (A6.01) legitimately lands as a named
`Partial(missing=("taxable_value", "igst_amount"))` — those two figures sit in
a colon-less "Particular / Amount" table pypdf flattens without a `Label:`
delimiter, the same accepted tier-1 limitation named throughout this batch.

**Phase 6 — archetype 2** (`register_types.py` + tenant migration `020` +
shared migration `015` + 3 new `RegisterSpec` entries in
`src/silver/registers/catalog.py`): `PAYROLL_TDS_REGISTER` (5 rows parsed out
of a genuinely reflowed pypdf table — row boundaries found via each row's own
stable token, e.g. `EMP0231`/`CONS0012`), `FIRC_BRC_REGISTER` (4 rows,
including the one with no FIRC number yet — `firc_no` is nullable by design,
not a parse failure), `FORM_15CA_15CB` (a **degenerate one-row register** —
its specimen is `Label: Value` shaped like archetypes 3/6/7, not a table, so
it reuses `parse_label_value` rather than a bespoke regex parser). One real
correction made mid-session: `payroll_tds_register.classification` was
originally `CHECK (... IN ('Employee','Contractor'))`, but the real specimen's
own column carries a third value, `'Professional'` — the CHECK was dropped
(free text, like `note_type` is a real fixed enum but this genuinely is not)
before the migration was ever left applied-and-pinned across a session
boundary; `docker compose down -v` + re-migrate picked up the correction
cleanly. `entity_gstin` for all three new register types is a fixed class
constant (`27AABCM4521F1Z5`) — a real deployment would resolve it from the
entity's registration record, which has no lookup in this codebase yet;
named as a simplification, not hidden. `test_register_specs.py`'s existing
DDL gate covers the 3 new specs automatically (it iterates `SPECS`), so no
new spec-vs-DDL test had to be hand-written.

**Phase 7 — MinIO Silver copy**: `ObjectStorePort.put_silver` +
`S3ObjectStore.put_silver` (`src/provisioning/objectstore.py`), key
`silver/{doc_type}/{date}/{ingest_id}.pdf`, same Object Lock/COMPLIANCE
retention as `put_bronze`. Wired as an optional `store: ObjectStorePort |
None = None` constructor parameter on every one of the 7 archetype base
classes in `src/extraction/base.py`, called via one shared `_put_silver_copy`
helper right after each successful (non-quarantined) write — best-effort,
same "log and swallow" pattern `promote.py`'s post-commit Kafka publish
already uses, not raised on a storage hiccup. `test_extraction_silver_copy.py`
verifies a real `silver/...` key lands in the dev MinIO cluster and is
byte-identical to the source PDF, alongside a `bronze/...` key for the same
tenant bucket.

**Phase 8 — verification**: `docker compose down -v` → re-migrate (shared
`001`–`015`, both tenants `001`–`020`) → `make ci` green, twice (once mid-session
after phase 6's DDL correction, once final after phase 7). 527 tests, 0
lint/mypy findings, isolation and static gates clean.

**Design deviations from `streamed-marinating-gray.md`, all noted in-line
where they matter:**
- The plan's "25 types" for archetype 3 is actually 24 per the registry's own
  `archetype` column — used the registry, not the plan's prose count.
- `ITC_04_ACKNOWLEDGEMENT` needed an actual registry correction (archetype
  5→6, shared migration `014`) to satisfy `proceeding_event`'s composite FK —
  the plan named this type as belonging there "pragmatically" but didn't
  specify how the archetype-5-in-the-CSV vs. archetype-6-in-the-table
  contradiction would be resolved.
- `entity_master_record`/`narrative_contract` business columns are more
  NULLABLE than migration `006`'s idiom strictly implies, because several
  real specimens genuinely have no clean second fact to bind — stated
  explicitly in each migration's header rather than forcing every column to
  mirror `entitlement_instrument`'s all-required shape.
- `narrative_contract` and `financial_statement_extract` use a CONTENT key
  (`bronze_ingest_id`) instead of a NATURAL key — no specimen in either
  archetype carries a document-level reference number the way an instrument's
  ARN or a proceeding's application number does.

**Not built, and explicitly out of scope per the plan**: OCR, a live LLM
escalation tier, an `extraction_audit` table, the general Bronze→Silver
dispatcher (`POST /artefacts/{id}/trigger` is still a stub — `run_extraction`
in `src/extraction/dispatch.py` is called directly by tests only, same as
`RegisterLoader`/`EntitlementService` always have been). See "Next session
starts here" below.

---

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

**THE NEXT SESSION CONTINUES CATEGORY B — FIRST STEP IS UNFINISHED BUSINESS,
NOT NEW WORK.** Read `HANDOFF-2026-08-19-categoryB.md` AND all four parts of
the tenth-session "Current state" entry above (the connector layer, the
manifest-publish/D1-decided section, "remaining design items closed, GSTR_2B
built end to end", and "GSTR_3B — code complete, UNVERIFIED against the
DB"), in that order, before anything below.

**0g. GSTR_3B (tenant `027`, shared `034`, `src/silver/gstn_returns/gstr3b.py`,
`tests/conformance/test_gstn_returns_gstr3b.py`) is WRITTEN, lint/mypy
clean, and has NEVER BEEN RUN.** The shared dev Postgres went down mid-session
(Docker Desktop issue on the host, not anything this session did — no
`docker compose down` was ever run) and did not come back before the session
ended. Do these, in order, before anything else:
  1. `docker ps` — confirm `inafin-tenant-data-{postgres,pgbouncer,minio,kafka}-1`
     are up. If not, that's a host-level Docker problem to resolve first
     (starting the existing compose stack back up is not the forbidden
     `docker compose down -v` — that rule is about wiping state, not about
     resuming a stopped stack).
  2. `make migrate` — applies `027`/`034` for the first time. Confirm zero
     drift.
  3. `pytest tests/conformance/test_gstn_returns_gstr3b.py -q` — these 12
     tests have never executed against a live database. If anything fails,
     fix it before treating GSTR_3B as done — nothing about this build has
     been proven yet, only written.
  4. Then `make lint`/`make typecheck`/full `pytest -q` as usual.

Once that's confirmed, do not re-propose `SourceConnectorPort`,
`LocalFixtureConnector`, any of the seven `adapters/*.py` stubs,
`factory.py`'s dispatch table, `registry_lookup.py`,
`scripts/stage_bronze_fixtures.py`, `src/dispatch/manifest.py`,
`BatchPublisherPort`, `_publish_if_ready`, `SilverWriteResult.batch_id`,
`gstr_2b`/`gstr_2b_line`/`gstr_2b_itc_summary` (tenant `026`),
`gstr_3b`/`gstr_3b_itc_detail`/`gstr_3b_inward_supply` (tenant `027`), the
`GSTN_JSON_PROMOTE` mechanism (shared `033`/`034`), or
`src/silver/gstn_returns/gstr2b.py`/`gstr3b.py`. **Do not re-propose a
shared `filed_return` header table across GSTR types — `TYPED-TABLES-PLAN.md`
§8 already rejects that shape; each return type gets its own table set, as
`gstr_2b`'s migration header explains.** What is still open:

0c. **D1/D2/D3 are all DECIDED. GSTR_2B and GSTR_3B are BUILT (2B verified,
    3B pending the DB check above); GSTR_1/9/9C are NOT.** Each needs its
    own migration (own table set, per §8), its own
    `src/silver/gstn_returns/<type>.py` parser (bespoke Python, not a spec —
    D3), and a `_GSTN_RETURN_LOADERS` entry in `src/dispatch/router.py`.
    `gstr_2b.py`/`gstr_3b.py` are the pattern to follow, the same role
    `sales_register.py`/tenant `008` played for the 23 A1 registers — but
    read each new type's real JSON shape first (GSTR_3B already proved
    GSTR-2B's shape doesn't generalize); GSTR-1 is both a grouped list AND a
    flat summary in the same payload (b2b/b2cl grouped, b2cs flat), and
    GSTR_9C's named reconciliation tables look like neither.
    `HANDOFF-2026-08-19-categoryB.md` itself is UNCHANGED and still says D1
    is open — its own text is stale on this point; trust this file's
    summary over it until someone updates it.
0d. **No orchestrator calls the new connectors yet.** `src/connectors/` can
    answer `fetch()` for any Category B ref in local-fixture mode today, but
    nothing iterates `(tenant, doc_type_code, gstin, period)` and actually
    calls `fetch()` then `BronzeIngestionService.receive()` then
    `dispatch_load()` — that loop is unbuilt. Confirmed shape (this
    session): a synchronous "pull now" function/CLI command first
    (upload-first, D2), a poller becomes a second caller of the same
    function later. Not started.
0e. **Live credentials for all seven adapters are unconfigured, as expected**
    — `Settings.source_connector_base_urls`/`_credential_refs` are empty by
    default. Wiring a real GSP/ICEGATE/DGFT/IRP contract is a future,
    separate task per source system, not blocking anything built so far.
0f. **The "cheap 14" flat CSV/JSON B-types (B2.02/03/06/07, B3.01-07,
    B6.02/03) still have no loader.** Recommendation from this session's
    discussion (typed shared-archetype-table loader — `entity_master_record`/
    `proceeding_event`/`entitlement_instrument`, not a new PDF-extraction
    path) was accepted in principle but explicitly deferred by Steve
    ("will see that later on") — not built, don't assume it's started.

The numbered backlog below is unchanged and still accurate, but is NOT the
next session's work unless Steve redirects.

**The dispatcher, trigger wiring, and OCR fallback are built as of 2026-08-14
(sixth session).** Read the "Current state (sixth session)" entry above
first — do not re-propose any part of `src/dispatch/`, the
`dispatch_mechanism` registry column, `POST /artefacts/{id}/trigger`'s real
dispatch, or `FallbackPdfTextReader`/`PaddleOcrReader`. **The load-bearing
rule going forward, mirroring `extraction_spec`'s: which of the four
mechanisms handles a `doc_type_code` is `platform_ref.document_type
.dispatch_mechanism` DATA — never a new if/elif branch keyed on `doc_type_code`.**
Adding the next document type under an existing mechanism is a CSV row.
`src/extraction/` itself (fifth session) is unchanged.

**The `inafin-api` migration situation from the sixth session is resolved
as of the seventh session** — read "Current state (seventh session)" above
first, including its late-session addendum. Do not re-propose restoring
`/tmp/inafin_api_migrations_holding/`; that sequence is formally withdrawn
and superseded by shared migrations `024`-`031` / tenant `021`-`025`.

**Read `HANDOFF-2026-08-14-session7.md` for the seventh session's context.
Item `00` it raised is DONE as of the eighth session** — see "Current state
(eighth session)" above and `API-CONTRACT.md`'s "Onboarding tables —
reconciled" section. Do not re-propose reconciling `026`-`028` against
`inafin-api`'s contract; migration `032` already did it, verbatim, from
column lists `inafin-api` supplied directly. The one thing still genuinely
open from that item: `customer_document`/`data_requirement`'s optional
`doc_type_code` bridge column was a unilateral call, not confirmed with
`inafin-api` — fine to leave as-is (it's nullable and costs nothing), but if
`inafin-api` ever says the bridge is unwanted, dropping it is a one-line
follow-up migration, not a redesign.

0. **Never run `docker compose down -v` or reset this Postgres, ever** —
   permanent standing rule as of the seventh session (this server is now
   shared with `inafin-api`/`inafin-portal`). The old item 0 here used to
   ask for a clean-cluster proof; that verification method no longer
   exists. Verify with targeted DDL and catalog queries against the live
   cluster only, as `024`-`031` already were.
0b. **DONE, in `inafin-api`'s own repo, confirmed by the user 2026-08-14.**
    `inafin-api` dropped its RLS admin path in favour of `SET LOCAL ROLE
    t_<slug>_recon`, migrated authorization queries off `platform_ref.tenant`
    onto `app.v1_tenant_directory`, and moved admin SQL onto the new
    `{{gold}}` tables — the exact list `API-CONTRACT.md` specified. This repo
    made no code changes for it (there was nothing on this side to do); this
    entry is a record that the boundary this repo published is now actually
    consumed. Do not re-flag this as outstanding.
0a. **DONE (2026-08-18, ninth session). The full suite is green for the
    first time since the fourth session — 543 passed / 2 skipped / 0
    failed.** `test_isolation.py::test_support_is_read_only`'s
    order-dependence is fixed, but **NOT the way the previous three
    handoffs said to fix it, because that diagnosis was wrong.** The
    prescribed fix (give `test_extraction_transaction.py`'s tests a fresh
    `uuid.uuid4()` entity_id) could never have worked: the assertion was
    `SELECT count(*) FROM transaction_document` with **no entity filter at
    all**, so it counts every row any test writes regardless of entity.
    `test_extraction_transaction.py` was also not the culprit — it already
    cleans its one write up in a `finally`. The actual writers were
    `test_transaction.py` and `test_api_ingest.py`, one legitimate row each
    (count was 1 seed + 2 = 3), and **both already used fresh entity_ids**
    exactly as prescribed. The fragile assertion was the bug, not the
    writers. Fixed by asserting the SEEDED ROW BY PRIMARY KEY
    (`WHERE doc_id = tenant_a.invoice_id`, expecting
    `COLLIDING_INVOICE_NUMBER`) instead of a table-wide count — both
    order-independent and strictly stronger, since it proves SUPPORT reads
    the row the seed actually wrote rather than merely that it can read
    *something*. No test outside `test_isolation.py` was touched.
    **Mutation-checked, and the first attempt at the check was itself
    misleading — worth knowing**: revoking `SELECT`/granting `DELETE` to
    `t_acme_support` directly in the database changed nothing, because the
    session-scoped `provisioned` fixture calls
    `ProvisioningService.provision` → `MigrationRunner` →
    `app.apply_tenant_grants`, which REVOKEs and re-GRANTs the whole matrix
    on every single test session. A live-DB grant mutation is therefore
    always undone before the first test runs. The real check needed the
    grant call in `src/migrate/runner.py:233` stubbed out *as well as* the
    `SELECT` revoked — with both, exactly one test failed
    (`test_support_is_read_only`), and restoring both returned the suite to
    543 passed. If you ever mutation-check a GRANT in this repo, mutate the
    re-assertion path too or you are testing nothing.

Priority order for what's actually open, next:

1. **A live LLM escalation tier.** `Partial` outcomes quarantine; they do not
   auto-retry through an LLM. Design agreed 2026-08-06 in `TODO.md`, never
   built, still stubbed-in-CI only per that design.
2. **`extraction_audit` / per-field provenance.** Deferred per
   `streamed-marinating-gray.md`'s explicit scope — `_quarantine`'s `reason`
   text is the only record of why a PDF extraction failed. Matters more now
   that OCR-sourced text exists (`PdfText.source == "ocr"`) and is more
   lossy than native text — this table is where that provenance would live,
   and where an OCR `Partial` escalating to the LLM tier (item 1) at a lower
   bar would be decided.
3. **The three known tier-1 gaps in the fifth session's extractors** — not
   bugs, but real coverage gaps a future session might want to close if
   better specimens arrive: `BillOfEntryExtractor` cannot bind
   `taxable_value`/`igst_amount` (colon-less amount table);
   `SEZ_ANNUAL_PERFORMANCE_REPORT`/`CUSTOMS_DUTY_EXEMPTION_CERTIFICATE` can't
   bind `valid_from`; 4 of 7 entity-master types can't bind `as_of_date`. All
   are asserted as named `Partial`s in the test suite, not silently accepted.
4. **v2 sign-off on the dropped `v1_purchase_invoice_line` view** (unchanged
   from the third session — see `TYPED-TABLES-PLAN.md` step 4).
5. **Real auth for the ingestion surface.** `src/api/auth.py`'s
   `StaticTokenAuth` is still a placeholder. Unchanged from prior sessions.
6. **`entitlement_instrument`** (tenant `006`, 33 HYBRID types) and **the 63
   HYBRID types generally** — unchanged from prior sessions.
7. **Excel adapter for the loader path.** Unchanged; CSV/NDJSON/PDF all work
   now.
8. **The six deploy blockers in `TODO.md`** (B1–B6). None have moved.
9. **A live ClamAV container for local dev.** Unchanged.
10. **A worker process consuming `load_trigger`, independent of the API
    request path.** `dispatch_load` (sixth session) is now called
    synchronously from `POST /trigger`; a worker that instead polls
    `load_trigger` and calls the same `dispatch_load` function would be an
    alternate caller, not new logic — useful if trigger latency ever needs
    to move off the request path, not currently blocking anything.
11. **A real GSTIN lookup for the trigger request.** `TriggerRequest.gstin`
    is still a caller-supplied field (sixth session) rather than resolved
    from the entity's own registration record, which has no lookup in this
    codebase yet — the same simplification the fourth session named for the
    three PDF-register extractors' `entity_gstin` class constant.

**The reference schema is `reference/inafin_a1_schema.sql`** (received
2026-08-07). It covers A1.01–A1.24 and is the column inventory the typed-tables
work was built against — still worth reading before adding any new A1-adjacent
table. §10 of `TYPED-TABLES-PLAN.md` records what changed against it and,
more importantly, **what our design keeps because the reference has no
substitute** (Bronze lineage, a real batch FK, bitemporality, the natural key,
schema-per-tenant isolation).

Column names for all A1 tables follow the reference (`qty`, `uom`, `cgst`, …).


### Extraction adapters — BUILT 2026-08-11 (fourth session)

Was deferred on input availability, not priority, since 2026-08-06 ("no LUT
or EPCG sample exists anywhere in the workspace"). The blocker was lifted the
same day the input arrived (third session, inspection only) and the tier-1
deterministic parser was built the same day (fourth session) —
`src/extraction/`, see the "Current state (2026-08-11, fourth session)"
section above for the full build. **Do not re-read this subsection as "not
yet built"** — it is, for all 50 specimens in `reference/A1-A7Documents/`.

What is still true from the original asymmetry argument, and still the
operating principle for whatever comes next: structured ERP data is the case
where the export schema is a contract *we* specify, so a synthetic fixture
has real fidelity — mock an ERP CSV freely, never mock a document layout with
no ground truth. Applied one level deeper, OCR is still unbuilt for exactly
this reason: all 50 specimens have native text layers, so a PaddleOCR
fallback tier would still be built against zero real fixtures if written now
— see "Next session starts here" item 3 and `HANDOFF-2026-08-11.md`'s open
questions on that tier (provenance weighting, what `NoTextLayer` means once
OCR exists). The LLM escalation tier (item 4) is the same story: designed
2026-08-06, still stubbed, still not live.

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



---

## Archived from `CLAUDE.md` on 2026-09-01 (fifteenth session)

Same reason as the first archival above: the per-session narrative sections
(eleventh through fourteenth) were making the file loaded into every
conversation too large. Nothing below is superseded by being archived — it
is the verbatim record of what was built, verified, and decided in those
four sessions. `CLAUDE.md` keeps only the durable rules and the current
open backlog; the "Built and working" table there is the up-to-date index.

### Fourteenth session (2026-08-24) — the manual local trigger (item #2 of 3)

**`tenantctl trigger` — a real CLI command, verified against the live
cluster, not just written.** `src/cli.py` gained `cmd_trigger` /
`_run_trigger`: `tenantctl trigger <slug> <ingest_id> <doc_type_code>
[--period-start] [--period-end] [--gstin]` dispatches one already-uploaded
artefact from a terminal — the actual "on local, manual trigger" the
three-item plan named. Manually verified end to end against the live
cluster: uploaded a real `PURCHASE_REGISTER` CSV, ran the CLI, watched it
write a real `ingest_batch` row and print `ACCEPTED (REGISTER_LOADER)
batch=<uuid>`, exit 0; a wrong `ingest_id` and a missing required field both
produce a clean one-line `ERROR:` and exit 1, no traceback.

**The route and the CLI cannot silently diverge, because they are now the
same function.** Before this session, `POST /artefacts/{id}/trigger`'s
entire "insert `load_trigger`, read the ledger, infer content format, fetch
bytes, call `dispatch_load`, fold `NoDispatchMechanismError`/
`ValidationRejected` into a recorded-not-dispatched outcome" sequence lived
only inside the route handler. Extracted to `src/dispatch/trigger.py`'s
`record_and_dispatch_trigger` — the route was refactored to call it (net
smaller: five now-unused imports dropped,
`MissingDispatchFieldError`/`UnknownExtractorError`/plain `ValueError` all
fold into one `except ValueError` since the first two are subclasses of the
third), and `cmd_trigger` calls the identical function. All 17 pre-existing
`test_api_ingest.py` trigger tests passed unchanged after the refactor —
proof the extraction changed nothing about the route's behavior.

**The module docstring states a real design rule, not just prose**: caller
mistakes (unknown `ingest_id`, a missing required field, an unrecognised
extension) RAISE, for each caller to map onto its own surface (HTTP
404/422, or a CLI exit code + stderr line); legitimate outcomes of a
well-formed trigger (`UNROUTED` — no loader built yet; `QUARANTINED` — the
document's own content failed validation) fold into
`TriggerOutcome.status`, never raise, because `load_trigger`'s row is real
and durable either way. Mutation-checked by swapping the fold for a
re-raise: failed exactly the 2 tests naming that split, plus the
pre-existing route test for the same case (`test_trigger_reports_unrouted_
for_a_type_with_no_dispatch_mechanism`) — 3 failures, all expected, nothing
else. Restored.

**New tests**: `tests/handoff/test_trigger_dispatch.py` (5 tests, calling
`record_and_dispatch_trigger` directly — the well-formed path, the two
RAISE shapes, the two FOLD shapes) and `tests/handoff/test_cli_trigger.py`
(4 tests, run as real subprocesses via `python -m src.cli trigger` against
the live cluster — success, unknown ingest_id, missing field, and a parity
test that the CLI's dispatch produces a real `ingest_batch` row queryable
the same way the HTTP route's would be).

**Scope decision, asked and answered**: the original #2 also named a
"scheduled job" for production use. Deliberately NOT built this session —
Steve confirmed stopping at the manual trigger. The design trap flagged when
#2 was first scoped still stands and is unchanged: `load_trigger` has no
status/claimed-at column BY DESIGN (Bronze is insert-only, invariant 6), so
a poller's "already processed" query has to be derived from whether a
matching `ingest_batch`/quarantine exists, never from a status flag added to
`load_trigger` itself. See backlog item 10 below — still where this picks
up.

**Verified**: `make lint`/`make typecheck` clean, full suite 602 passed / 2
skipped (up from 593). No migration — this session touched only Python
(`src/cli.py`, `src/dispatch/trigger.py`, `src/api/routes_ingest.py`).

### Thirteenth session (2026-08-24) — Bronze intake gaps (item #1 of 3)

Closed the three intake gaps flagged when the three-item plan (upload→Bronze;
trigger; schema/sample download — the last one is items #3, above) was first
scoped. #2 (trigger/scheduled job) is still open — this session did #1 only.

**A real gap, closed: nothing validated `document_type` before this
session.** `BronzeIngestionService.receive` defaulted `document_type` to
`"PURCHASE_INVOICE"` — one of the five retired Phase-1 codes that predate
`platform_ref.document_type` and are not even ROWS in it — and the only
guard was the ledger's FK against `platform_ref.universal_master`'s free
132-value vocabulary, not the 125-row in-scope registry. A tenant could
upload a file, get a 201, and the artefact would sit in Bronze forever,
undispatchable, discovered only much later if at all.

Fixed at the narrowest point that closes it for every caller, not just the
HTTP route: `document_type` lost its default (now a required keyword
argument) and `BronzeIngestionService.receive` gained
`_check_document_type_in_scope`, run right after `check_file` and before
hashing — a type that is not a row, or is a row with `in_scope = false`, is
refused with a distinct message for each case. A type that IS in-scope but
has no `dispatch_mechanism` yet (`GSTR_1` — Stream A, GSTN-API-polled, never
upload-triggered) is correctly ACCEPTED: this gate checks registry
membership, not dispatch readiness, and conflating the two would refuse
every one of the 38 UNSPECIFIED types the twelfth session's catalogue work
found.

Every existing caller that relied on the removed default was updated to
declare a real type explicitly (`tests/handoff/test_handoff.py`,
`test_outcome_gate.py`, `test_intake_gate.py` — matched to what each test's
payload actually represents, not defaulted to one code everywhere).

**Provenance**: the upload route now passes `received_from="PORTAL"`
explicitly rather than relying on the service's generic `"upload"` default —
worth doing now, before a second caller (a source connector pulling from
GSTN/ICEGATE, `src/connectors/`, still uncalled) starts writing through the
same service and this column becomes load-bearing for telling the two apart.

**`POST /artefacts/batch`, new.** A tenant drops several files of the SAME
`document_type` in one request; each file is processed independently through
the same `BronzeIngestionService.receive` — one bad file (wrong extension, an
unrecognised type) never fails the files around it. Always returns 200; the
body's `items` list carries the real per-file outcome, the same "status line
can't represent a mix, the body must" shape `StatusResponse.status`'s
PARTIAL/QUARANTINED vocabulary already uses one level down, at row grain.
**Deliberately not mixed-type**: `document_type` is one form field, not a
parallel array keyed to `files` — a parallel-array multipart shape is exactly
the kind of surface that silently misaligns (file 3 gets file 4's type) with
nothing but manual testing to catch it. A portal batching several document
types makes several calls, one per type. Bounded to `MAX_BATCH_FILES = 100`
so one request cannot hold a connection open indefinitely.

**Mutation-checked, three gates**: removing the document-type check call
failed the 3 targeted gate tests (plus 2 unrelated intake-gate tests that
share a literal payload byte-string with the now-unrejected upload — a real
but separate test fragility, not a production bug, left as-is since the
un-mutated suite is green); flipping the in-scope branch failed exactly
`test_an_out_of_scope_registry_row_is_refused`; removing
`received_from="PORTAL"` failed exactly the provenance test; removing the
batch route's per-file try/except failed exactly
`test_batch_upload_one_bad_file_does_not_fail_the_others`. All restored.

**Verified**: `make lint`/`make typecheck` clean, full suite 593 passed / 2
skipped (up from 582) — 4 new gate tests
(`tests/handoff/test_document_type_gate.py`) plus 6 new route-level tests
in `test_api_ingest.py` (unknown/missing type, provenance, 3 batch-route
tests). No migration needed — this session touched only Python.

**Not built this session**: nothing outstanding from the original three-gap
list — 1a (validation), 1b (multi-file), 1c (provenance) are all closed. #2
(trigger/scheduled job — a CLI dispatch command, auto-trigger on upload, and
the `load_trigger` worker-poller shape discussed but not built) is next.

### Twelfth session (2026-08-24) — the published schema catalogue

Built the whole of item #3 from the three-item plan (portal upload → Bronze;
trigger; **schema/sample download**). Steve's design, not the one originally
proposed: schema definitions live in `platform_ref`, the FILES live in MinIO
with the release in the object key, and a tenant is PINNED to the release they
downloaded at first upload so a later platform release cannot change the
contract under an integration in flight. `inafin-api` builds the read API on
top — it already has the grants, and `API-CONTRACT.md` now specifies it.

**The catalogue is derived, never written.** `src/catalogue/document_schema.py`
projects four existing sources of truth into one published shape and records
which one each row came from: `provenance` DERIVED (a `RegisterSpec` or
`sales_register.py`, both already gated against live DDL), DECLARED (a
`field_contract`/`extraction_spec` grammar cell, parsed by the same parser
ingestion uses), or PENDING (nothing published yet — an honest empty, so the
portal can say "sample only" instead of rendering a blank table). 125 types,
447 fields. `description` is nullable and deliberately unset: no column
comment exists anywhere in this schema, and writing 447 prose descriptions
from column names is the invented-content mistake this file already names once.

**A generator hazard was found by tripping over it, and is now guarded.**
Running `gen_registry_seed.py` silently rewrote applied, checksum-pinned
migrations `004` and `023` — both legitimately differ from a fresh generation
because later migrations (`014`, `033`) corrected them and the corrections were
written back into the CSV. The fifth session hit this too and caught it by eye.
`_emit` now refuses to rewrite any generated file whose content would change,
and `KNOWN_SEALED` names those two with their reasons, so a routine run stays
quiet about them while ANY other drift is a hard error.

**A REAL LATENT BUG, found by a gate rather than by reading — flag this to
`inafin-api`.** Migration `030`'s entire ambient `platform_ref` grant list —
every read AND the onboarding writes — **has never worked**. `app_login` held
the table privileges but was never granted `USAGE ON SCHEMA platform_ref`, so
every one of them was unreachable. True since 2026-08-14; fixed by shared
`038`. The seventh session's verification missed it because it queried
`information_schema.role_table_grants`, which correctly showed every GRANT as
present — **a recorded privilege is not a usable one**. `app.v1_tenant_
directory` worked throughout (schema `app` grants USAGE to PUBLIC), which is
exactly why tenant resolution appeared to function while everything after step
one did not. The gate that caught it opens a real bare `app_login` connection
and issues a real `SELECT`; that is the shape to copy.

**Other things worth knowing:**

- **`v1_schema_pin` was created and then withdrawn one migration later**
  (tenant `028` then `029`). `app.apply_tenant_grants` grants Bronze objects
  with `relkind = 'r'` — tables only; Silver has a second loop for `v1_` views,
  Bronze has none. A hand-written GRANT would not survive either, since step 1
  is `REVOKE ALL ON ALL TABLES` (views included), re-asserted every provision.
  Adding a Bronze view loop would change the isolation matrix for every tenant
  to serve one internal helper — not worth it. The DISTINCT ON now lives in
  `current_pin`, the single place callers already went through.
- **`ANOMALY_KEY.json` is never published.** It is the expected-results oracle
  for the Category B mock set (19 seeded anomalies); publishing it to the
  tenants whose data those anomalies exist to catch would hand over the answer
  sheet. `_EXCLUDED_FILENAMES` is the defence and
  `test_publish_schema_release.py` pins it — including a case that plants the
  oracle inside a ref folder, because the "it lives at the root" layout is an
  accident that a regenerated sample set could undo.
- **B4.03/B4.04 samples are correctly skipped** — they belong to
  `inafin-gst-corpus`, confirmed by the publisher reporting them as unmapped
  rather than silently dropping them.
- Steve chose to publish the reference samples as-is (193 files). They are
  synthetic, and the mock Category B set contains deliberate anomalies — that
  was a flagged, accepted call, not an oversight.

**Mutation-checked**: removing the `ensure_schema_pin` call failed exactly the
3 pin tests (the broken-store test correctly still passed); emptying
`_EXCLUDED_FILENAMES` failed exactly the 2 name-based oracle tests; corrupting
one published field name failed both catalogue gates independently; revoking
the `038` USAGE failed exactly the grant gate. All restored.

**Two test weaknesses found and fixed while writing these**: identical CSV
payloads meant "second upload" was being deduped at intake and never reached
the pinning code, so that test was passing for the wrong reason; and a test
asserting an UNSPECIFIED type pins nothing was wrong about the design, not the
code (all 125 in-scope types get a schema file — a PENDING one still tells a
tenant something useful).

**Still open on #3**: nothing built here serves the files over HTTP — that is
`inafin-api`'s half, specified in `API-CONTRACT.md`. Rolling a tenant forward
to a new release has no operator command yet (it is an INSERT into
`schema_pin`, deliberately). Field `description`s are unwritten and want a
human, not a generator.

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

### Mutation checking and the Archetype 2 decision, from earlier sessions

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


---

## Fifteenth session (2026-09-01) — the ERP upload E2E integration findings

An integration test suite run across `inafin-tenant-data`, `inafin-api`, and
`inafin-portal` surfaced 140 Bronze→Silver trigger errors across 70 document
types. Four findings, triaged and closed in order; `API-CONTRACT.md` was
updated as the changes landed since it is the boundary `inafin-api` reads.

**P0 #2 — DB integrity errors reached the caller as opaque HTTP 500s.**
`POST /artefacts/{id}/trigger` caught only `ForeignKeyViolation` and
`ValueError`; `CheckViolation`/`UniqueViolation`/`DataError` all escaped.
Fixed with two new error types (`src/core/errors.py`):
`UnknownArtefact` (no such artefact — scoped to exactly the `load_trigger`
INSERT's own FK, not the whole trigger call, which is a narrower and more
correct catch than what it replaced — a Silver-side FK violation used to be
misreported as "no artefact") and `SilverConstraintViolation` (carries
`kind` — CONFLICT for a unique violation, INVALID for everything else —
plus `constraint`/`column`/`table` off psycopg's `Diagnostic`). The route
now returns 404/409/422 instead of 404/422/500; `tenantctl trigger` prints
one clean `ERROR: KIND: message [constraint=...]` line instead of a
traceback. A mutation check on the FK-scoping caught a real gap the first
pass missed — nothing had ever tested that a Silver-layer FK violation
(as opposed to `load_trigger`'s own) does NOT get reported as 404.

**P0 #1 — the published schema was coarser than what Silver enforces.**
`document_type_field.data_type` only ever said "text" or "decimal" — never
that `supplier_gstin` is `platform_ref.gstin` (PAN-embedded regex),
`gst_rate` is bounded 0–100, `itc_eligibility` accepts four values, or that
`currency`/`dr_cr` are fixed-width. A client generating a file valid against
the downloaded schema could still be refused at ingestion. **Rejected the
literal fix** (widen `Kind` with `GSTIN`/`FIXED_WIDTH` members) because it
would hand-restate a rule the database already owns, the exact mistake
`registers/spec.py`'s docstring already argues against for the same reason.
Built instead: `src/catalogue/field_constraints.py` derives constraint
metadata straight from `pg_constraint`/`pg_type` — never hand-written.
Shared migration `041` adds eight columns to `document_type_field`
(`sql_domain`, `pattern`, `allowed_values`, `min_value`/`max_value`,
`max_length`, `numeric_precision`/`numeric_scale`) plus a new
`document_type_rule` table for constraints that touch more than one column
(one row exists today: `sales_register_line`'s IGST-positive-implies-
CGST/SGST-zero rule). `042` is the generated seed. `gen_field_constraints.py`
follows `gen_registry_seed.py`'s pattern (refuses to silently rewrite an
applied migration) but is a genuinely new shape in this repo: it needs a
LIVE cluster to derive from, since there is no offline rendering of
`pg_constraint`. `tests/conformance/test_field_constraints.py` re-derives
against live DDL and fails on drift, same shape as
`test_register_specs.py::test_spec_matches_the_table`. Coverage: 152
domain-backed fields, 17 patterns, 18 vocabularies, 14 bounded, 5
fixed-width, across the 33 DERIVED types. **A near-miss worth remembering**:
the first cut of the constraint parser silently dropped `tax_rate`'s 0–100
bounds — `(0)::numeric` normalises to `(0)`, parens and all, after cast
stripping, and the first numeric-literal regex didn't allow the parens. Only
caught by dumping every live constraint through the parser and reading the
unparsed list by eye, not by a test that was there from the start.

**P1 — PDF archetype natural-key collisions, initially misdiagnosed.** The
finding suspected the reference corpus reused PDFs across document types.
It does not: 50 specimens, 50 distinct SHA-256 hashes, exactly 1:1 with the
50 `PDF_EXTRACTION` types. The real cause: three of the five archetype
services (`entitlement`, `proceeding_event`, `entity_master`) already had a
correct `supersede()`, and all five tables already carried a `supersedes_*`
column and a partial `*_current_uq` index — the schema was designed for
this from the start. What no caller ever had was the LOOKUP: nothing
answered "which row is current for this document", so `src/extraction/
base.py` could only ever call `record()`, a blind INSERT, so the second
dispatch of ANY document had to violate the unique index. Fixed with one
shared helper, `src/silver/supersede.py`'s `close_current` (a `FOR UPDATE`
find-and-close against the index's own key columns, in the same transaction
as the insert), wired through a new `record_or_supersede` on all five
services, and `base.py`'s five call sites switched to it. Verified against
the live cluster: three dispatches of one artefact produced a correct
three-row chain (`fa8eb0 → 0736c9 → 1e63b6`, exactly one current, each
pointing at the one it replaced). A mutation check caught the sharpest
failure mode directly: closing the prior row WITHOUT setting
`supersedes_*` satisfies the unique index and looks fine, but silently
loses the bitemporal chain — the chain-shape assertion (not just "the
second call succeeded") is what catches that.

**P2 (pinned-schema metadata blank)** — `inafin-api`'s, not this repo's;
flagged back to them rather than fixed here.

**Step 4 — carrying the new constraint data to a client.** `_render_schema`
(`scripts/publish_schema_release.py`) only ever projected
`ordinal/field_name/scope/data_type/required/source_label`; migration 041's
columns would have reached no published file at all without this. Added a
per-field `constraints` object (omitted, not null, when a field carries
none — DECLARED types own no table and therefore carry none at all) and a
per-type `rules` array. Published and promoted `v2` — CURRENT as of this
session, `v1` kept (never deleted; a superseded release is not a rolled-back
one). **A genuinely new operator command**: `tenantctl reschema <slug>
[--doc-type CODE]`, backed by `src/catalogue/pin.py`'s new
`roll_forward`/`roll_forward_all`. This is the exact case migration `028`
made `schema_pin.pinned_by_ingest_id` nullable for ("NULL only for a
deliberate roll-forward"), and it had no caller until this session. Steve's
call, explicit and worth recording: no real clients exist anywhere yet, so
both dev tenants (`acme`, `globex`) were rolled forward unconditionally
rather than building any compatibility path — the right call given the
constraint metadata was simply never published before `v2`, so there was
nothing on `v1` worth protecting a tenant's continued read of.

**Mutation-checked throughout, same discipline as every prior session**: 2
mutations on the trigger error mapping (dropping the CONFLICT branch,
dropping the INVALID branch) plus one that found a real gap (the FK-scoping
test didn't exist until a mutation on it failed nothing); 4 mutations on the
constraint derivation/gate (corrupt a stored vocabulary, null a domain,
drop the envelope filter, plant an envelope rule — one of the four, planting
a rule with an already-published column, needed a purpose-built test that
didn't exist before this session); 3 on supersede (disable the lookup
entirely — 2 failures; close without linking — 1 failure, the sharp one; drop
`FOR UPDATE` — correctly 0 failures, since no serial test suite can provoke
a concurrent race, and this is documented rather than pretended away); 3 on
`roll_forward`/`roll_forward_all` (2 real regressions caught; removing the
Python-side idempotency check correctly found nothing, because
`schema_pin_release_uq` already guarantees the no-op at the DB layer — the
Python check only saves a network round-trip, not correctness).

**Verified**: `make lint`/`make typecheck` clean throughout, full suite
**630 passed / 2 skipped / 0 failed** (up from 602 at the end of the
fourteenth session), `make migrate` zero drift — all against the live
shared cluster, which was restored mid-session by an external DB issue and
re-verified rather than assumed. Two new shared migrations (`041`, `042`);
no tenant migration. `API-CONTRACT.md` updated: the new
`document_type_field` constraint columns and `document_type_rule`, the
`409`/`422`/`404` trigger contract (including the `404`→`422` behaviour
change for a Silver-side FK violation), and the `v2`/`tenantctl reschema`
section. Not yet done: giving `inafin-api`/`inafin-integration-tests`
explicit client-facing change instructions (next).
