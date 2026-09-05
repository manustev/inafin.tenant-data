# 0003. REF-02 notified RCM category is a platform-wide reference contract, not a tenant one

**Status:** Accepted
**Date:** 2026-09-05
**Requested by:** `inafin-reconciliation-engine`, "DEV E2E Fixture Request" /
PRIM-07: `platform_ref.v1_ref02_notified_rcm_category`

## Context

The request asked for `category_code`, `applicability_test`,
`conditions_json`, etc. — the notified-RCM-category vocabulary from GST law
(CGST Act s.9(3)/9(4) and its amending notifications). Two things made this
different from every other `inafin-reconciliation-engine` contract to date
(`docs/adr/0001`'s Silver `v1_rcm_%` views, its Gold `v1_reconciliation_%`
follow-ups):

1. **No `entity_id`/`gstin` column anywhere in the requested shape.** Every
   prior contract — Silver evidence views, `tenant_setting`, `gl_category_
   bridge` — is scoped per tenant or per client, because it is either
   extracted from a specific client's documents or configured by a specific
   client's admin. Which supplies are notified under RCM is a fact of GST
   law, identical for every tenant. Building this as a per-tenant Gold table
   (the `038`/`039` pattern) would have been structurally wrong, not just
   unnecessary — it would imply Acme and Globex could have different RCM
   law, which they can't.
2. **Contract rule 3** ("one category_code resolves to at most one APPROVED
   effective row for a date — overlaps are invalid configuration") is a
   genuine temporal non-overlap requirement. `tenant_setting`/`gl_category_
   bridge` only ever enforced "one CURRENT row per scope" via a plain unique
   index on `WHERE superseded_at IS NULL` — that does not stop two
   `APPROVED`, not-yet-superseded rows for the same key from having
   overlapping `effective_from`/`effective_to` windows if both are inserted
   directly (as opposed to superseding one another). This repo has never
   needed a real interval-overlap constraint before.

Checked before deciding: `platform_ref` already holds exactly this kind of
platform-wide reference master data — `hsn_master`/`sac_master` (shared
migration `029`), seeded EMPTY because "no real HSN/SAC code list exists
anywhere in this workspace to seed from, stated plainly rather than
invented." Checked `inafin-gst-corpus` (the other GST reference corpus in
this workspace) for a citable RCM/reverse-charge notification text — none
exists there either. `platform_ref_reader` (shared migration `001`) is
already a role every tenant role is a member of, `t_<slug>_recon_engine`
included (migration `060`), so no new role was needed to satisfy "grant
SELECT only to the reconciliation platform-reference reader role."

## Decision

- `platform_ref.ref02_notified_rcm_category` (shared migration `064`) is a
  platform-wide table, not tenant-templated — same category rows are
  visible to every tenant's `recon_engine` (and every other tenant role,
  same as `hsn_master`/`universal_master`/`document_type` today —
  `platform_ref_reader` membership is not RCM-specific).
- **Seeded EMPTY**, same posture and same reasoning as `hsn_master`/
  `sac_master`: this is real regulatory content with real financial
  consequences if wrong, and this repo has no citable notification text
  checked in anywhere. Fabricating a plausible `threshold_amount` or
  `trigger_rate_percent` is worse than an empty table for compliance data —
  unlike `tenant_setting`/`gl_category_bridge`'s config fixtures, an invented
  RCM rate cannot be labeled "this is just test data" without risk of it
  being read as real guidance later. Populating this table for real is a
  follow-up task gated on the engine team (or a domain SME) supplying an
  actual notification citation, not a code change.
- **`EXCLUDE USING gist (category_code WITH =, daterange(...) WITH &&) WHERE
  (approval_state = 'APPROVED' AND superseded_at IS NULL)`** enforces
  contract rule 3 at the database level. First use of `btree_gist`/`EXCLUDE`
  in this repo — installed via `CREATE EXTENSION IF NOT EXISTS btree_gist`,
  which `tenant_migrate` can run without superuser (a PG13+ "trusted"
  extension), the same way `pgcrypto` (migration `024`) was installed.
  Mutation-tested directly: two non-overlapping `APPROVED` windows for one
  `category_code` insert cleanly; an overlapping third is rejected by
  Postgres with `ExclusionViolation`.
- Access is granted **only through `platform_ref.v1_ref02_notified_rcm_
  category`**, never the base table — a deliberate exception to
  `platform_ref`'s own existing norm (`universal_master`, `hsn_master`,
  `document_type` are all granted as base tables directly). Honored because
  the request explicitly asked for the same view-only lineage discipline
  every Silver `v1_` contract already has, and there was no reason to refuse
  it just because `platform_ref` hadn't required it before this. Full
  history exposed (no "currently effective" pre-filter) — resolving
  `APPROVED` + effective-for-a-date is the engine's own job, same principle
  as `tenant_setting`/`gl_category_bridge`.
- `scripts/check_isolation.py` gained two new rules scoped to this one
  table: no tenant role may write it (mirrors the existing
  `platform-ref-read-only` check on `universal_master`), and no tenant role
  may hold direct `SELECT` on the base table (`ref02-category-view-only`) —
  mutation-tested by granting `SELECT` on the base table to
  `t_acme_recon_engine` directly and confirming the gate flags it, then
  reverting.

## Consequences

- This is the first `inafin-reconciliation-engine` contract that is
  genuinely tenant-agnostic — reviewers should not expect an
  `entity_id`/`gstin` column here the way every other RCM contract has one,
  and should not expect Acme/Globex to ever see different rows from this
  view for the same `category_code`.
- The engine's own request asked for "Acme/Globex fixtures" including an
  "overlapping effective rows (negative test)" — answered with
  clearly-synthetic `TEST_`-prefixed category codes proving the constraint
  mechanism, not real RCM category content (there is no real content to
  fixture yet — see "seeded EMPTY" above). Real fixtures are blocked on a
  real citation, the same way `docs/review/sme-question-td-rcm-006-foreign-
  payment-evidence.md` blocks TD-RCM-006 on a real specimen.
- `btree_gist` is now a standing dependency of this cluster's `platform_ref`
  schema. Any future EXCLUDE-constraint need elsewhere in this repo can
  reuse it rather than re-deciding whether to introduce it.
- What would revisit this: if the engine team can supply an actual
  notification citation (a real CGST notification number and text), the
  next step is a data-only migration/seed script populating real category
  rows — no schema change implied by that.
