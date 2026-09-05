# inafin-reconciliation-engine feedback — 2026-09-03

Feedback received after the Priority-0 + reader-contract handoff. Logged
here as a paper trail: both points are resolved or already correct, nothing
pending.

## 1. `v1_rcm_registration_history.effective_to` — first response was wrong; fixed

Engine team, first message: *"`effective_to` derives from record
supersession time, not a proven legal registration end date. The engine
will not treat it as transaction-date expiry."* — treated at the time as an
acknowledged, correct limitation. **That was wrong.** Their follow-up gave
the concrete failure mode: a registration cancelled 31-Jul but corrected
into Silver on 15-Sep would show `effective_to = 15-Sep` (the correction
date), so an invoice dated 20-Aug would read as "registration still valid"
when it legally was not as of 31-Jul. `superseded_at` is a system/audit
timestamp (when *we* corrected the record), not a business-validity end
date — conflating the two is a real defect, not a documented limitation.

**Fixed in tenant migration `037`, deployed to both Acme and Globex**:
`effective_to` is now parsed from `gstin_register_entry.status`'s own
stated date (e.g. `'Cancelled (31-Jul-2025)'` → `2025-07-31`) — a fact the
source document already carried, just unstructured, not something derived
from ingest timing. `registration_status` is cleaned to the bare state word
(`Active`/`Suspended`/`Cancelled`) as a consequence, so the date isn't
exposed twice in two shapes. Verified against Acme's real data: the
Haryana GSTIN (`06AABCM4521F1ZM`, status `'Suspended (28-Feb-2025)'`) now
reports `effective_to = 2025-02-28` instead of a supersession timestamp.

`recipient_is_business_entity` stays `NULL`, per their own instruction
("if unavailable, retain NULL; do not default it") — no source in this data
model states it, and this repo won't encode a tax-law derivation rule
without SME sign-off. Open question, not silently resolved.

## 2. Blocked on real Postgres persistence pending the CREATE decision — resolved

Engine team: *"We can start core adapters, plan execution, and SQLite/
in-memory tests now. Real PostgreSQL assessment persistence remains
intentionally blocked until the engine receives the separate CREATE/
migration decision for `t_<slug>_reconciliation`."*

Resolved same day: `t_<slug>_recon_engine` now holds `CREATE` on its own
`t_<slug>_reconciliation` schema (shared migration `061`,
`docs/adr/0001` updated). They can create and own their own tables there
now — no longer blocked.

## 3. Side effect of using the CREATE grant — noticed, fixed, no action needed on their end

The engine team's own migration tooling has already run against the shared
cluster — `engine_schema_migration`, `analysis_run`,
`evaluation_context_snapshot`, `evaluation_plan_snapshot`,
`capability_execution`, `evidence_record`, `assessment`, `outbox_event` all
exist in both `t_acme_reconciliation` and `t_globex_reconciliation`, owned
by their respective `recon_engine` roles. This briefly broke
`apply_tenant_grants` (a `tenant_migrate`-owned function tried to
`REVOKE`/`GRANT` on tables it does not own) and blocked `make migrate` on
the shared cluster for about ten minutes. Fixed in shared migration `062` —
tenant-data's grant function now only ever manages the reconciliation
schema at the schema level, never enumerates tables inside it. **Their
tables were never touched, dropped, or altered** — this was a tenant-data
function failing to run, not their data being at risk. No action needed on
their side; flagging so they know the earlier failed migration wasn't
caused by anything they did.

## 4. Tenant settings contract — built (`TD-RCM-NEW-001`, informal)

Engine team asked for `v1_reconciliation_tenant_setting` (tenant-wide
default + entity/GSTIN override, typed value, unit, version, approval
state, effective dates, source reference — first key:
`rcm.director_remuneration.amount_match_tolerance`). Built as a **generic**
table, not reconciliation-scoped — `{{gold}}.tenant_setting`, usable by
portal/API preferences too, per Steve's explicit direction. This required
the first deliberate exception to `docs/adr/0001`'s "recon_engine touches
nothing in Gold" — see that ADR's follow-up section for the full reasoning
(why Gold, not a cross-schema fragility risk the way the reconciliation
schema itself is; why a named allowlist, not a blanket Gold opening).
Exposes full row-level history (not pre-filtered to "currently effective")
— which row applies for a given transaction date is the engine's own
resolution, consistent with the "expose facts, let the deterministic
engine explain the linkage" principle their own remuneration request
(below) states explicitly.

## 5. Employee-relationship / director-remuneration evidence — cannot be built as specified

Engine team proposed two views (`v1_rcm_employee_relationship_evidence`,
`v1_rcm_director_remuneration_evidence`) assuming this repo has separate
employee-master, journal/GL, and AP-payment source data to join. Checked
against the full registry: **no such document types exist.** The only
employment-adjacent source is `payroll_tds_register` (already fully
consumed by the existing `v1_rcm_payroll_tds_evidence`) — a period-based
payroll/TDS export with no stable person identifier across periods and no
`gl_code`/`invoice_or_journal_reference`/`posting_date` at all.
`trial_balance` is a fiscal-year aggregate, not transaction-line;
`bank_statement_outward` has per-transaction dates/amounts but nothing
that keys it to a payroll row.

