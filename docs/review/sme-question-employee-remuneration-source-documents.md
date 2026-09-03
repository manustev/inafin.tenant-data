# SME question — source documents for employee-relationship / director-remuneration evidence

**Related:** `inafin-reconciliation-engine`'s proposed `v1_rcm_employee_
relationship_evidence` and `v1_rcm_director_remuneration_evidence`
contracts.
**Status:** Blocked. No schema or extraction work should start until this
is answered — building against a guessed document shape is exactly the
"invented vocabulary" mistake this repo has avoided every other time
(GSTR_2B's `cdnr`, GSTR_3B's `inter_sup`).

## What we have today

`payroll_tds_register` (already fully exposed via `v1_rcm_payroll_tds_
evidence`) is the only employment-adjacent source in this repo: a
period-based payroll/TDS export with `person_name`, `role_title`,
`classification` (Employee/Contractor/Professional), `gross_amount`,
`tds_deducted`, one row per pay period per person. It has no stable person
identifier across periods, no `gl_code`, no `invoice_or_journal_reference`,
no `posting_date`.

`trial_balance` is a fiscal-year GL-level aggregate, not transaction-line.
`bank_statement_outward` has per-transaction `txn_date`/`amount`/
`invoice_ref`, but nothing that keys a specific payment to a specific
payroll row.

## What's being asked for, and the two questions that need real answers

1. **Employee master** — `v1_rcm_employee_relationship_evidence` wants a
   stable `person_key`/`employee_id` with `employment_relationship`/
   `employment_status` HISTORY (`effective_from`/`effective_to`,
   `superseded_at`). **Which real document would a client provide that
   carries this?** An HR system export? A headcount/personnel master
   register? Please name the actual document and its real column layout.

2. **Remuneration transaction evidence** — `v1_rcm_director_remuneration_
   evidence` wants `gl_code`, `posting_date`, `payment_date`,
   `invoice_or_journal_reference`, and `payroll_record_id` all on one row,
   at transaction-line grain. **Which real document combines these?** A
   payroll disbursement journal? A GL extract filtered to remuneration
   accounts? Note: we will NOT derive this by matching `payroll_tds_
   register` against `bank_statement_outward` on amount/name — that is
   exactly the kind of guessed linkage the engine's own request says it
   does not want us making on its behalf.

## What we need to actually build this, once the source is named

Same standing rule as everywhere else in this repo: a column list is not
enough. Please also provide **at least one real specimen** (or a real
sample export) showing these fields actually populated, so the extractor/
loader is built against ground truth, not a plausible-sounding guess.
