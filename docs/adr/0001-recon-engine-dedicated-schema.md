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
