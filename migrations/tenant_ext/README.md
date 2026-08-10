# `migrations/tenant_ext/` — per-tenant column extensions

One subdirectory per tenant slug. Files here are applied **only** to that tenant,
after the whole common chain in `migrations/tenant/`.

```
migrations/tenant_ext/
  acme/
    ext_001_sales_register_cost_centre.sql
```

This directory is the mechanism the typed-table redesign rests on
(`TYPED-TABLES-PLAN.md` §7). Table-per-type exists because a tenant needs to
carry additional columns on a register, and that is impossible on a table shared
by 11 or 33 document types across every tenant. Here it is a local change.

## Rules

1. **Filenames are `ext_NNN_description.sql`.** Enforced by
   `src/migrate/runner.py::_load_ext`, which refuses the whole chain otherwise.
   Both chains are recorded in one `__migration_version` table keyed by
   filename, so an extension named `003_gold.sql` would collide with the common
   chain: the runner would treat the real `003` as already applied for that
   tenant, and then report a checksum mismatch on a file nobody edited.

2. **Checksum-pinned, exactly like the common chain.** Never edit an applied
   extension — add `ext_002_`. `MigrationRunner._verify_checksum` does not care
   which chain a file came from.

3. **The common chain runs first, always.** An extension adds columns to a base
   table, so the base table must exist. A tenant three migrations behind catches
   up before its own extensions are re-evaluated.

4. **Drift is per tenant.** `drift_report()` compares each slug against
   `common + that slug's extensions`. A tenant with an unapplied extension is
   drift, and is reported as such.

5. **Extension columns stay below the `v1_` view line.** The views expose the
   common core only. Otherwise `inafinplatform/v2` sees a different shape per
   tenant and the view stops being a contract (`TYPED-TABLES-PLAN.md` §7).
   Definer rights as always — never `security_invoker = true`.

6. **Add columns; do not reshape.** An extension that drops a common column, or
   redefines one, puts that tenant's base table out of sync with the template
   and every later common migration becomes a coin flip. If a tenant genuinely
   cannot live with the common shape, that is a change to the common shape.

## What was rejected

An `ext jsonb` column on every table, holding bespoke fields. It reintroduces
the untyped bag this redesign exists to remove — no CHECKs, no domains, no typed
indexes, no planner statistics. Worth revisiting only if extensions turn out to
be numerous and short-lived, which is not what the customer described.
