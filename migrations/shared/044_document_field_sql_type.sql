-- =============================================================================
-- Shared migration 044 — the published schema names the column's BASE type,
-- not only its constraints.
--
-- THE GAP, found by the ERP upload E2E suite (2026-09-01) reviewing migration
-- 041/042's constraint columns against `SALES_REGISTER`: `line_number` and
-- `qty` publish `data_type = 'text'` (the coarse `Kind` a CSV cell parses as
-- — `document_schema.py`'s own documented reason, unchanged by this
-- migration) and, before this column existed, carried no OTHER signal that
-- `line_number` is actually a Postgres `integer` or that `qty` is actually a
-- `numeric(18,3)` — a client reading `data_type` alone had no way to learn
-- either fact, only `numeric_precision`/`numeric_scale` values that imply it
-- without saying it.
--
-- `sql_type` is the explicit answer: `format_type()` on the base type behind
-- the column (through its domain, same as `sql_domain` already resolves
-- through), derived by `src/catalogue/field_constraints.py` alongside the
-- other constraint columns, never hand-written. Seeded by migration 045,
-- which also carries the `invoice_type`/`supply_type` vocabulary correction
-- (see that file's header) — both are outputs of the SAME derivation run,
-- against the SAME live DDL, so one seed migration carries both rather than
-- two migrations racing to update the same rows.
-- =============================================================================

ALTER TABLE platform_ref.document_type_field
    ADD COLUMN sql_type text;

COMMENT ON COLUMN platform_ref.document_type_field.sql_type IS
    'The base Postgres type behind this column (through its domain), e.g. '
    '"integer", "numeric", "text", "date". Derived from pg_type by '
    'src/catalogue/field_constraints.py — never hand-written. NULL means no '
    'column exists for this field (see the module docstring), not "unknown".';
