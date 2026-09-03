# 0001. `inafin-reconciliation-engine` gets its own tenant schema, not a Gold table set

**Status:** Accepted
**Date:** 2026-09-03
**Requested by:** `inafin-reconciliation-engine` design, via
`inafin-tenant-data-rcm-contract-request.md` (Priority 0)

## Context

`inafin-reconciliation-engine` is a new, standalone consumer of this repo's
Silver layer — processing RCM, ITC, FCM, and ADT reconciliation. It is a
distinct codebase from `inafinplatform/v2`, which this repo's own
`ARCHITECTURE.md` already designed a consumer role for (`t_<slug>_recon`,
full DML on every table in `t_<slug>_gold`, `SELECT` on nothing but the
Silver `v1_` views — see `ARCHITECTURE.md` §5). `v2` is being treated as
legacy; some of its modules are being carried into the new engine, but it is
not the same running service.

The request doc's Priority 0 asked for a dedicated `t_<slug>_reconciliation`
schema and role. The first read of this looked like a duplicate of
`t_<slug>_gold`/`t_<slug>_recon` — until checking how `apply_tenant_grants`
(`migrations/shared/001_app.sql`) actually grants Gold: it is a **blanket
sweep**, every table in `{{gold}}` to whichever role holds `t_<slug>_recon`,
with no per-table scoping. That means two independent engines sharing that
one role would each get DML on the other's tables — `inafinplatform/v2`'s
`fact_record`/`batch_execution`/`contest_records` and the new engine's
output tables would be mutually readable/writable. That directly
contradicts the request doc's own acceptance criterion ("the role cannot
access... unrelated Gold tables") and this repo's least-privilege posture.

Decided with Steve (2026-09-03): rather than extend `apply_tenant_grants`'s
Gold sweep to be table-scoped (which would have worked, but adds complexity
to a foundational shared migration for a boundary that gains nothing once
the engine has its own schema), give the engine a genuinely separate schema.
Steve's stated reasoning: in production this engine could reasonably live on
a separate database or server entirely, so it should not be structurally
tied to `t_<slug>_gold`'s lifecycle now.

## Decision

- Every tenant gets a new schema, `t_<slug>_reconciliation`, alongside
  `t_<slug>_bronze`/`silver`/`gold`.
- A new role, **`t_<slug>_recon_engine`**, gets:
  - `SELECT` on the tenant's approved Silver `v1_` views — same mechanism
    `t_<slug>_recon` already uses (definer-rights views, no base-table
    access, ever).
  - Full rights inside `t_<slug>_reconciliation` only. No access to Bronze,
    Silver base tables, Gold, `platform_ref`, or any other tenant's schema.
- Table DDL *inside* `t_<slug>_reconciliation` is the engine team's own to
  design and manage — this repo does not dictate its shape, the way it does
  not dictate Gold's shape for `inafinplatform/v2` either.
- Anything the engine needs from Silver or Gold (a new `v1_` view, a new
  Gold table) is still a reviewed contract change through this repo, per
  the existing `v1_` view discipline (`migrations/tenant/004_views.sql`) —
  this decision does not change that boundary, only where the engine's own
  output lives.
- **`GRANT CREATE ON SCHEMA t_<slug>_reconciliation TO t_<slug>_recon_engine`
  — reversed 2026-09-03, shared migration `061`.** Originally deliberately
  withheld pending a separate production-readiness decision. Steve decided
  the same day, once the engine team's own feedback made clear they were
  blocked on it for adapter/persistence work, to grant it now rather than
  gate early integration on a later milestone. `t_<slug>_recon_engine` now
  holds `CREATE` on its own schema — table DDL inside `t_<slug>_reconciliation`
  is fully the engine team's own from here on, including whatever migration
  tooling they run against it. This repo does not govern that DDL's shape,
  the same way it does not govern `inafinplatform/v2`'s Gold table shapes.

## Follow-up: apply_tenant_grants stopped managing table-level privileges inside the schema

