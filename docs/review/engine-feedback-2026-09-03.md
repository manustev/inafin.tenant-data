# inafin-reconciliation-engine feedback — 2026-09-03

Feedback received after the Priority-0 + reader-contract handoff. Logged
here as a paper trail: both points are resolved or already correct, nothing
pending.

## 1. `v1_rcm_registration_history.effective_to` — acknowledged, matches our own caveat

Engine team: *"`v1_rcm_registration_history.effective_to` derives from
record supersession time, not a proven legal registration end date. The
engine will not treat it as transaction-date expiry."*

This is exactly the limitation documented in the view itself
(`migrations/tenant/036_rcm_reader_contracts.sql`,
`COMMENT ON VIEW {{silver}}.v1_rcm_registration_history`): `gstin_register_
entry` has no validity-window column of its own, so `effective_to` is
derived from `superseded_at` (when we recorded a replacement fact), not a
legally authoritative end date. No action needed — their handling is
correct and matches the documented contract.

## 2. Blocked on real Postgres persistence pending the CREATE decision — resolved

Engine team: *"We can start core adapters, plan execution, and SQLite/
in-memory tests now. Real PostgreSQL assessment persistence remains
intentionally blocked until the engine receives the separate CREATE/
migration decision for `t_<slug>_reconciliation`."*

Resolved same day: `t_<slug>_recon_engine` now holds `CREATE` on its own
`t_<slug>_reconciliation` schema (shared migration `061`,
`docs/adr/0001` updated). They can create and own their own tables there
now — no longer blocked.
