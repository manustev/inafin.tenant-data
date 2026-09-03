# 0002. Phase 1 RCM director-employment evidence uses the client's own director list only

**Status:** Accepted
**Date:** 2026-09-03
**Requested by:** `inafin-reconciliation-engine` design, via
`inafin-tenant-data-rcm-contract-request.md` (TD-RCM-002)

## Context

TD-RCM-002 asks for `v1_rcm_director_evidence` with `appointment_date`,
`cessation_date`, `employment_status`, `employment_status_basis`, plus
`board_resolution_reference`/`service_agreement_reference`.

Two sources exist for director facts in this repo:

- `director_list_din` (tenant migration `034`, built and verified) — one row
  per director, extracted from a client-supplied director list PDF:
  `name, din, designation, appointment_date, cessation_date,
  currently_active`. This reflects what the client's own document states,
  as of when that document was produced.
- `FORM_DIR_12` (registry `A3.02`) — the ROC filing that independently
  proves an appointment/cessation with a government filing reference. This
  type is registry-marked `CONDITIONAL, FORENSIC` and has no extractor.
  Forensic Mode — the broader chain-of-custody capability this and 7 other
  in-scope types depend on — is deferred product-wide (`CLAUDE.md`,
  "Decisions already made"), not deferred specifically for RCM.

Building `FORM_DIR_12` support now would mean picking Forensic Mode back up
for one field, ahead of its own standing deferral. Steve decided to proceed
without it for Phase 1, on the understanding that the client's own list is
sufficient for "does our record show this person was a director around this
transaction date" — the SME will still be asked to confirm this framing
in parallel (see `dir12-sme-question.md`), but Phase 1 work is not blocked
on that answer.

## Decision

`v1_rcm_director_evidence` is built from `director_list_din` (appointment/
cessation/name/din/designation) joined to `narrative_contract` for
`BOARD_RESOLUTION_DIRECTOR_APPOINTMENT`/`DIRECTOR_SERVICE_AGREEMENT` rows as
an **evidence locator only** (a reference to where the document lives, not
an extracted `board_resolution_reference` field — `narrative_contract`
extracts no structured fields from these two types by design, see
`src/extraction/narrative_contract_types.py`). `employment_status_basis`
reports which of these evidence rows exist for the director, not a
government-filing-backed determination.

`FORM_DIR_12` is not built. `employment_status`/`evidence_status` for a
director with no corroborating `FORM_DIR_12` reflects "per client-supplied
list" — it does not and cannot claim ROC-filing-backed certainty.

## Consequences

- TD-RCM-002 is unblocked for Phase 1 without new extraction work — it is a
  join view over two already-built, already-verified tables.
- The engine's Phase 1 director-employment determination carries the same
  evidentiary weight as its source: a client-supplied list, not an
  independently verifiable government record. This should be visible in
  the engine's own output (e.g. an evidence-basis field), not silently
  presented as equivalent to a DIR-12-backed fact.
- If the SME later says DIR-12-backed proof is required, that is new work
  gated on Forensic Mode generally (`CLAUDE.md` backlog item 6/Forensic
  Mode deferral) — it supersedes this ADR, not amends it.