Shared migration `062`, same day. The moment the engine exercised the
`CREATE` grant from `061` and created its own tables (`analysis_run`,
`evidence_record`, `assessment`, ... — all owned by `t_<slug>_recon_engine`,
not `tenant_migrate`), `apply_tenant_grants`'s blanket `REVOKE ALL ON ALL
TABLES IN SCHEMA t_<slug>_reconciliation` broke `make migrate` outright:
Postgres requires object ownership (or `GRANT OPTION`) to `REVOKE`/`GRANT`
on a table, and `tenant_migrate` has neither on a table it did not create.

This was foreseeable in hindsight — "the engine owns table DDL inside its
own schema" and "tenant-data's grant function keeps self-healing every
table in that schema" are in direct tension the moment the first table
actually gets created. `062` resolves it by having `apply_tenant_grants`
only ever touch the reconciliation schema at the SCHEMA level (`USAGE`,
`CREATE` — controlled by schema ownership, which `tenant_migrate` does
hold) and never enumerate or grant/revoke individual tables inside it.
Table-level privileges inside `t_<slug>_reconciliation` are entirely a
function of Postgres ownership from here — created-by-`recon_engine` means
owned-by-`recon_engine` means full privileges already, with nothing for
this repo to assert or revoke.

## Follow-up: the first deliberate Gold exception

Shared migration `063` + tenant migration `038`, same day. A generic
tenant-wide settings/preferences table (`{{gold}}.tenant_setting` — not
reconciliation-specific; portal, API, and recon consumers alike) needed to
be readable by `recon_engine`. This repo's stated rule was flat: "no access
to Bronze, Silver base tables, Gold, or any other tenant." Two real
questions had to be settled before deciding this was still safe:

1. **Does Gold living on a separate database server from Silver make a
   cross-schema view fragile?** No — Bronze/Silver/Gold are provisioned
   together, in one function, over one connection
   (`provision_tenant_schemas`), and `inafin-api`/portal already writes to
   Gold via `SET LOCAL ROLE t_<slug>_recon` today, which only works because
   Gold is reachable from the same Postgres connection as everything else.
   The separate-database risk is real but specific to the RECONCILIATION
   schema (which the engine team themselves flagged as possibly landing on
   its own server) — it does not extend backward onto Gold.
2. **Does exposing one Gold view reopen Gold generally?** No, if done as a
   named allowlist rather than a blanket grant — the same allowlist
   philosophy `060` already used for Silver's `v1_rcm_%` views, applied to
   Gold: `apply_tenant_grants` now grants `recon_engine` `SELECT` on any
   view matching `v1_reconciliation_%`, and nothing else in Gold.
   `inafinplatform/v2`'s actual business tables (`fact_record`,
   `workspace_*`, `gst_document`, ...) remain completely unreachable.

`recon_engine` also gained `USAGE` on the Gold schema itself (needed to
reach the view at all) and a matching `SELECT` on Gold's
`__schema_identity` (same reasoning `001_app.sql` gives for every other
schema a role holds `USAGE` on — without it, a legitimate read hits a bare
permission-denied instead of the boundary guard's legible error).
`scripts/check_isolation.py` was extended and mutation-checked for this
exact allowlist (excluding the one approved view from the pattern match
was caught, then reverted).

## Follow-up: a second Gold exception under the same allowlist

Tenant migration `039`, 2026-09-04. The engine team asked for a governed
GL-code-to-REF-02-category bridge (`v1_rcm_category_bridge` in their
request) — client-configured mapping data, not extracted-document
evidence, same shape as `tenant_setting`. Built as `{{gold}}.
gl_category_bridge` + `{{gold}}.v1_reconciliation_category_bridge`,
following `038`'s precedent exactly. No new shared migration was needed:
`063`'s allowlist already grants `recon_engine` `SELECT` on any Gold view
matching `v1_reconciliation_%`, and this view's name already matches —
confirming the allowlist is genuinely self-extending, the same property
`060`'s `v1_rcm_%` Silver allowlist has.

One design bug found and fixed before any real data went in: the first cut
of the table's uniqueness constraint scoped only on `(gl_code, match_mode,
entity_id, gstin)`, which rejects two legitimate `TOKEN_SET`/`EXACT_PHRASE`
rules on the same `gl_code` with different narration patterns — exactly the
"incompatible multi-category match" fixture the engine asked for. Fixed by
adding `normalized_narration_pattern` into the unique index before the
first fixture insert. Caught during fixture-seeding, not in production,
because the fixture set deliberately included the ambiguous case.

## Consequences

- `inafin-reconciliation-engine` is isolated from `inafinplatform/v2`/
  `inafin-api`'s Gold tables from day one — no shared-role blast radius to
  reason about.
- This repo does not need to touch `apply_tenant_grants`'s Gold sweep at
  all for this work — Gold's existing behavior (one schema, one writer
  role, full sweep) is unchanged and still correct for its current single
  consumer.
- **First precedent in this repo for a tenant-scoped role holding `CREATE`
  on its own schema** — every other role only ever gets `USAGE` plus
  object-level grants, because tenant-data owns all other DDL. When this is
  eventually granted, it should get its own ADR at that time (this one
  records the schema/role/no-CREATE-yet decision only, not the eventual
  CREATE grant).
- Provisioning `t_<slug>_reconciliation` + `t_<slug>_recon_engine` (schema,
  role, `v1_` view grants, no `CREATE`) is now unblocked implementation
  work. The engine's own table DDL is blocked until the `CREATE` grant is
  deliberately added, closer to production.