**Needed before any schema work starts**: which real document carries an
employee master (stable person/employee id, employment relationship,
status history), and which real document carries remuneration transaction
evidence with `gl_code` + `posting_date` + `invoice_or_journal_reference`
together (a payroll disbursement journal? a GL extract?). Not resolved as
of this note — a `docs/review/` SME question for this should be filed
before the two views are attempted, same discipline as the DIR-12/
TD-RCM-006 questions.

## 6. Still open — requested fixtures not yet built

Their request also asked: *"publish fixtures covering: open registration,
legal cancellation, late correction, and a correction that does not change
registration validity."* Acme's real data already demonstrates the first
two (four `Active` GSTINs, one `Suspended` with a date). The other two —
a same-GSTIN correction that changes the legal end date, and one that
doesn't — are not yet seeded on either tenant. Not done as of this note.

## 7. GL-to-REF-02 category bridge — built (`TD-RCM-NEW-002`, informal)

Engine team asked for `v1_rcm_category_bridge` (governed GL-code-to-
REF-02-category mapping, tenant/entity/GSTIN scope, `GL_EXACT`/
`EXACT_PHRASE`/`TOKEN_SET` match modes, full history, priority-based
precedence resolved by the engine). Built as `v1_reconciliation_
category_bridge` (tenant migration `039`) — same Gold-hosted, generic-
config shape as `tenant_setting` (`docs/adr/0001`'s follow-up), since this
is client-configured mapping data, not something extracted from an
ingested document. No new grant migration was needed — it lands under the
same `v1_reconciliation_%` allowlist `063` already grants.

Five fixture scenarios seeded on Acme, as requested:
- **Exact match** — tenant-wide `4010-FREIGHT` → `REF02_FREIGHT_INWARD`
  (`GL_EXACT`).
- **No match** — `9999-MISC` is deliberately left unmapped (no row; a
  missing mapping is represented by absence, not a sentinel row).
- **Entity/GSTIN override** — the same `4010-FREIGHT` GL code, overridden
  for GSTIN `06AABCM4521F1ZM` to `REF02_FREIGHT_EXPORT` at higher priority.
- **Expired mapping** — `5020-COMMISSION` → `REF02_COMMISSION_OLD`,
  `effective_to = 2026-06-30`.
- **Incompatible multi-category match** — `6030-BANK-CHARGES` carries two
  currently-active `TOKEN_SET` rules (`'bank charges'` →
  `REF02_BANK_CHARGES`, `'forex conversion'` → `REF02_FOREX_CONVERSION`)
  that could both plausibly match one narration — resolving that ambiguity
  is the engine's own precedence logic, not something this repo
  pre-resolves.

Not yet done: Globex fixtures (schema/view exist there, no rows seeded —
say if you need them).

## 8. PRIM-07 — `v1_ref02_notified_rcm_category` — schema/grants built, real content blocked

Full review: `docs/adr/0003`. Short version:

- **Built**: `platform_ref.ref02_notified_rcm_category` +
  `platform_ref.v1_ref02_notified_rcm_category` (shared migration `064`).
  Platform-wide, not per-tenant — there's no `entity_id`/`gstin` in your
  requested shape, and RCM law doesn't vary by tenant, so this isn't a
  per-tenant Gold table like `tenant_setting`/`gl_category_bridge`.
  `t_acme_recon_engine` and `t_globex_recon_engine` both already see it
  (verified) — that's correct, not a leak, since `platform_ref_reader`
  membership already covers every tenant role.
- **Contract rule 3 (no overlapping APPROVED windows) is enforced at the DB
  level**, not just documented — a Postgres `EXCLUDE` constraint, mutation-
  tested by actually inserting an overlapping row and confirming Postgres
  rejects it.
- **Contract rule 5 (view-only, base table never granted) is enforced and
  gated** — a base-table grant would now fail an automated isolation check,
  not just a review.
- **Not done: real category content.** Checked this workspace end to end —
  no citable RCM/reverse-charge notification text exists here to transcribe
  from (same gap `hsn_master`/`sac_master` already had and is still open
  about). We will not fabricate a `threshold_amount` or
  `trigger_rate_percent` that looks plausible — this is compliance data with
  a real consequence if wrong, not client config. **Please supply an actual
  notification citation** (a real CGST notification number + text, or a
  pointer to where your team already has one) and we'll seed real rows —
  that's a data-only follow-up, no schema change needed.
- **Test fixtures for the negative case (overlap) were built using a
  clearly-synthetic `TEST_`-prefixed category code**, proving the
  constraint mechanism works — not standing in for real RCM category data.
  The "approved effective / future / superseded" positive fixtures are not
  seeded for the same reason: there is no real category to seed yet.
