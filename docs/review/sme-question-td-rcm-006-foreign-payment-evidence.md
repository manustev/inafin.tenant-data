# SME question — TD-RCM-006 foreign-payment evidence fields

**Related:** `inafin-tenant-data-rcm-contract-request.md` (TD-RCM-006)
**Status:** Blocked on this answer. `v1_foreign_currency_payment_register`
(existing) already covers candidate detection and is unaffected either way.

## What we have today

`foreign_currency_payment_register` is the client's own flat export:
`payment_date, amount_fcy, currency, amount_inr, vendor, country, purpose,
invoice_contract_ref`. `invoice_contract_ref` already covers
TD-RCM-006's `invoice_or_contract_reference` — no question needed there.

## What's being asked for, and what we need to know about each

1. **`stable_counterparty_key`** — today `vendor`/`country` are free text,
   with no stable identifier. What should make this key stable — a GSTIN, a
   PAN, a SWIFT BIC, a client-maintained vendor code? Is this a new column
   the client's own export would carry, or does it need to be resolved by
   matching against some other list we don't currently model (a vendor
   master)?

2. **`place_of_supply_fact` / `place_of_supply_basis`** — we don't see this
   as a natural fact on a payment register in any specimen we have. Which
   actual document carries it — is it on the register itself, or does it
   come from the underlying invoice/contract? Naming the source document
   decides whether this is a small additive column or a new extractor.

3. **`consideration_status`** (paid/outstanding) — this looks derivable by
   joining the payment register against the purchase register (an invoice
   with no matching payment is outstanding). Do you want us to publish it as
   a computed column, or is the engine expected to compute it itself from
   the purchase-register and payment-register contracts it already gets
   (TD-RCM-004 and the existing foreign-currency payment register)?

## What we need to actually build this, once the above is answered

A column list alone isn't enough. Per this repo's standing rule (already
applied twice, to GSTR_2B's `cdnr` and GSTR_3B's `inter_sup`): a
closed-vocabulary field is never given invented accepted values from a
written description alone. `place_of_supply_basis` and `consideration_status`
both look like enums. **Please also provide at least one real specimen file
or row with these fields actually populated** — schema plus specimen, not
schema alone — before we build an extractor or loader against them.
