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

## 4. Still open — requested fixtures not yet built

Their request also asked: *"publish fixtures covering: open registration,
legal cancellation, late correction, and a correction that does not change
registration validity."* Acme's real data already demonstrates the first
two (four `Active` GSTINs, one `Suspended` with a date). The other two —
a same-GSTIN correction that changes the legal end date, and one that
doesn't — are not yet seeded on either tenant. Not done as of this note.
