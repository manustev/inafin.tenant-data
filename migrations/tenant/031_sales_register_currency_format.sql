-- =============================================================================
-- Tenant migration 031 — `sales_register.currency` gains a real format check.
--
-- THE GAP, found by the ERP upload E2E suite (2026-09-01) alongside the
-- `invoice_type`/`supply_type` vocabulary gap this session's other fix
-- closes: the published schema catalogue told a client `currency` was a
-- 3-character field but named no rule for WHAT those three characters could
-- be, because none existed to derive — `currency char(3) NOT NULL DEFAULT
-- 'INR'` (tenant migration 009) constrains only the column's WIDTH.
--
-- WHY A REGEX, NOT A universal_master VOCABULARY. `invoice_type` and
-- `supply_type` got a real vocabulary constraint (shared migration 011/012)
-- because someone decided what the finite, closed set of values is. Nobody
-- has made that decision for currency, and there is no specimen in this repo
-- naming which currencies a sales register may carry — inventing a list here
-- would be exactly the mistake `SESSION-HISTORY.md`'s "never invent a
-- vocabulary with no specimen" rule already names (GSTR-2B's `cdnr`, GSTR-3B's
-- `inter_sup`). ISO 4217's shape (three uppercase letters), unlike its
-- membership, is not a business decision — it is what `char(3)` already
-- implied and a lowercase or numeral-bearing value was never going to be a
-- valid currency code regardless of which ones this tenant is allowed to use.
-- =============================================================================

ALTER TABLE {{silver}}.sales_register
    ADD CONSTRAINT sales_register_currency_format CHECK (currency ~ '^[A-Z]{3}$');
